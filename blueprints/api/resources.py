"""
blueprints/api/resources.py
===========================

Staff-facing REST resources for the React admin panel.

Covers: dashboard KPIs, customers, plans, invoices, payments and the payment
authorisation queue. Money credits the moment a payment is recorded;
authorisation stays a separate review step, exactly as in the Jinja2 app.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, request
from sqlalchemy import func, or_
from sqlalchemy.exc import DataError, IntegrityError

from models import (Customer, CustomerPlan, Invoice, Payment, Plan,
                    ServiceProvider, User, db)
from services.plans import close_active_plans

from .serializers import (customer_dict, customer_plan_dict, invoice_dict,
                          payment_dict, plan_dict, user_dict)
from .utils import (admin_required, body, current_staff_id, fail, money, ok,
                    paginate, staff_required)

bp = Blueprint('api_resources', __name__)

CUSTOMER_WRITABLE = (
    'title', 'customer_type', 'company_name', 'first_name', 'middle_name',
    'last_name', 'email', 'home_phone', 'mobile', 'username', 'gstin', 'pan',
    'aadhar', 'tax_type', 'connection_type', 'reference_id', 'zone',
    'flat_no', 'locality', 'area', 'building', 'billing_address',
    'primary_address', 'notes', 'discount_percent', 'discount_amount',
    'is_active',
    # Connection identity and location, added with the Add Customer redesign.
    'ip_address', 'ipacct_id', 'service_provider_id', 'billing_type',
    'invoice_date', 'latitude', 'longitude',
    # KYC proof *types* are plain text and safe to set from JSON; the files
    # themselves go through the multipart upload endpoint, never from here.
    'address_proof_type', 'id_proof_type',
)

#: Columns the client must not be able to write directly. wallet_balance is
#: money: it only moves through the wallet endpoints, which write a matching
#: WalletEntry. Accepting it here would let a bad payload set a balance with
#: no history behind it.
CUSTOMER_READONLY = ('wallet_balance', 'password_hash', 'reg_form_file',
                     'photo_file', 'address_proof_file', 'id_proof_file')

#: Blank means "not set" for these, not the empty string - a unique index
#: rejects a second row holding '' but allows any number of NULLs.
CUSTOMER_NULLABLE_UNIQUE = ('username', 'reference_id')


def _assign_customer_fields(customer, data):
    """Copy the writable fields off the payload, normalising empties."""
    email = data.get('email')
    if email and '@' not in str(email).strip():
        return None, 'Invalid email address format.'
    for field in CUSTOMER_WRITABLE:
        if field not in data:
            continue
        value = data[field]
        if field in CUSTOMER_NULLABLE_UNIQUE:
            value = (str(value).strip() or None) if value is not None else None
        elif field == 'service_provider_id':
            value = int(value) if str(value or '').strip().isdigit() else None
        elif field in ('invoice_date',) and not value:
            value = None
        setattr(customer, field, value)

    if not customer.billing_address:
        parts = [customer.flat_no, customer.building, customer.area, customer.locality]
        parts = [p for p in parts if p and p != '-']
        if parts:
            customer.billing_address = ', '.join(parts) + ', Navi Mumbai, Maharashtra'

    return customer


# --------------------------------------------------------------------------- #
#  Dashboard
# --------------------------------------------------------------------------- #
@bp.get('/dashboard')
@staff_required
def dashboard():
    today = date.today()
    month_start = today.replace(day=1)

    total_customers = Customer.query.count()
    active_customers = Customer.query.filter_by(is_active=True).count()

    active_plans = CustomerPlan.query.filter_by(status='active').count()
    expiring_7 = CustomerPlan.query.filter(
        CustomerPlan.status == 'active',
        CustomerPlan.end_date >= today,
        CustomerPlan.end_date <= today + timedelta(days=7)).count()
    expired = CustomerPlan.query.filter(
        CustomerPlan.status == 'active',
        CustomerPlan.end_date < today).count()

    collected_month = db.session.query(
        func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.status == 'approved',
        Payment.payment_date >= month_start).scalar() or 0

    pending_auth = Payment.query.filter(
        Payment.status.in_(('approved', 'pending')),
        Payment.authorized_at.is_(None)).count()

    outstanding = 0.0
    open_invoices = Invoice.query.filter(
        Invoice.status.in_(('draft', 'sent', 'overdue'))).all()
    for inv in open_invoices:
        if inv.balance > 0:
            outstanding += inv.balance

    # Collection split by mode for the current month
    by_mode = {'cash': 0.0, 'cheque': 0.0, 'online': 0.0, 'other': 0.0}
    for p in Payment.query.filter(Payment.status == 'approved',
                                  Payment.payment_date >= month_start).all():
        by_mode[p.mode_group] = by_mode.get(p.mode_group, 0.0) + money(p.amount)

    # Last six months of collection
    trend = []
    cursor = month_start
    for _ in range(6):
        nxt = (cursor + timedelta(days=32)).replace(day=1)
        amount = db.session.query(
            func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.status == 'approved',
            Payment.payment_date >= cursor,
            Payment.payment_date < nxt).scalar() or 0
        trend.append({'month': cursor.strftime('%b %Y'),
                      'amount': money(amount)})
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    trend.reverse()

    return ok({
        'customers': {'total': total_customers,
                      'active': active_customers,
                      'inactive': total_customers - active_customers},
        'plans': {'active': active_plans,
                  'expiring_7_days': expiring_7,
                  'expired': expired},
        'money': {'collected_this_month': money(collected_month),
                  'outstanding': round(outstanding, 2),
                  'pending_authorization': pending_auth,
                  'by_mode': by_mode},
        'trend': trend,
    })


# --------------------------------------------------------------------------- #
#  Customers
# --------------------------------------------------------------------------- #
@bp.get('/customers')
@staff_required
def customer_list():
    # customer_dict reads c.plans (to find the active one) and that plan's
    # Plan row. Without these two loaders that is two lazy loads PER ROW - a
    # 25-row page cost 33 queries, which is a third of a second of pure
    # latency once the database is on another host. Now it is 3.
    from sqlalchemy.orm import selectinload
    query = Customer.query.options(
        selectinload(Customer.plans).selectinload(CustomerPlan.plan))
    q = (request.args.get('q') or '').strip()
    if q:
        from .utils import escape_like
        like = f'%{escape_like(q)}%'
        query = query.filter(or_(
            Customer.first_name.ilike(like, escape='\\'),
            Customer.last_name.ilike(like, escape='\\'),
            Customer.mobile.ilike(like, escape='\\'),
            Customer.username.ilike(like, escape='\\'),
            Customer.reference_id.ilike(like, escape='\\'),
            Customer.email.ilike(like, escape='\\')))

    status = request.args.get('status')
    if status == 'active':
        query = query.filter(Customer.is_active.is_(True))
    elif status == 'inactive':
        query = query.filter(Customer.is_active.is_(False))

    zone = request.args.get('zone')
    if zone:
        query = query.filter(Customer.zone == zone)

    # Registration-date range - powers the dashboard's "New clients" drill-down.
    d_from = request.args.get('from')
    d_to = request.args.get('to')
    if d_from:
        query = query.filter(Customer.registration_date >= d_from)
    if d_to:
        query = query.filter(Customer.registration_date <= d_to)

    rows, meta = paginate(query.order_by(Customer.id.desc()))
    return ok([customer_dict(c) for c in rows], meta=meta)


@bp.get('/customers/<int:cid>')
@staff_required
def customer_detail(cid):
    from sqlalchemy.orm import joinedload, selectinload

    # Load the header's active plan and provider with the customer.  The
    # serializer uses both; leaving them lazy added several remote-database
    # round trips to every profile visit.
    customer = Customer.query.options(
        joinedload(Customer.plans).joinedload(CustomerPlan.plan),
        joinedload(Customer.service_provider),
    ).filter_by(id=cid).first()
    if not customer:
        return fail('not_found', 404)

    # Every one of these serialisers reaches into a relationship (an invoice's
    # payments for its balance, a payment's invoice number, a plan's Plan row),
    # so without the loaders below each list costs a query per element.
    invoices = Invoice.query.filter_by(customer_id=cid).options(
        joinedload(Invoice.payments)).order_by(
        Invoice.issue_date.desc(), Invoice.id.desc()).limit(50).all()
    payments = Payment.query.filter_by(customer_id=cid).options(
        joinedload(Payment.invoice), joinedload(Payment.customer),
        joinedload(Payment.received_by_user),
        joinedload(Payment.authorized_by_user)).order_by(
        Payment.payment_date.desc(), Payment.id.desc()).limit(50).all()
    plans = sorted(customer.plans, key=lambda cp: cp.id, reverse=True)

    # The detail header prints both figures. Compute them in SQL rather than
    # fetching a customer's full invoice history and all payment rows.
    from services.outstanding import outstanding_summary_for_customer
    outstanding, pending_count = outstanding_summary_for_customer(cid)
    invoice_count = Invoice.query.filter_by(customer_id=cid).count()

    return ok({
        'customer': customer_dict(customer, detail=True),
        'plans': [customer_plan_dict(cp) for cp in plans],
        'invoices': [invoice_dict(i) for i in invoices],
        'payments': [payment_dict(p) for p in payments],
        'outstanding': outstanding,
        'pending_invoice_count': pending_count,
        'wallet_balance': money(getattr(customer, 'wallet_balance', 0)),
        'invoice_count': invoice_count,
    })



def _commit_customer(customer, created):
    """Commit, turning DB-level rejections into JSON the UI can act on.

    Several Customer columns are MySQL ENUMs (title, customer_type,
    connection_type, tax_type). A value outside the allowed set raises
    DataError and, without this, surfaced as a 500 HTML traceback that the
    frontend could not parse - the save just appeared to do nothing.
    """
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return fail('duplicate_value', 409,
                    detail='A customer with that username or reference ID '
                           'already exists.' if 'Duplicate' in str(exc.orig)
                    else str(exc.orig)[:200])
    except DataError as exc:
        db.session.rollback()
        return fail('invalid_field_value', 422,
                    detail='One of the values is not allowed for its field. '
                           'Check Title, Customer type, Connection type and '
                           'Tax type. (' + str(exc.orig)[:160] + ')')
    return None


@bp.post('/customers')
@staff_required
def customer_create():
    data = body()
    for field in ('first_name', 'last_name', 'mobile'):
        if not data.get(field):
            return fail('first_name_last_name_mobile_required', 400)

    from services.usernames import availability, reserve

    username = (data.get('username') or '').strip()
    if username:
        # Checks the live table AND the permanent ledger of names already
        # issued - a username is spent when it is handed out, not freed when
        # the customer goes away.
        free, reason = availability(username)
        if not free:
            return fail('username_unavailable', 409, detail=reason)

    customer = Customer()
    result = _assign_customer_fields(customer, data)
    if isinstance(result, tuple) and result[0] is None:
        return fail('invalid_email', 400, detail=result[1])

    password = data.get('password') or '123456'
    customer.set_password(password)

    db.session.add(customer)
    error = _commit_customer(customer, created=True)
    if error:
        return error

    # Reserve only after the customer actually saved. Reserving first would
    # burn the name on a request that then failed validation.
    if username:
        reserve(username, customer_id=customer.id)
        db.session.commit()

    # The Add Customer screen assigns a plan in the same submit. Do it here
    # rather than making the browser fire a second request it could lose
    # halfway through, leaving a customer with no service attached.
    assigned, plan_error = _assign_initial_plan(customer, data)

    # Send welcome message (best-effort, never blocks the response)
    try:
        from app import send_template_message
        send_template_message(customer, 'welcome',
                              plan=assigned.plan if assigned else None,
                              customer_plan=assigned)
    except Exception:
        pass

    payload = customer_dict(customer, detail=True)
    payload['assigned_plan'] = customer_plan_dict(assigned) if assigned else None
    if plan_error:
        # The customer saved; only the plan did not. Say so plainly instead of
        # failing the whole request and losing the record just created.
        payload['plan_warning'] = plan_error
    return ok(payload), 201


def _assign_initial_plan(customer, data):
    """Attach the plan chosen on the Add Customer form. Returns (plan, warning)."""
    plan_id = data.get('plan_id')
    if not plan_id:
        return None, None

    plan = db.session.get(Plan, int(plan_id)) if str(plan_id).isdigit() else None
    if not plan:
        return None, 'The selected plan no longer exists, so none was assigned.'

    start_raw = data.get('plan_start_date') or data.get('start_date')
    try:
        start_date = (datetime.strptime(str(start_raw)[:10], '%Y-%m-%d').date()
                      if start_raw else date.today())
    except ValueError:
        return None, 'The plan start date was not understood, so no plan was assigned.'

    # Normally there is nothing to close here - this runs on a customer that
    # was created seconds ago. It is here because every path that assigns a
    # plan has to leave exactly one row open, and a resubmitted Add Customer
    # form that got as far as the plan the first time is the case it catches.
    close_active_plans(customer.id)

    customer_plan = CustomerPlan(
        customer_id=customer.id,
        plan_id=plan.id,
        start_date=start_date,
        end_date=start_date + timedelta(days=int(plan.validity_days or 30)),
        status='active',
        auto_renew=True,
        grace_period_days=1,
    )
    db.session.add(customer_plan)

    try:
        db.session.commit()
    except (IntegrityError, DataError) as exc:
        db.session.rollback()
        return None, f'The plan could not be assigned: {str(exc.orig)[:120]}'
    return customer_plan, None


@bp.put('/customers/<int:cid>')
@staff_required
def customer_update(cid):
    customer = db.session.get(Customer, cid)
    if not customer:
        return fail('not_found', 404)

    data = body()

    # The username is the customer's identity across every log line, message
    # and receipt already written. Letting it be edited would silently rewrite
    # who those refer to, and the name it freed could never be reissued
    # anyway. So it is set once, at creation, and refused afterwards.
    submitted = (data.get('username') or '').strip()
    if submitted and submitted.lower() != (customer.username or '').strip().lower():
        return fail('username_immutable', 409,
                    detail='The username cannot be changed once the account '
                           'has been created.')
    data.pop('username', None)

    result = _assign_customer_fields(customer, data)
    if isinstance(result, tuple) and result[0] is None:
        return fail('invalid_email', 400, detail=result[1])

    password = data.get('password')
    if password:
        customer.set_password(password)

    error = _commit_customer(customer, created=False)
    if error:
        return error
    return ok(customer_dict(customer, detail=True))


@bp.get('/customers/username-available')
@staff_required
def customer_username_available():
    """Live check for the Add Customer form.

    Answers the same question the create endpoint will answer on submit, using
    the same function - so the form cannot say "available" for a name that is
    then refused.
    """
    from services.usernames import availability

    raw = (request.args.get('username') or '').strip()
    if not raw:
        return ok({'username': '', 'available': False,
                   'reason': 'Enter a username to check.'})

    free, reason = availability(raw)
    return ok({'username': raw, 'available': free, 'reason': reason})


# DELETE /customers/<cid> is NOT defined here.
#
# It was - as a soft delete that only set is_active = False - while
# customer_actions.py defined the same URL as a real delete. Two handlers for
# one route, and which of them answered came down to blueprint registration
# order rather than a decision: the customers list showed a trash icon,
# confirmed "Deactivate customer?", and the customer screen offered a
# permanent delete, both hitting the same endpoint.
#
# The delete lives in customer_actions.py, which removes the account and
# everything attached to it. Deactivating is still available, and is a
# different verb: POST /customers/<cid>/disable.


# --------------------------------------------------------------------------- #
#  Plans
# --------------------------------------------------------------------------- #
@bp.get('/plans')
@staff_required
def plan_list():
    query = Plan.query
    if request.args.get('active') == '1':
        query = query.filter(Plan.is_active.is_(True))
    rows, meta = paginate(query.order_by(Plan.name))

    # How many customers are on each plan, as ONE grouped query.
    #
    # Deleting a plan somebody is subscribed to would orphan their billing
    # history, so the delete endpoint quietly deactivates instead. The screen
    # has to be able to say which of the two is about to happen BEFORE the
    # operator presses it - "Delete" that silently does something else is
    # worse than no button.
    counts = {}
    if rows:
        from sqlalchemy import func
        counts = dict(
            db.session.query(CustomerPlan.plan_id, func.count(CustomerPlan.id))
            .filter(CustomerPlan.plan_id.in_([p.id for p in rows]))
            .group_by(CustomerPlan.plan_id).all())

    payload = []
    for plan in rows:
        entry = plan_dict(plan)
        entry['customer_count'] = int(counts.get(plan.id, 0))
        payload.append(entry)
    return ok(payload, meta=meta)


@bp.post('/plans')
@admin_required
def plan_create():
    data = body()
    if not data.get('name') or data.get('price_monthly') in (None, ''):
        return fail('name_and_price_required', 400)

    plan = Plan(
        name=data['name'],
        plan_code=data.get('plan_code'),
        plan_type=data.get('plan_type'),
        speed_mbps=int(data.get('speed_mbps') or 0),
        price_monthly=data['price_monthly'],
        isp_amount=data.get('isp_amount') or 0,
        validity_days=int(data.get('validity_days') or 30),
        service_provider_id=data.get('service_provider_id') or None,
        is_active=bool(data.get('is_active', True)),
    )
    db.session.add(plan)
    db.session.commit()
    return ok(plan_dict(plan)), 201


@bp.put('/plans/<int:pid>')
@admin_required
def plan_update(pid):
    plan = db.session.get(Plan, pid)
    if not plan:
        return fail('not_found', 404)
    data = body()
    for field in ('name', 'plan_code', 'plan_type', 'speed_mbps',
                  'price_monthly', 'isp_amount', 'validity_days',
                  'service_provider_id', 'is_active'):
        if field in data:
            setattr(plan, field, data[field] or None
                    if field == 'service_provider_id' else data[field])
    db.session.commit()
    return ok(plan_dict(plan))


@bp.delete('/plans/<int:pid>')
@admin_required
def plan_delete(pid):
    """Remove a plan nobody is on.

    A plan somebody IS on cannot be deleted: their customer_plan rows and
    every invoice raised from them point at it, and removing it would leave
    that billing history describing a package that no longer exists.

    This used to answer that case by quietly switching the plan off and
    returning 200. That was defensible while the screen had one button whose
    label changed - it is not now that Retire is its own button, because an
    operator who presses Delete and is told "done" has been told the wrong
    thing. It refuses, says how many customers are in the way, and leaves
    them to press the other button.
    """
    plan = db.session.get(Plan, pid)
    if not plan:
        return fail('not_found', 404)

    subscribers = CustomerPlan.query.filter_by(plan_id=pid).count()
    if subscribers:
        return fail('plan_in_use', 409,
                    detail=f'{subscribers} customer plan'
                           f'{"" if subscribers == 1 else "s"} reference '
                           f'{plan.name}, including past ones whose invoices '
                           f'still name it. Retire it instead - it stops being '
                           f'offered and everything already billed stays '
                           f'readable.',
                    customers=subscribers,
                    can_retire=bool(plan.is_active))

    name = plan.name
    db.session.delete(plan)
    db.session.commit()
    return ok({'status': 'deleted', 'id': pid, 'name': name})


@bp.get('/service-providers')
@staff_required
def provider_list():
    rows = ServiceProvider.query.order_by(ServiceProvider.name).all()
    return ok([{'id': s.id, 'name': s.name, 'is_active': bool(s.is_active)}
               for s in rows])


@bp.post('/service-providers')
@admin_required
def provider_create():
    name = (body().get('name') or '').strip()
    if not name:
        return fail('name_required', 400)
    if ServiceProvider.query.filter_by(name=name).first():
        return fail('name_taken', 409)
    provider = ServiceProvider(name=name, is_active=bool(body().get('is_active', True)))
    db.session.add(provider)
    db.session.commit()
    return ok({'id': provider.id, 'name': provider.name,
               'is_active': bool(provider.is_active)}), 201


@bp.put('/service-providers/<int:sid>')
@admin_required
def provider_update(sid):
    provider = db.session.get(ServiceProvider, sid)
    if not provider:
        return fail('not_found', 404)
    data = body()
    name = (data.get('name') or '').strip()
    if not name:
        return fail('name_required', 400)
    duplicate = ServiceProvider.query.filter(
        ServiceProvider.name == name, ServiceProvider.id != sid).first()
    if duplicate:
        return fail('name_taken', 409)
    provider.name = name
    if 'is_active' in data:
        provider.is_active = bool(data['is_active'])
    db.session.commit()
    return ok({'id': provider.id, 'name': provider.name,
               'is_active': bool(provider.is_active)})


@bp.delete('/service-providers/<int:sid>')
@admin_required
def provider_delete(sid):
    provider = db.session.get(ServiceProvider, sid)
    if not provider:
        return fail('not_found', 404)
    if Plan.query.filter_by(service_provider_id=sid).first():
        provider.is_active = False
        db.session.commit()
        return ok({'status': 'deactivated'})
    db.session.delete(provider)
    db.session.commit()
    return ok({'status': 'deleted'})


# --------------------------------------------------------------------------- #
#  Invoices
# --------------------------------------------------------------------------- #
@bp.get('/invoices')
@staff_required
def invoice_list():
    # invoice_dict reads inv.customer for the name and inv.payments for the
    # balance - both lazy, both once per row. 53 queries became 3.
    #
    # The third one was missed: `caption` falls through to
    # customer_plan.plan.name for any bill without an explicit caption and
    # without a recorded payment mode - which is most unpaid bills. That is
    # another two lazy loads per row, and it put a 100-row page back at 69
    # queries. Locally that is milliseconds; against a hosted MySQL it is 60+
    # extra round trips before the page can render.
    from sqlalchemy.orm import selectinload
    query = Invoice.query.options(
        selectinload(Invoice.customer),
        selectinload(Invoice.payments),
        selectinload(Invoice.customer_plan).selectinload(CustomerPlan.plan))
    q = (request.args.get('q') or '').strip()
    if q:
        from .utils import escape_like
        query = query.filter(Invoice.invoice_no.ilike(f'%{escape_like(q)}%', escape='\\'))

    # 'pending' is not a stored status - it means anything still owing, which
    # is how the dashboard's Pending Bills column counts them.
    status = request.args.get('status')
    if status == 'pending':
        query = query.filter(Invoice.status.in_(('draft', 'sent', 'overdue')))
    elif status == 'cancelled':
        query = query.filter(Invoice.status == 'cancelled')
    elif status:
        query = query.filter(Invoice.status == status)
    else:
        query = query.filter(Invoice.status != 'cancelled')

    customer_id = request.args.get('customer_id')
    if customer_id:
        query = query.filter(Invoice.customer_id == customer_id)

    d_from = request.args.get('from')
    d_to = request.args.get('to')
    if d_from:
        query = query.filter(Invoice.issue_date >= d_from)
    if d_to:
        query = query.filter(Invoice.issue_date <= d_to)

    rows, meta = paginate(query.order_by(Invoice.issue_date.desc(),
                                         Invoice.id.desc()))
    return ok([invoice_dict(i) for i in rows], meta=meta)


@bp.get('/invoices/<int:iid>')
@staff_required
def invoice_detail(iid):
    inv = db.session.get(Invoice, iid)
    if not inv:
        return fail('not_found', 404)
    return ok(invoice_dict(inv, detail=True))


# --------------------------------------------------------------------------- #
#  Payments
# --------------------------------------------------------------------------- #
@bp.get('/payments')
@staff_required
def payment_list():
    # payment_dict reads p.invoice (for the number) and p.customer.
    from sqlalchemy.orm import selectinload
    query = Payment.query.options(
        selectinload(Payment.invoice),
        selectinload(Payment.customer),
        selectinload(Payment.received_by_user),
        selectinload(Payment.authorized_by_user))

    if request.args.get('pending_auth') == '1':
        query = query.filter(Payment.status.in_(('approved', 'pending')),
                             Payment.authorized_at.is_(None))
    status = request.args.get('status')
    if status:
        query = query.filter(Payment.status == status)
    customer_id = request.args.get('customer_id')
    if customer_id:
        query = query.filter(Payment.customer_id == customer_id)
    source = request.args.get('source')
    if source:
        query = query.filter(Payment.source == source)
    d_from = request.args.get('from')
    d_to = request.args.get('to')
    if d_from:
        query = query.filter(Payment.payment_date >= d_from)
    if d_to:
        query = query.filter(Payment.payment_date <= d_to)

    rows, meta = paginate(query.order_by(Payment.payment_date.desc(),
                                         Payment.id.desc()))
    return ok([payment_dict(p) for p in rows], meta=meta)


@bp.post('/payments')
@staff_required
def payment_create():
    data = body()
    if not data.get('invoice_id') or data.get('amount') in (None, ''):
        return fail('invoice_id_and_amount_required', 400)

    invoice = db.session.get(Invoice, int(data['invoice_id']))
    if not invoice:
        return fail('invoice_not_found', 404)

    from models import User as _User
    _current_user = _User.query.get(current_staff_id())
    _is_admin = _current_user.is_admin() if _current_user else False

    payment = Payment(
        invoice_id=invoice.id,
        customer_id=invoice.customer_id,
        amount=data['amount'],
        discount_amount=data.get('discount_amount') or 0,
        payment_date=data.get('payment_date') or date.today(),
        payment_mode=data.get('payment_mode') or 'Cash',
        mode_detail=data.get('mode_detail'),
        book_receipt_no=data.get('book_receipt_no'),
        remarks=data.get('remarks'),
        status='approved' if _is_admin else 'pending',
        source='admin',
        authorized_at=datetime.utcnow() if _is_admin else None,
        authorized_by_user_id=current_staff_id() if _is_admin else None,
        received_by_user_id=current_staff_id(),
    )
    db.session.add(payment)
    db.session.flush()

    if invoice.balance <= 0:
        invoice.status = 'paid'
    db.session.commit()
    return ok(payment_dict(payment)), 201


@bp.post('/payments/<int:pid>/authorize')
@admin_required
def payment_authorize(pid):
    payment = db.session.get(Payment, pid)
    if not payment:
        return fail('not_found', 404)
    payment.status = 'approved'
    payment.authorized_at = datetime.utcnow()
    payment.authorized_by_user_id = current_staff_id()
    db.session.commit()
    return ok(payment_dict(payment))


@bp.post('/payments/<int:pid>/reject')
@admin_required
def payment_reject(pid):
    payment = db.session.get(Payment, pid)
    if not payment:
        return fail('not_found', 404)

    reason = (body().get('reason') or '').strip()
    payment.status = 'rejected'
    payment.authorized_at = datetime.utcnow()
    payment.authorized_by_user_id = current_staff_id()
    payment.remarks = (payment.remarks or '') + '\nRejected: ' + reason

    invoice = payment.invoice
    if invoice and invoice.status == 'paid' and invoice.balance > 0:
        invoice.status = 'sent'

    db.session.commit()
    return ok(payment_dict(payment))


# --------------------------------------------------------------------------- #
#  Users (read-only here; full CRUD lives in staff.py)
# --------------------------------------------------------------------------- #
@bp.get('/users')
@staff_required
def user_list():
    rows = User.query.order_by(User.username).all()
    return ok([user_dict(u) for u in rows])
