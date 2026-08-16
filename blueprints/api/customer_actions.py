"""
blueprints/api/customer_actions.py
==================================

The customer-detail actions from templates/customers/view.html, exposed as
REST so the React screen can do everything the Jinja page could.

These existed only as form-post routes in app.py. Rather than reimplement the
rules, each endpoint calls the *same* helpers app.py uses - network sync,
SMS/email, audit logging - so behaviour cannot drift between the two UIs.
Those helpers are imported inside the functions: app.py imports this
blueprint at start-up, so a module-level import would be circular.

Every route here is admin-only, matching the @admin_required on the original
Jinja routes. These change money and service state; staff should not.
"""
import re
import secrets
from datetime import date, datetime, timedelta

from flask import Blueprint

from models import Customer, CustomerPlan, Invoice, Plan, db
from services.plans import close_active_plans, current_plan

from .serializers import customer_dict, customer_plan_dict, invoice_dict
from .utils import admin_required, body, fail, ok

bp = Blueprint('api_customer_actions', __name__)

MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')


def _customer_or_404(cid):
    customer = db.session.get(Customer, cid)
    return customer, (None if customer else fail('not_found', 404))


def _audit(action, detail):
    """Audit logging is best-effort: never fail a real action over a log."""
    try:
        from app import log_audit
        log_audit(action, detail)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Status: enable / disable / terminate
# --------------------------------------------------------------------------- #
@bp.post('/customers/<int:cid>/enable')
@admin_required
def customer_enable(cid):
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    customer.is_active = True
    db.session.commit()

    network_ok = True
    try:
        from app import enable_connection_on_network
        enable_connection_on_network(customer)
    except Exception:
        # The DB change stands; the caller is told the network sync failed so
        # they can retry rather than assume the line is live.
        network_ok = False

    _audit('Enable Customer', f"Enabled customer {customer.full_name}")
    return ok({'customer': customer_dict(customer), 'network_synced': network_ok})


@bp.post('/customers/<int:cid>/disable')
@admin_required
def customer_disable(cid):
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    customer.is_active = False
    db.session.commit()

    network_ok = True
    try:
        from app import disable_connection_on_network
        disable_connection_on_network(customer)
    except Exception:
        network_ok = False

    _audit('Disable Customer', f"Disabled customer {customer.full_name}")
    return ok({'customer': customer_dict(customer), 'network_synced': network_ok})


@bp.post('/customers/<int:cid>/terminate')
@admin_required
def customer_terminate(cid):
    """Deactivate the customer and terminate their plan.

    Every open row, not one of them: closing the service has to leave nothing
    behind that the billing run would still treat as live.
    """
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    customer.is_active = False
    terminated = close_active_plans(cid)
    db.session.commit()

    network_ok = True
    try:
        from app import disable_connection_on_network
        disable_connection_on_network(customer)
    except Exception:
        network_ok = False

    _audit('Terminate Customer', f"Terminated customer {customer.full_name}")
    return ok({
        'customer': customer_dict(customer),
        'terminated_plan_id': terminated[0].id if terminated else None,
        'terminated_plan_ids': [cp.id for cp in terminated],
        'network_synced': network_ok,
    })


# --------------------------------------------------------------------------- #
#  Credentials: portal password, MAC
# --------------------------------------------------------------------------- #
@bp.post('/customers/<int:cid>/reset-password')
@admin_required
def customer_reset_password(cid):
    """Generate a temporary portal password and deliver it to the customer.

    The password is returned once so the operator can read it out over the
    phone if the SMS does not arrive. It is not stored in plain text.
    """
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    temp_password = secrets.token_urlsafe(8)
    customer.set_password(temp_password)
    db.session.commit()

    delivered = {'sms': False, 'email': False, 'network': False}

    try:
        from app import reset_customer_password_on_log2space
        reset_customer_password_on_log2space(customer, temp_password)
        delivered['network'] = True
    except Exception:
        pass

    if customer.mobile:
        try:
            from app import send_sms
            send_sms(customer.mobile,
                     'Your portal password has been reset. '
                     f'Temporary password: {temp_password}')
            delivered['sms'] = True
        except Exception:
            pass

    if customer.email:
        try:
            from app import send_email
            send_email(customer.email, 'Password Reset',
                       f'Your new temporary password is: {temp_password}')
            delivered['email'] = True
        except Exception:
            pass

    _audit('Reset Customer Password',
           f"Reset portal password for {customer.full_name}")
    return ok({'temporary_password': temp_password, 'delivered': delivered})


