"""
blueprints/api/renewals.py
==========================

The renewal queue: requests customers raised from the portal, waiting for
someone to approve them, plus the admin-side bulk renewal of plans that are
about to expire.

This is a thin REST layer over ``services/renewals.py``, deliberately. That
module already holds the rules - how a plan's extension base is chosen, what
happens to the invoice when a request is turned down, why approving brings a
suspended customer back online - and they were written once and tested through
the Jinja screens. Re-deriving them here would mean two implementations of
"what does approving a renewal do", which is exactly how the two halves of a
system drift apart.

The gap this closes: a customer could raise a renewal from the portal and
nobody in the React admin could see it, let alone approve it. The requests
piled up in a table with no screen.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, request
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from models import (Customer, CustomerPlan, Invoice, Payment, Plan, User, db)
from models_ext import InvoiceItem, RenewalRequest
from services import renewals as renewal_service
from services.plans import close_active_plans, current_plan

from .utils import (admin_required, body, current_staff_id, fail, iso, money,
                    ok, paginate, staff_required)

bp = Blueprint('api_renewals', __name__)


def _request_dict(req):
    customer = req.customer
    return {
        'id': req.id,
        'customer_id': req.customer_id,
        'customer_name': customer.full_name if customer else '',
        'username': (customer.username or '') if customer else '',
        'mobile': (customer.mobile or '') if customer else '',
        'zone': (customer.zone or '') if customer else '',
        'kind': req.kind,
        'kind_label': 'Plan change' if req.kind == 'change' else 'Renewal',
        'current_plan': req.current_plan.name if req.current_plan else '',
        'requested_plan': req.requested_plan.name if req.requested_plan else '',
        'is_upgrade': req.is_upgrade,
        'months': req.months,
        'days': req.days,
        'amount': money(req.amount),
        'status': req.status,
        'note': req.note or '',
        'decision_note': req.decision_note or '',
        'invoice_id': req.invoice_id,
        'invoice_no': req.invoice.invoice_no if req.invoice else '',
        # Whether the money actually arrived is the single most important
        # thing on this screen, so it is not left to be inferred.
        'invoice_paid': bool(req.invoice and req.invoice.balance <= 0),
        'invoice_balance': money(req.invoice.balance) if req.invoice else 0.0,
        'payment_id': req.payment_id,
        'current_expiry': iso(req.customer_plan.end_date) if req.customer_plan else '',
        'effective_from': iso(req.effective_from),
        'effective_to': iso(req.effective_to),
        'decided_at': iso(req.decided_at),
        'decided_by': req.decided_by.full_name if req.decided_by else '',
        'created_at': iso(req.created_at),
    }


@bp.get('/renewals')
@staff_required
def renewal_list():
    """The queue. Defaults to pending, because that is the work."""
    status = (request.args.get('status') or 'pending').strip()

    query = (RenewalRequest.query
             .options(joinedload(RenewalRequest.customer),
                      joinedload(RenewalRequest.invoice),
                      joinedload(RenewalRequest.requested_plan),
                      joinedload(RenewalRequest.current_plan)))

    if status and status != 'all':
        query = query.filter(RenewalRequest.status == status)

    kind = (request.args.get('kind') or '').strip()
    if kind in ('renew', 'change'):
        query = query.filter(RenewalRequest.kind == kind)

    paid = (request.args.get('paid') or '').strip()
    if paid in ('1', 'yes', 'true'):
        # Paid requests are the ones safe to approve, so they are worth
        # isolating when working through a long queue.
        query = query.join(RenewalRequest.invoice).filter(
            db.text('1=1'))

    q = (request.args.get('q') or '').strip()
    if q:
        safe = q.replace('%', '\\%').replace('_', '\\_')
        like = f'%{safe}%'
        query = query.join(Customer, Customer.id == RenewalRequest.customer_id) \
            .filter(or_(Customer.first_name.ilike(like, escape='\\'),
                        Customer.last_name.ilike(like, escape='\\'),
                        Customer.username.ilike(like, escape='\\'),
                        Customer.mobile.ilike(like, escape='\\')))

    query = query.order_by(RenewalRequest.created_at.desc())
    rows, meta = paginate(query, default_per_page=50)
    entries = [_request_dict(r) for r in rows]

    if paid in ('1', 'yes', 'true'):
        entries = [e for e in entries if e['invoice_paid']]

    return ok(entries, meta=meta, totals={
        'count': len(entries),
        'amount': round(sum(e['amount'] for e in entries), 2),
        'paid': sum(1 for e in entries if e['invoice_paid']),
        'unpaid': sum(1 for e in entries if not e['invoice_paid']),
    })


@bp.get('/renewals/counts')
@staff_required
def renewal_counts():
    """Badge counts, so the menu can show there is work waiting."""
    counts = {}
    for status in ('pending', 'approved', 'rejected', 'cancelled'):
        counts[status] = RenewalRequest.query.filter_by(status=status).count()

    pending = renewal_service.pending_requests()
    counts['pending_paid'] = sum(
        1 for r in pending if r.invoice and r.invoice.balance <= 0)
    counts['pending_amount'] = round(
        sum(float(r.amount or 0) for r in pending), 2)
    return ok(counts)


@bp.post('/renewals/<int:rid>/approve')
@admin_required
def renewal_approve(rid):
    """
    Apply one renewal: extend the plan, switching it first if this was an
    upgrade or downgrade.

    Approving an unpaid request is allowed but must be deliberate - the caller
    has to say ``allow_unpaid``. Extending service for money that never
    arrived is a decision someone should make on purpose, not a click that
    looks the same as any other.
    """
    req = db.session.get(RenewalRequest, rid)
    if req is None:
        return fail('not_found', 404)
    if req.status != 'pending':
        return fail('already_decided', 409,
                    detail=f'That request was already {req.status}.')

    data = body()
    if req.invoice is not None and req.invoice.balance > 0 \
            and not data.get('allow_unpaid'):
        return fail('invoice_unpaid', 409,
                    detail=f'{req.invoice.invoice_no} still has '
                           f'{money(req.invoice.balance):.2f} outstanding. '
                           f'Approve anyway only if you have the money.')

    user = db.session.get(User, current_staff_id())
    if not renewal_service.approve(req, user=user,
                                   note=(data.get('note') or '').strip()):
        return fail('could_not_approve', 400,
                    detail='That request could not be applied. It may be '
                           'missing its plan or customer.')

    _audit('Approve Renewal',
           f'{req.customer.full_name if req.customer else req.customer_id}: '
           f'{req.requested_plan.name if req.requested_plan else "?"} '
           f'to {iso(req.effective_to)}')

    _notify(req, 'renewal')
    return ok(_request_dict(req))


@bp.post('/renewals/<int:rid>/reject')
@admin_required
def renewal_reject(rid):
    """Turn a renewal down. Its invoice is cancelled so it stops chasing."""
    req = db.session.get(RenewalRequest, rid)
    if req is None:
        return fail('not_found', 404)
    if req.status != 'pending':
        return fail('already_decided', 409,
                    detail=f'That request was already {req.status}.')

    note = (body().get('note') or '').strip()
    if not note:
        return fail('note_required', 400,
                    detail='Say why this is being turned down - the customer '
                           'raised it and may ask.')

    # Money already taken for a renewal we are refusing is reported, not moved.
    # The wallet that used to hold it is gone, and silently keeping a payment
    # for service the customer will not receive is worse than saying so: the
    # operator has to refund it or raise a credit, and they can only do that
    # if they are told.
    paid = money(req.invoice.paid_amount) if req.invoice else 0.0

    user = db.session.get(User, current_staff_id())
    if not renewal_service.reject(req, user=user, note=note):
        return fail('could_not_reject', 400)

    _audit('Reject Renewal',
           f'{req.customer.full_name if req.customer else req.customer_id}: '
           f'{note}' + (f' ({paid:.2f} already paid - needs refunding)'
                        if paid else ''))

    payload = _request_dict(req)
    payload['already_paid'] = paid
    if paid:
        payload['detail'] = (f'{paid:.2f} had already been paid against this '
                             f'request. It has NOT been refunded automatically '
                             f'- settle it with the customer directly.')
    return ok(payload)


@bp.post('/renewals/bulk')
@admin_required
def renewal_bulk():
    """
    Decide several requests at once.

    Every id is answered for, and an unpaid request is skipped by name rather
    than swept along with the rest - so "12 approved" out of 15 selected is
    followed by the three reasons.
    """
    data = body()
    ids = data.get('ids') or []
    action = (data.get('action') or 'approve').strip()
    note = (data.get('note') or '').strip()
    allow_unpaid = bool(data.get('allow_unpaid'))

    if not isinstance(ids, list) or not ids:
        return fail('ids_required', 400,
                    detail='Select at least one request.')
    if action not in ('approve', 'reject'):
        return fail('invalid_action', 400)
    if action == 'reject' and not note:
        return fail('note_required', 400,
                    detail='Say why these are being turned down.')

    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return fail('invalid_ids', 400)

    user = db.session.get(User, current_staff_id())
    found = {r.id: r for r in RenewalRequest.query.filter(
        RenewalRequest.id.in_(ids)).all()}

    done, skipped = [], []
    for rid in ids:
        req = found.get(rid)
        name = (req.customer.full_name if req and req.customer
                else f'Request #{rid}')

        if req is None:
            skipped.append({'id': rid, 'name': name,
                            'reason': 'No such request.'})
            continue
        if req.status != 'pending':
            skipped.append({'id': rid, 'name': name,
                            'reason': f'Already {req.status}.'})
            continue

        if action == 'approve':
            if req.invoice is not None and req.invoice.balance > 0 \
                    and not allow_unpaid:
                skipped.append({
                    'id': rid, 'name': name,
                    'reason': f'{req.invoice.invoice_no} is unpaid '
                              f'({money(req.invoice.balance):.2f} due).'})
                continue
            applied = renewal_service.approve(req, user=user, note=note)
            if applied:
                _notify(req, 'renewal')
        else:
            paid = money(req.invoice.paid_amount) if req.invoice else 0.0
            applied = renewal_service.reject(req, user=user, note=note)
            if applied and paid > 0:
                done.append({'id': rid, 'name': name, 'already_paid': paid})
                continue

        if applied:
            done.append({'id': rid, 'name': name,
                         'effective_to': iso(req.effective_to)})
        else:
            skipped.append({'id': rid, 'name': name,
                            'reason': 'Could not be applied.'})

    _audit(f'Bulk {action.title()} Renewals',
           f'{len(done)} {action}d, {len(skipped)} skipped')

    return ok({'action': action, 'done': done, 'done_count': len(done),
               'skipped': skipped, 'skipped_count': len(skipped)})


@bp.post('/renewals/send-reminders')
@admin_required
def renewal_send_reminders():
    """
    Message every customer whose plan expires within `days`.

    The response reports the gateway's own verdict per customer rather than a
    bare count, because "sent 214 reminders" is worthless if the gateway was
    switched off and every one of them was a dry run.
    """
    data = body()
    try:
        days = int(data.get('days') or 7)
    except (TypeError, ValueError):
        days = 7
    days = max(0, min(days, 90))

    cutoff = date.today() + timedelta(days=days)
    rows = (CustomerPlan.query
            .options(joinedload(CustomerPlan.plan))
            .filter(CustomerPlan.status == 'active',
                    CustomerPlan.end_date <= cutoff,
                    CustomerPlan.end_date >= date.today())
            .all())

    zone = (data.get('zone') or '').strip()
    results = {'sent': [], 'dry_run': [], 'failed': [], 'skipped': []}

    for cp in rows:
        customer = db.session.get(Customer, cp.customer_id)
        if customer is None:
            continue
        if zone and (customer.zone or '') != zone:
            continue
        if not customer.mobile:
            results['skipped'].append({'name': customer.full_name,
                                       'reason': 'No mobile number.'})
            continue

        try:
            from app import send_template_message
            # 'expiry_3d', not 'expiry'. There has never been a template of
            # type 'expiry' - the seeded types are expiry_3d and expiry_2d -
            # so this button failed for every customer with "No active
            # 'expiry' message template", which reads like a configuration
            # problem the operator could fix and was not.
            outcome = send_template_message(customer, 'expiry_3d',
                                            customer_plan=cp, plan=cp.plan)
            status = getattr(outcome, 'status', 'unknown')
        except Exception as exc:
            status = 'failed'
            outcome = None
            results['failed'].append({'name': customer.full_name,
                                      'reason': 'An error occurred while sending the renewal message.'})
            continue

        entry = {'name': customer.full_name, 'mobile': customer.mobile,
                 'expires': iso(cp.end_date)}
        # 'queued' belongs with 'sent': the gateway has the message. Leaving
        # it to fall through to the else branch would report every single
        # successful hand-off as a failure.
        if status in ('sent', 'queued'):
            entry['queued'] = status == 'queued'
            results['sent'].append(entry)
        elif status == 'dry-run':
            results['dry_run'].append(entry)
        else:
            entry['reason'] = getattr(outcome, 'detail', status)[:120]
            results['failed'].append(entry)

    _audit('Send Expiry Reminders',
           f"{len(results['sent'])} sent, {len(results['dry_run'])} dry-run, "
           f"{len(results['failed'])} failed")

    return ok({
        'considered': len(rows),
        'sent_count': len(results['sent']),
        'dry_run_count': len(results['dry_run']),
        'failed_count': len(results['failed']),
        'skipped_count': len(results['skipped']),
        **results,
        'gateway_configured': _gateway_configured(),
    })


@bp.get('/renewals/due')
@staff_required
def renewals_due():
    """Plans about to expire - who the reminder run would contact."""
    try:
        days = int(request.args.get('days') or 7)
    except (TypeError, ValueError):
        days = 7
    days = max(0, min(days, 90))

    cutoff = date.today() + timedelta(days=days)
    query = (CustomerPlan.query
             .options(joinedload(CustomerPlan.plan))
             .join(Customer, Customer.id == CustomerPlan.customer_id)
             .filter(CustomerPlan.status == 'active',
                     CustomerPlan.end_date <= cutoff,
                     CustomerPlan.end_date >= date.today()))

    zone = (request.args.get('zone') or '').strip()
    if zone:
        query = query.filter(Customer.zone == zone)

    rows, meta = paginate(query.order_by(CustomerPlan.end_date.asc()),
                          default_per_page=100)

    entries = []
    for cp in rows:
        customer = db.session.get(Customer, cp.customer_id)
        if customer is None:
            continue
        entries.append({
            'customer_id': customer.id,
            'customer_plan_id': cp.id,
            'name': customer.full_name,
            'username': customer.username or '',
            'mobile': customer.mobile or '',
            'has_mobile': bool(customer.mobile),
            'zone': customer.zone or '',
            'plan_name': cp.plan.name if cp.plan else '',
            'end_date': iso(cp.end_date),
            'days_left': (cp.end_date - date.today()).days if cp.end_date else None,
        })

    return ok(entries, meta=meta, totals={
        'count': meta.get('total', len(entries)),
        'without_mobile': sum(1 for e in entries if not e['has_mobile']),
        'days': days,
    })


def _gateway_configured():
    try:
        from services import messaging
        return bool(messaging.is_configured())
    except Exception:
        return False


def _notify(req, template_type):
    """Tell the customer their renewal went through. Never fatal."""
    try:
        from app import send_template_message
        send_template_message(req.customer, template_type,
                              customer_plan=req.customer_plan,
                              plan=req.requested_plan,
                              invoice=req.invoice)
    except Exception as exc:
        current_app.logger.warning('Renewal notification (%s) failed for %s: %s',
                                   template_type, req.customer.full_name, exc)


def _audit(action, detail):
    try:
        from app import log_audit
        log_audit(action, detail)
    except Exception as exc:
        current_app.logger.warning('Audit log failed (%s): %s', action, exc)


# --------------------------------------------------------------------------- #
#  Counter renewal: renew a plan and take the money in one go
#
#  This is the everyday transaction - a customer walks in or calls, renews,
#  and pays. There was an Addon Invoice screen for extra charges but nothing
#  for the renewal itself, so the only way to do it was to renew on one screen
#  and then hunt for the invoice to record the payment against on another.
#
#  Ported from the Jinja renewal screen so the rules survive intact: renewing
#  early never loses days the customer has already paid for, a plan change
#  closes the old plan row rather than editing it, and a payment taken by
#  non-admin staff still goes to the authorisation queue.
# --------------------------------------------------------------------------- #
PAYMENT_MODES = ('Cash', 'Cheque', 'UPI', 'Card', 'NEFT', 'RTGS', 'IMPS',
                 'Bank Transfer', 'Paytm', 'GooglePay', 'PhonePay',
                 'Online Transfer', 'Credit Card')

#: Modes where a bank reference or cheque number is expected.
REFERENCED_MODES = {'Cheque', 'UPI', 'Card', 'NEFT', 'RTGS', 'IMPS',
                    'Bank Transfer', 'Paytm', 'GooglePay', 'PhonePay',
                    'Online Transfer', 'Credit Act'}


def _dec(value, default='0'):
    try:
        return Decimal(str(value if value not in (None, '') else default))
    except (InvalidOperation, TypeError):
        return Decimal(default)


def _gst_percent():
    try:
        return Decimal(str(_setting('gst_percent', 18) or 18))
    except (InvalidOperation, TypeError):
        return Decimal('18')


def _configured_tax_mode():
    """The company's tax treatment, from the `tax_type` setting.

    The renewal dialog used to open on "Non-taxable" whatever the company was
    configured to do, so unless the operator remembered to change it every
    counter renewal went out without GST - while the customer portal, which
    reads this setting, charged it. Same plan, two prices, depending on which
    door the customer came through. This is the default the form opens on; the
    operator can still override it per invoice.
    """
    value = (_setting('tax_type', '') or '').strip().lower()
    if _gst_percent() <= 0:
        return 'notax'
    if value.startswith('inc'):
        return 'include'
    if value.startswith('exc'):
        return 'exclude'
    return 'notax'


def _tax_mode_for(customer):
    """What the Tax dropdown should open on for THIS customer.

    ``Customer.tax_type`` is the per-account switch the office sets on the
    customer form, and until now nothing read it when money was calculated -
    a customer marked Non-Taxable was quoted GST at the counter and in the
    portal alike. Same rule as the portal's ``_customer_tax_mode``, so a
    renewal costs the same figure whichever door the customer comes through.

    Still only a DEFAULT here: the operator can override the dropdown for a
    single invoice, which is the point of having it on the form.
    """
    if _gst_percent() <= 0:
        return 'notax'
    tax_type = str(getattr(customer, 'tax_type', '') or '').strip().lower()
    if tax_type.startswith('non'):
        return 'notax'
    company = _configured_tax_mode()
    return company if company != 'notax' else 'exclude'


def _setting(key, default=None):
    try:
        from models_ext import Setting
        value = Setting.get(key)
        return default if value in (None, '') else value
    except Exception:
        return default


def _due_days():
    try:
        return int(_setting('invoice_due_days', 15) or 15)
    except (TypeError, ValueError):
        return 15


def _apply_tax(amount, mode):
    """(grand_total, tax_amount) for include / exclude / notax."""
    amount = _dec(amount)
    rate = _gst_percent()
    mode = (mode or 'notax').strip().lower()
    cents = Decimal('0.01')

    if amount <= 0 or rate <= 0 or mode == 'notax':
        return amount.quantize(cents), Decimal('0.00')
    if mode == 'exclude':
        tax = amount * rate / Decimal('100')
        return (amount + tax).quantize(cents), tax.quantize(cents)
    # include: the price already contains the tax, so work backwards.
    base = amount / (Decimal('1') + rate / Decimal('100'))
    return amount.quantize(cents), (amount - base).quantize(cents)


def _parse_date(value, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _extension_base(active):
    """Where the new period starts.

    The later of today and the current expiry, so a customer who renews a week
    early keeps the week they already paid for. Only when auto-renew is off do
    we run strictly from the old expiry.
    """
    if not active:
        return date.today()
    if active.auto_renew:
        return max(active.end_date, date.today())
    return active.end_date


@bp.get('/customers/<int:cid>/renew/quote')
@staff_required
def renew_quote(cid):
    """Everything the counter renewal form needs, with the sums already done."""
    customer = db.session.get(Customer, cid)
    if customer is None:
        return fail('not_found', 404)

    active = current_plan(cid)
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.name).all()

    open_invoices = [i for i in Invoice.query.filter(
        Invoice.customer_id == cid,
        Invoice.status.in_(('draft', 'sent', 'overdue'))).all() if i.balance > 0]

    base = _extension_base(active)

    return ok({
        'customer': {
            'id': customer.id,
            'full_name': customer.full_name,
            'account_id': f'C{customer.id}',
            'username': customer.username or '',
            'mobile': customer.mobile or '',
            'is_active': bool(customer.is_active),
            'discount_percent': money(customer.discount_percent),
            'discount_amount': money(customer.discount_amount),
        },
        'active_plan': {
            'customer_plan_id': active.id,
            'plan_id': active.plan_id,
            'plan_name': active.plan.name if active.plan else '',
            'price': money(active.effective_price),
            'start_date': iso(active.start_date),
            'end_date': iso(active.end_date),
            'auto_renew': bool(active.auto_renew),
            'days_left': (active.end_date - date.today()).days,
        } if active else None,
        'plans': [{
            'id': p.id, 'name': p.name,
            'price_monthly': money(p.price_monthly),
            'speed_mbps': p.speed_mbps,
            'validity_days': p.validity_days or 30,
        } for p in plans],
        # The dates a one-period renewal would produce, so the form opens on
        # the right answer instead of making the operator work it out.
        'suggested': {
            'start_date': iso(active.start_date if active else date.today()),
            'extends_from': iso(base),
            'end_date': iso(base + timedelta(
                days=int((active.plan.validity_days if active and active.plan
                          else 30) or 30))),
            'due_date': iso(date.today() + timedelta(days=_due_days())),
        },
        'outstanding': float(round(sum(i.balance for i in open_invoices))),
        'open_invoices': [{
            'id': i.id, 'invoice_no': i.invoice_no,
            'balance': money(i.balance), 'due_date': iso(i.due_date),
        } for i in open_invoices],
        'payment_modes': list(PAYMENT_MODES),
        'referenced_modes': sorted(REFERENCED_MODES),
        'gst_percent': money(_gst_percent()),
        # What the Tax dropdown should open on, so the counter and the
        # customer portal bill the same plan the same way by default - and so
        # a customer the office marked Non-Taxable is not quoted GST here
        # either.
        'tax_default': _tax_mode_for(customer),
        'due_days': _due_days(),
        'today': iso(date.today()),
    })


@bp.post('/customers/<int:cid>/renew')
@staff_required
def renew_at_counter(cid):
    """
    Renew (or change) the plan and raise the invoice for it.

    The money is NOT taken here. Collecting at the same moment meant a
    mis-keyed renewal could not simply be withdrawn, and a customer settling a
    renewal and an addon in one go had to be entered twice. The bill lands on
    the Pending Invoice tab and one payment entry clears whatever is owed.

    Everything is written in one transaction. A renewal that extended the plan
    but failed to save its invoice would leave a customer with free service
    and no record of why, so on any error nothing is kept.
    """
    customer = db.session.get(Customer, cid)
    if customer is None:
        return fail('not_found', 404)

    data = body()
    active = current_plan(cid)

    plan_id = data.get('plan_id')
    plan = db.session.get(Plan, int(plan_id)) if plan_id else (
        active.plan if active else None)
    if plan is None:
        return fail('plan_required', 400,
                    detail='Choose a plan to renew onto.')

    try:
        periods = max(1, min(int(data.get('periods') or 1), 36))
    except (TypeError, ValueError):
        periods = 1

    validity = int(plan.validity_days or 30)
    is_change = bool(active and active.plan_id != plan.id)
    base = _extension_base(active)

    start_date = _parse_date(data.get('start_date'),
                             active.start_date if active else date.today())
    end_date = _parse_date(data.get('end_date'),
                           base + timedelta(days=validity * periods))
    if start_date is None or end_date is None:
        return fail('invalid_date', 400, detail='Use YYYY-MM-DD for the dates.')
    if end_date <= start_date:
        return fail('end_before_start', 400,
                    detail='The new expiry must be after the start date.')

    default_unit_price = (
        active.effective_price
        if active is not None and active.plan_id == plan.id
        else plan.price_monthly
    )
    amount = _dec(data.get('amount'), str(_dec(default_unit_price) * periods))
    if amount < 0:
        return fail('invalid_amount', 400, detail='The amount cannot be negative.')

    discount = _dec(data.get('discount_amount'), '0')
    if discount < 0:
        discount = Decimal('0')
    if discount > amount:
        return fail('discount_exceeds_amount', 400,
                    detail='The discount cannot be more than the amount.')

    discount_reason = (data.get('discount_reason') or '').strip() or None
    if discount > 0 and not discount_reason:
        return fail('discount_reason_required', 400,
                    detail='Pick a discount type from Discount Master.')

    tax_mode = (data.get('tax_applicable') or 'notax').lower()
    grand_total, tax_amount = _apply_tax(amount - discount, tax_mode)

    caption = (data.get('caption') or '').strip() or (
        f'Plan change - {plan.name}' if is_change else plan.name)

    try:
        if is_change:
            # A plan change closes the old row rather than editing it, so the
            # customer's plan history still shows what they used to be on.
            # Any other row that was left open is closed with it - the plan
            # being written here is the one the customer is on.
            close_active_plans(cid, status='cancelled')
            db.session.flush()
            customer_plan = CustomerPlan(
                customer_id=customer.id, plan_id=plan.id,
                start_date=start_date, end_date=end_date, status='active',
                auto_renew=True,
                grace_period_days=(active.grace_period_days if active else 1),
                last_invoice_date=date.today(),
                suspension_review_status='none')
            db.session.add(customer_plan)
        elif active:
            # Renewing settles which row is the live one: this is the plan
            # that was just paid for, so anything else still open is closed.
            close_active_plans(cid, keep=active, status='cancelled')
            customer_plan = active
            customer_plan.start_date = start_date
            customer_plan.end_date = end_date
            customer_plan.status = 'active'
            customer_plan.last_invoice_date = date.today()
            customer_plan.suspension_review_status = 'none'
            customer_plan.suspended_at = None
        else:
            customer_plan = CustomerPlan(
                customer_id=customer.id, plan_id=plan.id,
                start_date=start_date, end_date=end_date, status='active',
                auto_renew=True, last_invoice_date=date.today())
            db.session.add(customer_plan)

        # Whatever price was agreed at the counter is this customer's price
        # from now on, so the next billing run charges the same figure.
        if data.get('amount') not in (None, '') and periods > 0:
            customer_plan.price = (amount / Decimal(periods)).quantize(
                Decimal('0.01'))

        db.session.flush()

        from .customer_billing import _next_invoice_no
        invoice = Invoice(
            customer_id=customer.id,
            customer_plan_id=customer_plan.id,
            invoice_no=_next_invoice_no(),
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=_due_days()),
            period_start=start_date,
            period_end=end_date,
            total_amount=grand_total,
            tax_amount=tax_amount,
            discount_amount=discount,
            discount_reason=discount_reason,
            caption=caption,
            invoice_type='plan',
            status='sent',
            remarks=(data.get('remarks') or '').strip() or None)
        db.session.add(invoice)
        db.session.flush()

        try:
            db.session.add(InvoiceItem(
                invoice_id=invoice.id,
                description=f'{plan.name} x {periods} '
                            f'({start_date:%d-%b-%Y} to {end_date:%d-%b-%Y})',
                item_type='plan', quantity=periods,
                unit_price=(amount / Decimal(periods)).quantize(Decimal('0.01')),
                discount_amount=discount,
                tax_percent=(_gst_percent() if tax_mode != 'notax'
                             else Decimal('0')),
                period_from=start_date, period_to=end_date))
        except Exception as exc:
            # The line item is a nicety; the invoice total is what is owed.
            current_app.logger.warning('Failed to add InvoiceItem line: %s', exc)

        reconnected = False
        if not customer.is_active and data.get('reactivate'):
            customer.is_active = True
            reconnected = True

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return fail('renewal_failed', 500,
                    detail='The renewal could not be saved, so nothing was '
                           'charged. Please try again later.')

    if reconnected:
        _reconnect(customer)

    _audit('Renew Plan',
           f'{customer.full_name}: {plan.name} x{periods} until {end_date} '
           f'(invoice {invoice.invoice_no}, {grand_total})')

    message_status = ''
    if data.get('send_message', True):
        message_status = _notify_renewal(customer, plan, customer_plan,
                                         invoice)

    return ok({
        'customer_plan_id': customer_plan.id,
        'plan_name': plan.name,
        'is_change': is_change,
        'start_date': iso(start_date),
        'end_date': iso(end_date),
        'periods': periods,
        'amount': money(amount),
        'discount': money(discount),
        'tax_amount': money(tax_amount),
        'grand_total': money(grand_total),
        'invoice_id': invoice.id,
        'invoice_no': invoice.invoice_no,
        'invoice_balance': money(invoice.balance),
        # Nothing has been collected here on purpose. The bill goes to the
        # Pending Invoice tab, where one payment entry can settle it together
        # with any addon the customer also owes for.
        'awaiting_payment': money(invoice.balance),
        'reconnected': reconnected,
        'message_status': message_status,
    })


def _is_admin():
    try:
        user = db.session.get(User, current_staff_id())
        return bool(user and user.role == 'admin')
    except Exception:
        return False


def _mode_detail(data):
    parts = [data.get('bank_name'), data.get('transaction_no'),
             data.get('transaction_date')]
    return ', '.join(str(p).strip() for p in parts if str(p or '').strip()) or None


def _reconnect(customer):
    """Put a disconnected line back on the network. Never fatal."""
    try:
        from app import enable_connection_on_network
        enable_connection_on_network(customer)
    except Exception as exc:
        current_app.logger.warning('Network reconnect failed for %s: %s',
                                   customer.full_name, exc)


def _notify_renewal(customer, plan, customer_plan, invoice):
    """Tell the customer their plan is renewed. No payment to report: this
    endpoint raises the bill, it does not take the money."""
    try:
        from app import send_template_message
        result = send_template_message(customer, 'renewal', plan=plan,
                                       customer_plan=customer_plan,
                                       invoice=invoice)
        return getattr(result, 'status', 'unknown')
    except Exception as exc:
        return 'failed: an error occurred while sending the renewal message.'
