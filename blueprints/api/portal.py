"""
blueprints/api/portal.py
========================

Customer-facing REST API - consumed by the React Native app.

Flow for an in-app payment:
  1. POST /api/v1/portal/pay/order      -> creates OnlinePaymentOrder + Cashfree order
  2. app opens Cashfree checkout with payment_session_id
  3. Cashfree calls POST /api/v1/portal/pay/webhook (signed, authoritative)
  4. webhook writes a Payment row with source='portal'

Because step 4 writes into the same ``payments`` table the counter staff use,
an app payment shows up in the admin panel automatically - no extra sync.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, current_app, request

from models import (Customer, CustomerPlan, Invoice, OnlinePaymentOrder,
                    Payment, Plan, db)
from services import cashfree
from services.outstanding import OPEN_STATUSES, outstanding_for_customer

from .serializers import (company_branding, customer_dict, customer_plan_dict,
                          invoice_dict, payment_dict, plan_dict)
from .utils import (body, current_customer_id, customer_required, fail, iso,
                    money, ok, paginate)

bp = Blueprint('api_portal', __name__)


def _setting_value(key):
    """One settings row, without importing the whole messaging module."""
    try:
        from models_ext import Setting
        row = Setting.query.filter_by(key=key).first()
        return row.value if row else ''
    except Exception:
        return ''


def _open_invoices(customer_id):
    """This customer's unsettled bills, oldest first.

    Oldest first because that is the order money should be applied in: a
    part payment that clears the newest bill and leaves the oldest one
    ageing towards disconnection is not what anybody means by "pay my dues".
    """
    from sqlalchemy.orm import selectinload

    rows = (Invoice.query.options(selectinload(Invoice.payments))
            .filter(Invoice.customer_id == customer_id,
                    Invoice.status.in_(OPEN_STATUSES))
            .order_by(Invoice.issue_date.asc(), Invoice.id.asc())
            .all())
    return [i for i in rows if i.balance > 0]


# --------------------------------------------------------------------------- #
#  Read screens
# --------------------------------------------------------------------------- #
@bp.get('/portal/dashboard')
@customer_required
def portal_dashboard():
    from sqlalchemy.orm import joinedload, selectinload

    customer = Customer.query.options(
        selectinload(Customer.plans).selectinload(CustomerPlan.plan),
        joinedload(Customer.service_provider),
    ).filter_by(id=current_customer_id()).first()
    if not customer:
        return fail('not_found', 404)

    active = next((cp for cp in customer.plans if cp.status == 'active'), None)
    invoices = Invoice.query.options(
        selectinload(Invoice.payments), joinedload(Invoice.customer)
    ).filter_by(customer_id=customer.id).order_by(
        Invoice.issue_date.desc(), Invoice.id.desc()).limit(5).all()
    payments = Payment.query.options(
        joinedload(Payment.invoice), joinedload(Payment.customer),
        joinedload(Payment.received_by_user),
        joinedload(Payment.authorized_by_user),
    ).filter_by(customer_id=customer.id).filter(
        Payment.status == 'approved').order_by(
        Payment.payment_date.desc(), Payment.id.desc()).limit(5).all()

    # One query, and the same arithmetic the admin dashboard uses. Reading
    # Invoice.balance in a loop lazy-loads each invoice's payments, so the
    # customer's own home screen got slower with every bill they had ever
    # been sent - and it counted CANCELLED invoices, which the admin side
    # does not, so the two disagreed about what the customer owed.
    outstanding = outstanding_for_customer(customer.id)
    due = _open_invoices(customer.id)

    return ok({
        'customer': customer_dict(customer, detail=True),
        'active_plan': customer_plan_dict(active),
        'outstanding': outstanding,
        'due_invoice_count': len(due),
        'oldest_due_invoice': invoice_dict(due[0]) if due else None,
        'recent_invoices': [invoice_dict(i) for i in invoices],
        'recent_payments': [payment_dict(p) for p in payments],
        'branding': company_branding(),
        'gateway_ready': cashfree.is_configured(),
    })


@bp.get('/portal/invoices')
@customer_required
def portal_invoices():
    cid = current_customer_id()
    from sqlalchemy.orm import joinedload, selectinload
    query = Invoice.query.options(
        joinedload(Invoice.customer), selectinload(Invoice.payments)
    ).filter_by(customer_id=cid)
    rows, meta = paginate(query.order_by(Invoice.issue_date.desc(),
                                         Invoice.id.desc()))
    # The account total, not the total of this page. A customer on page 2 of
    # their bills still owes the same amount, and a "pay everything" button
    # that quietly meant "pay the four bills you can currently see" would take
    # the wrong money.
    open_invoices = _open_invoices(cid)
    meta = dict(meta or {})
    meta['outstanding'] = outstanding_for_customer(cid)
    meta['due_invoice_count'] = len(open_invoices)
    return ok([invoice_dict(i) for i in rows], meta=meta)


@bp.get('/portal/invoices/<int:iid>')
@customer_required
def portal_invoice_detail(iid):
    inv = db.session.get(Invoice, iid)
    if not inv or inv.customer_id != current_customer_id():
        return fail('not_found', 404)
    return ok(invoice_dict(inv, detail=True))


@bp.get('/portal/payments')
@customer_required
def portal_payments():
    from sqlalchemy.orm import joinedload
    query = Payment.query.options(
        joinedload(Payment.invoice), joinedload(Payment.customer),
        joinedload(Payment.received_by_user),
        joinedload(Payment.authorized_by_user),
    ).filter_by(customer_id=current_customer_id())
    rows, meta = paginate(query.order_by(Payment.payment_date.desc(),
                                         Payment.id.desc()))
    return ok([payment_dict(p) for p in rows], meta=meta)


@bp.get('/portal/plans')
@customer_required
def portal_plans():
    """Plans a customer may switch to."""
    rows = Plan.query.filter_by(is_active=True).order_by(Plan.price_monthly).all()
    return ok([plan_dict(p) for p in rows])


# --------------------------------------------------------------------------- #
#  Renewal / plan change
# --------------------------------------------------------------------------- #
def _raise_invoice(customer, plan, caption, customer_plan=None):
    """Create an unpaid invoice for a renewal or plan change."""
    seq = (db.session.query(db.func.count(Invoice.id)).scalar() or 0) + 1
    invoice_no = f"INV-{date.today().strftime('%y%m')}-{seq:05d}"
    while Invoice.query.filter_by(invoice_no=invoice_no).first():
        seq += 1
        invoice_no = f"INV-{date.today().strftime('%y%m')}-{seq:05d}"

    due_days = int(current_app.config.get('INVOICE_DUE_DAYS', 15) or 15)
    unit_price = (
        customer_plan.effective_price
        if customer_plan is not None and customer_plan.plan_id == plan.id
        else plan.price_monthly
    )
    invoice = Invoice(
        customer_id=customer.id,
        customer_plan_id=customer_plan.id if customer_plan else None,
        invoice_no=invoice_no,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=due_days),
        total_amount=unit_price,
        tax_amount=0.00,
        caption=caption,
        invoice_type='plan',
        status='sent',
    )
    db.session.add(invoice)
    db.session.commit()
    return invoice


@bp.post('/portal/renew')
@customer_required
def portal_renew():
    customer = db.session.get(Customer, current_customer_id())
    active = CustomerPlan.query.filter_by(customer_id=customer.id,
                                          status='active').first()
    if not active or not active.plan:
        return fail('no_plan_to_renew', 400)

    # Reuse an open invoice if there already is one
    open_invoice = next(
        (i for i in Invoice.query.filter(
            Invoice.customer_id == customer.id,
            Invoice.status.in_(('draft', 'sent', 'overdue'))).all()
         if i.balance > 0), None)

    invoice = open_invoice or _raise_invoice(
        customer, active.plan, 'Renewal - ' + active.plan.name,
        customer_plan=active)

    return ok({'invoice': invoice_dict(invoice, detail=True)})


@bp.post('/portal/change-plan')
@customer_required
def portal_change_plan():
    data = body()
    plan_id = data.get('plan_id')
    if not plan_id:
        return fail('plan_id_required', 400)

    plan = db.session.get(Plan, int(plan_id))
    if not plan or not plan.is_active:
        return fail('plan_not_found', 404)

    customer = db.session.get(Customer, current_customer_id())
    invoice = _raise_invoice(customer, plan, 'Plan change - ' + plan.name)
    # Remember the target plan so the webhook can apply it after payment.
    invoice.remarks = f'PLAN_CHANGE:{plan.id}'
    db.session.commit()

    return ok({'invoice': invoice_dict(invoice, detail=True),
               'plan': plan_dict(plan)})


# --------------------------------------------------------------------------- #
#  Cashfree
# --------------------------------------------------------------------------- #
@bp.get('/portal/pay/config')
@customer_required
def portal_pay_config():
    """
    Whether online payment is available, before the customer commits to it.

    Without this the portal had to offer a Pay button and find out from a 503
    that the gateway was never configured - the customer taps, waits, and gets
    an error for something that was never going to work. Asking first lets the
    screen explain how to pay instead.
    """
    configured = False
    try:
        configured = bool(cashfree.is_configured())
    except Exception:
        configured = False

    # When there is no gateway, the customer still has to be able to pay -
    # they just cannot do it with a card. Answering "online payment is not
    # switched on" and stopping there leaves somebody who wants to give us
    # money with nowhere to go, which is the worst possible outcome for a
    # screen that exists to collect it. So the offline route comes back too:
    # the bank details and the phone number already on the company record.
    offline = {}
    try:
        from models import Company
        company = Company.query.first()
        if company:
            offline = {
                'company': company.name or '',
                'bank_details': (company.bank_account_details or '').strip(),
                'phone': (company.mobile or company.phone or '').strip(),
                'upi': (_setting_value('upi_id') or '').strip(),
            }
    except Exception:
        offline = {}

    return ok({
        'enabled': configured,
        'gateway': 'cashfree',
        'environment': cashfree.environment() if configured else '',
        'sdk_url': cashfree.sdk_url() if configured else '',
        'offline': offline,
        'detail': '' if configured else
                  'Card and UPI payment is not switched on yet. '
                  'You can still pay by bank transfer or at the office.',
    })


@bp.get('/portal/invoices/<int:iid>/pdf')
@customer_required
def portal_invoice_pdf(iid):
    """A customer's own bill as a PDF.

    Deliberately separate from the staff route rather than relaxing that one:
    the ownership check is the whole point, and it belongs on a route that can
    only ever be reached with a customer token.
    """
    invoice = db.session.get(Invoice, iid)
    if not invoice or invoice.customer_id != current_customer_id():
        return fail('not_found', 404)

    try:
        from services.invoice_pdf import build_invoice_pdf
    except ImportError as exc:
        from flask import current_app
        current_app.logger.error('Portal invoice PDF unavailable: %s', exc)
        return fail('pdf_unavailable', 503,
                    detail='Bill downloads are temporarily unavailable. '
                           'Please contact the office.')

    logo = None
    try:
        import os
        from models import Company
        company = Company.query.first()
        name = getattr(company, 'company_logo', None)
        if name:
            candidate = os.path.join(current_app.root_path, 'static',
                                     'uploads', name)
            logo = candidate if os.path.exists(candidate) else None
    except Exception:
        logo = None

    try:
        detailed = (request.args.get('detail') or '').strip() in ('1', 'true', 'yes')
        pdf = build_invoice_pdf(invoice, logo_path=logo, detailed=detailed)
    except Exception:
        return fail('pdf_failed', 500,
                    detail='That bill could not be generated. Please contact us.')

    return Response(pdf, mimetype='application/pdf', headers={
        'Content-Disposition':
            f'inline; filename="{invoice.invoice_no or "invoice"}.pdf"',
    })


@bp.post('/portal/pay/order')
@customer_required
def portal_pay_order():
    if not cashfree.is_configured():
        return fail('payment_gateway_not_configured', 503)

    data = body()
    invoice_id = data.get('invoice_id')

    # No invoice_id means "pay what I owe". The customer sees one number on
    # their dashboard - the account total - and asking them to work out which
    # of four bills to settle first is our filing problem, not theirs.
    if invoice_id:
        invoice = db.session.get(Invoice, int(invoice_id))
        if not invoice or invoice.customer_id != current_customer_id():
            return fail('invoice_not_found', 404)
        ceiling = round(float(invoice.balance), 2)
    else:
        open_invoices = _open_invoices(current_customer_id())
        if not open_invoices:
            return fail('nothing_to_pay', 400,
                        detail='There is nothing outstanding on your account.')
        invoice = open_invoices[0]
        ceiling = outstanding_for_customer(current_customer_id())

    requested = data.get('amount')
    amount = round(float(requested if requested not in (None, '') else ceiling), 2)
    if amount <= 0:
        return fail('nothing_to_pay', 400)
    # Never take more than is owed. Refunding an overpayment is a manual job
    # at the counter, and the customer would rather be stopped here.
    if amount > ceiling + 0.01:
        return fail('amount_exceeds_due', 400,
                    detail=f'Only Rs.{ceiling:.2f} is outstanding on this '
                           f'account right now.')

    customer = invoice.customer
    order_id = cashfree.new_order_id()
    order = OnlinePaymentOrder(
        order_id=order_id,
        customer_id=customer.id,
        invoice_id=invoice.id,
        gateway='cashfree',
        amount=amount,
        status='created',
        note=(f'App payment for {invoice.invoice_no}' if invoice_id
              else f'App payment - account dues (from {invoice.invoice_no})'),
    )
    db.session.add(order)
    db.session.commit()

    return_url = (data.get('return_url')
                  or current_app.config.get('CASHFREE_RETURN_URL')
                  or '')

    try:
        payload = cashfree.create_order(
            order_id=order_id,
            amount=amount,
            customer_id=customer.id,
            customer_phone=customer.mobile,
            customer_name=customer.full_name,
            customer_email=customer.email or '',
            return_url=return_url,
            notify_url=request.url_root.rstrip('/') + '/api/v1/portal/pay/webhook',
            note=order.note,
        )
    except cashfree.CashfreeError as exc:
        order.status = 'failed'
        order.note = str(exc)[:255]
        db.session.commit()
        return fail('gateway_error', 424, detail=str(exc)[:200])

    order.payment_session_id = payload.get('payment_session_id')
    order.cf_order_id = str(payload.get('cf_order_id') or '')
    db.session.commit()

    return ok({
        'order_id': order.order_id,
        'cf_order_id': order.cf_order_id,
        'payment_session_id': order.payment_session_id,
        'amount': money(order.amount),
        'environment': cashfree.environment(),
        'sdk_url': cashfree.sdk_url(),
    })


def _allocate(order, amount, txn_id, method):
    """Credit ``amount`` across this customer's open bills, oldest first.

    One payment can now settle more than one invoice, because the portal lets
    a customer pay their account total rather than picking a bill. Writing the
    whole sum against the single invoice the order was raised from would leave
    that invoice over-paid and the others still showing as due - the ledger,
    the reminders and the disconnection list would all be wrong.

    Money always starts at the invoice the order names, so paying one specific
    bill still lands where the customer expected, and only the surplus flows
    onward. Anything left over after every open bill is settled stays on that
    first invoice as a credit rather than vanishing.
    """
    invoice = order.invoice
    customer = order.customer

    targets = [invoice] + [i for i in _open_invoices(customer.id)
                           if i.id != invoice.id]

    payments, remaining = [], round(float(amount), 2)
    for target in targets:
        if remaining <= 0:
            break
        share = min(remaining, round(float(target.balance), 2))
        if share <= 0:
            continue
        payments.append((target, share))
        remaining = round(remaining - share, 2)

    # Nothing owed anywhere (or a deliberate overpayment): keep it on the
    # named invoice so the money is recorded rather than silently dropped.
    if remaining > 0:
        if payments and payments[0][0] is invoice:
            payments[0] = (invoice, round(payments[0][1] + remaining, 2))
        else:
            payments.insert(0, (invoice, remaining))

    written = []
    for target, share in payments:
        payment = Payment(
            invoice_id=target.id,
            customer_id=customer.id,
            amount=share,
            payment_date=date.today(),
            payment_mode='Online',
            mode_detail=f'Cashfree {method} | Txn {txn_id} | Order {order.order_id}',
            gateway_transaction_id=str(txn_id or order.order_id)[:100],
            source='portal',
            status='approved',
            remarks=f'Cashfree order {order.order_id} (online portal)',
        )
        db.session.add(payment)
        written.append((payment, target))

    db.session.flush()

    # Expire the loaded payment collections before asking for the balance.
    # Invoice.balance sums `self.payments`, and these rows were inserted by
    # invoice_id rather than appended to that relationship - so a collection
    # already loaded in this request (it is: _open_invoices reads .balance to
    # decide what is open) still holds the OLD list. Without this, a fully
    # settled invoice keeps its 'sent' status, stays on the dues list, and
    # gets chased for money the customer has already paid.
    for _, target in written:
        db.session.expire(target, ['payments'])

    for _, target in written:
        if target.balance <= 0:
            target.status = 'paid'

    return written[0][0] if written else None


def _apply_successful_payment(order, txn_id=None, method=None):
    """Idempotently credit a paid Cashfree order and extend the plan."""
    if order.status == 'paid':
        return order

    invoice = order.invoice
    customer = order.customer
    method = str(method or 'Online')[:50]

    payment = _allocate(order, order.amount, txn_id, method)

    order.status = 'paid'
    order.transaction_id = str(txn_id or '')[:100]
    order.payment_method = method
    order.payment_id = payment.id if payment else None

    active = CustomerPlan.query.filter_by(customer_id=customer.id,
                                          status='active').first()

    # A plan-change invoice carries PLAN_CHANGE:<id> in remarks.
    target_plan = None
    if invoice.remarks and 'PLAN_CHANGE:' in invoice.remarks:
        try:
            target_plan = db.session.get(
                Plan, int(invoice.remarks.split('PLAN_CHANGE:')[1].split(':')[0]))
        except (ValueError, IndexError):
            target_plan = None

    if target_plan:
        if active:
            active.status = 'cancelled'
        new_plan = CustomerPlan(
            customer_id=customer.id,
            plan_id=target_plan.id,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=target_plan.validity_days or 30),
            status='active',
            last_invoice_date=date.today(),
            suspension_review_status='none',
        )
        db.session.add(new_plan)
        active = new_plan
    elif active and active.plan:
        base = max(active.end_date, date.today())
        active.end_date = base + timedelta(days=active.plan.validity_days or 30)
        active.status = 'active'
        active.last_invoice_date = date.today()

    if not customer.is_active:
        customer.is_active = True

    db.session.commit()
    return order


@bp.post('/portal/pay/webhook')
def portal_pay_webhook():
    """
    Cashfree server-to-server callback. Public (no JWT) but signature-verified.
    """
    raw = request.get_data()
    signature = request.headers.get('x-webhook-signature', '')
    timestamp = request.headers.get('x-webhook-timestamp', '')

    if not cashfree.verify_webhook(raw, signature, timestamp):
        return fail('bad_signature', 401)

    payload = request.get_json(silent=True) or {}
    data = payload.get('data', {}) or {}
    order_block = data.get('order', {}) or {}
    payment_block = data.get('payment', {}) or {}

    order_id = order_block.get('order_id') or order_block.get('orderId')
    if not order_id:
        return fail('missing_order_id', 400)

    order = OnlinePaymentOrder.query.filter_by(order_id=order_id).first()
    if not order:
        return fail('order_not_found', 404)

    status = str(payment_block.get('payment_status', '')).upper()
    if status == 'SUCCESS':
        _apply_successful_payment(
            order,
            txn_id=payment_block.get('cf_payment_id')
            or payment_block.get('bank_reference'),
            method=payment_block.get('payment_group'))
    elif status in ('FAILED', 'USER_DROPPED'):
        if order.status != 'paid':
            order.status = 'failed'
            db.session.commit()

    return ok({'status': order.status})


@bp.get('/portal/pay/status/<order_id>')
@customer_required
def portal_pay_status(order_id):
    order = OnlinePaymentOrder.query.filter_by(order_id=order_id).first()
    if not order or order.customer_id != current_customer_id():
        return fail('not_found', 404)

    if order.status != 'paid':
        try:
            data = cashfree.fetch_order(order.order_id)
            if cashfree.is_paid(data):
                detail = cashfree.successful_payment(order.order_id) or {}
                _apply_successful_payment(
                    order,
                    txn_id=detail.get('cf_payment_id') or order.cf_order_id,
                    method=detail.get('payment_group') or 'Cashfree')
        except cashfree.CashfreeError:
            pass

    return ok({
        'order_id': order.order_id,
        'status': order.status,
        'amount': money(order.amount),
        'transaction_id': order.transaction_id or '',
        'payment_method': order.payment_method or '',
        'invoice': invoice_dict(order.invoice) if order.invoice else None,
    })