@bp.post('/customers/<int:cid>/reset-mac')
@admin_required
def customer_reset_mac(cid):
    """Push a new MAC to the provider. Deliberately not stored locally."""
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    mac = (body().get('mac_address') or '').strip()
    if not mac:
        return fail('mac_address_required', 400)
    if not MAC_RE.match(mac):
        return fail('invalid_mac_format', 400,
                    detail='Use XX:XX:XX:XX:XX:XX')

    try:
        from app import reset_mac_on_log2space
        succeeded = bool(reset_mac_on_log2space(mac, customer.reference_id))
    except Exception as exc:
        return fail('network_error', 424, detail=str(exc)[:200])

    if not succeeded:
        return fail('mac_reset_failed', 424,
                    detail='The provider rejected the MAC reset.')

    _audit('Reset MAC', f"Reset MAC to {mac} for customer {customer.full_name}")
    return ok({'mac_address': mac, 'status': 'reset'})


# --------------------------------------------------------------------------- #
#  Notes
# --------------------------------------------------------------------------- #
@bp.get('/customers/<int:cid>/note')
@admin_required
def customer_note_get(cid):
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing
    return ok({'note': customer.notes or ''})


@bp.put('/customers/<int:cid>/note')
@admin_required
def customer_note_save(cid):
    """Customer.notes is a single free-text field, so this replaces it.

    The Jinja route was labelled "add note" but overwrote the same column;
    naming it 'save' here keeps the UI honest about what happens.
    """
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    note = (body().get('note') or '').strip()
    customer.notes = note or None
    db.session.commit()

    _audit('Update Note', f"Updated note for customer {customer.full_name}")
    return ok({'note': customer.notes or ''})


# --------------------------------------------------------------------------- #
#  Discounts
# --------------------------------------------------------------------------- #
@bp.post('/customers/<int:cid>/discount')
@admin_required
def customer_discount_set(cid):
    """Percent and flat amount are mutually exclusive - setting one clears
    the other, matching the original behaviour."""
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    data = body()
    discount_type = (data.get('discount_type') or '').strip().lower()

    try:
        value = float(data.get('value'))
    except (TypeError, ValueError):
        return fail('invalid_discount_value', 400)

    if value < 0:
        return fail('invalid_discount_value', 400,
                    detail='A discount cannot be negative.')

    if discount_type == 'percent':
        if value > 100:
            return fail('discount_percent_too_high', 400,
                        detail='A percentage discount cannot exceed 100%.')
        customer.discount_percent = value
        customer.discount_amount = 0
    elif discount_type == 'amount':
        customer.discount_amount = value
        customer.discount_percent = 0
    else:
        return fail('invalid_discount_type', 400,
                    detail="Use 'percent' or 'amount'.")

    db.session.commit()
    _audit('Add Discount',
           f"Added {discount_type} discount of {value} for {customer.full_name}")
    return ok(customer_dict(customer, detail=True))


@bp.delete('/customers/<int:cid>/discount')
@admin_required
def customer_discount_clear(cid):
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    customer.discount_percent = 0
    customer.discount_amount = 0
    db.session.commit()

    _audit('Remove Discount', f"Removed discount for {customer.full_name}")
    return ok(customer_dict(customer, detail=True))


# --------------------------------------------------------------------------- #
#  Messaging
# --------------------------------------------------------------------------- #
@bp.post('/customers/<int:cid>/send-sms')
@admin_required
def customer_send_sms(cid):
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    message = (body().get('message') or '').strip()
    if not message:
        return fail('message_required', 400)
    if not customer.mobile:
        return fail('no_mobile_number', 400,
                    detail='This customer has no mobile number on file.')

    try:
        from app import send_sms
        send_sms(customer.mobile, message)
    except Exception as exc:
        return fail('sms_failed', 424, detail=str(exc)[:200])

    _audit('Send SMS', f"Sent SMS to {customer.full_name}: {message[:50]}...")
    return ok({'status': 'sent', 'to': customer.mobile})


# --------------------------------------------------------------------------- #
#  Plans: assign / renew
# --------------------------------------------------------------------------- #
@bp.post('/customers/<int:cid>/assign-plan')
@admin_required
def customer_assign_plan(cid):
    """Attach a plan. Any currently active plan is terminated first."""
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    data = body()
    plan_id = data.get('plan_id')
    start_raw = data.get('start_date')

    if not plan_id or not start_raw:
        return fail('plan_and_start_date_required', 400)

    plan = db.session.get(Plan, int(plan_id))
    if not plan:
        return fail('plan_not_found', 404)

    try:
        start_date = datetime.strptime(str(start_raw)[:10], '%Y-%m-%d').date()
    except ValueError:
        return fail('invalid_start_date', 400)

    # Every open row is closed, not just the first one the database returns.
    # Assigning a plan is what makes it THE plan; leaving another row open
    # would put two live plans on the customer's Plan tab, each with its own
    # expiry date and its own Renew button.
    replaced = close_active_plans(cid)

    new_plan = CustomerPlan(
        customer_id=cid,
        plan_id=plan.id,
        start_date=start_date,
        end_date=start_date + timedelta(days=int(plan.validity_days or 30)),
        status='active',
        auto_renew=True,
        grace_period_days=1,
    )
    db.session.add(new_plan)
    db.session.commit()

    _audit('Assign Plan',
           f"Assigned plan {plan.name} to {customer.full_name}")
    return ok({
        'plan': customer_plan_dict(new_plan),
        'replaced_plan_id': replaced[0].id if replaced else None,
        'replaced_plan_ids': [cp.id for cp in replaced],
    }), 201


@bp.post('/customers/<int:cid>/renew-plan')
@admin_required
def customer_renew_plan(cid):
    """Extend the active plan and raise the renewal invoice.

    When auto_renew is on, the new period starts from today if the plan has
    already lapsed, so a late renewal does not grant backdated service.
    """
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    active_plan = current_plan(cid)
    if not active_plan:
        return fail('no_active_plan', 400,
                    detail='Assign a plan before renewing.')

    plan = active_plan.plan
    if not plan:
        return fail('plan_missing', 409,
                    detail='The linked plan no longer exists.')

    data = body()
    base = (max(active_plan.end_date, date.today())
            if active_plan.auto_renew else active_plan.end_date)
    new_end = base + timedelta(days=int(plan.validity_days or 30))

    invalid_date = False
    if data.get('start_date'):
        try:
            active_plan.start_date = datetime.strptime(
                str(data['start_date'])[:10], '%Y-%m-%d').date()
        except ValueError:
            invalid_date = True
    if data.get('end_date'):
        try:
            new_end = datetime.strptime(
                str(data['end_date'])[:10], '%Y-%m-%d').date()
        except ValueError:
            invalid_date = True

    if new_end < active_plan.start_date:
        return fail('end_date_before_start_date', 400)

    active_plan.end_date = new_end
    active_plan.status = 'active'
    active_plan.last_invoice_date = date.today()
    db.session.commit()

    try:
        from app import generate_invoice_no
        invoice_no = generate_invoice_no()
    except Exception:
        seq = (db.session.query(db.func.count(Invoice.id)).scalar() or 0) + 1
        invoice_no = f"INV-{date.today():%y%m}-{seq:05d}"

    invoice = Invoice(
        customer_id=cid,
        invoice_no=invoice_no,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=15),
        total_amount=active_plan.effective_price,
        tax_amount=0.00,
        customer_plan_id=active_plan.id,
        caption=plan.name,
        invoice_type='plan',
        status='sent',
    )
    db.session.add(invoice)
    db.session.commit()

    _audit('Renew Plan',
           f"Renewed plan {plan.name} for {customer.full_name} until {new_end}")

    # Tell the customer their plan is live again. The renewal is already
    # committed, so a gateway outage cannot roll it back - the send result is
    # reported alongside the renewal instead of replacing it.
    notification = _notify_renewal(customer, active_plan, plan, invoice,
                                   send=data.get('notify', True))

    return ok({
        'plan': customer_plan_dict(active_plan),
        'invoice': invoice_dict(invoice),
        'invalid_date_ignored': invalid_date,
        'notification': notification,
    }), 201


def _notify_renewal(customer, customer_plan, plan, invoice, send=True):
    """Send the 'renewal' WhatsApp template. Never raises."""
    if not send:
        return {'status': 'skipped', 'detail': 'Notification was turned off.'}
    if not customer.mobile:
        return {'status': 'skipped',
                'detail': 'No mobile number on file for this customer.'}

    try:
        from app import send_template_message
        result = send_template_message(
            customer, 'renewal',
            plan=plan, customer_plan=customer_plan, invoice=invoice)
    except Exception as exc:
        return {'status': 'failed', 'detail': str(exc)[:200]}

    status = getattr(result, 'status', 'unknown')
    if status == 'dry-run':
        return {'status': 'dry-run', 'to': customer.mobile,
                'detail': 'WhatsApp gateway is not configured, so the renewal '
                          'message was logged instead of sent.'}
    if status == 'sent':
        return {'status': 'sent', 'to': customer.mobile}
    return {'status': 'failed',
            'detail': getattr(result, 'detail', '')
            or 'The gateway rejected the renewal message.'}


# --------------------------------------------------------------------------- #
#  Removing a customer
# --------------------------------------------------------------------------- #
def _customer_history(customer):
    """What is attached to this customer that the business has to keep."""
    from models import Payment

    invoices = Invoice.query.filter_by(customer_id=customer.id).count()
    payments = Payment.query.filter_by(customer_id=customer.id).count()
    return invoices, payments


@bp.delete('/customers/<int:cid>')
@admin_required
def customer_delete(cid):
    """Remove a customer record entirely.

    Deliberately narrow: this deletes ONLY a customer with no invoices and no
    payments. That is the case it exists for - the duplicate, the typo, the
    row created during a demo - and it is the only case where deleting is
    honest.

    A customer who has been invoiced cannot be deleted, and the answer is a
    400 explaining why rather than a cascade. Two reasons:

      * Those invoices are GST records. India requires them kept for years
        after the financial year they belong to, and a delete that quietly
        took the bills with it would remove evidence the business is required
        to be able to produce.
      * The totals on every report are built from invoices and payments.
        Deleting them silently changes last month's collection figure, which
        somebody has already reconciled.

    Deactivating is the operation for a customer who has left: the line stops,
    the history stays, and the account can be brought back. That is offered in
    the refusal so the operator is not left with a dead end.
    """
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    invoices, payments = _customer_history(customer)
    if invoices or payments:
        parts = []
        if invoices:
            parts.append(f'{invoices} invoice' + ('s' if invoices != 1 else ''))
        if payments:
            parts.append(f'{payments} payment' + ('s' if payments != 1 else ''))
        return fail('customer_has_history', 400,
                    detail=f'{customer.full_name} has {" and ".join(parts)} on '
                           f'file, which are accounting records the business '
                           f'has to keep. Deactivate this customer instead - '
                           f'the connection stops and the history stays.',
                    invoices=invoices, payments=payments,
                    can_deactivate=bool(customer.is_active))

    name = customer.full_name
    try:
        # Rows that belong to the customer and carry no accounting weight. Each
        # is deleted explicitly rather than left to a cascade, because the
        # cascade rules differ per table and a missing one would abort the
        # whole delete with a foreign-key error and no explanation.
        CustomerPlan.query.filter_by(customer_id=cid).delete(
            synchronize_session=False)
        for model_name in ('WalletEntry', 'CustomerDocument', 'ServiceRequest',
                           'OnlinePaymentOrder', 'MessageLog', 'Notification'):
            try:
                model = getattr(__import__('models', fromlist=[model_name]),
                                model_name, None)
                if model is not None and hasattr(model, 'customer_id'):
                    model.query.filter_by(customer_id=cid).delete(
                        synchronize_session=False)
            except Exception:
                # A table this build does not have is not a reason to stop.
                continue

        db.session.delete(customer)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return fail('delete_failed', 500,
                    detail=f'{name} could not be removed, so nothing was '
                           f'changed. {str(exc)[:160]}')

    _audit('Delete Customer', f'{name} (id {cid}) removed - no billing history')
    return ok({'deleted': True, 'id': cid, 'name': name})
