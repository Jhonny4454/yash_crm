"""
blueprints/portal_bp.py
=======================

The customer self-service portal, beyond the four screens that already lived
in app.py (login / dashboard / profile / checkout).

Adds:

    /customer/forgot-password       Request an OTP
    /customer/reset-password        Set a new password with the OTP
    /customer/invoices              Invoice list + filters
    /customer/invoices/<id>         Invoice detail with a Pay button
    /customer/invoices/<id>/print   Print / save-as-PDF view
    /customer/payments              Payment history
    /customer/payments/<id>/receipt Printable receipt
    /customer/plans                 Browse plans / request an upgrade
    /customer/pay/<invoice_id>      Pay ANY open invoice online
    /customer/notifications         In-app notification inbox

Everything is session-based (same cookie as the rest of the portal), so it
sits alongside the JWT REST API in blueprints/api/portal.py rather than
replacing it.
"""
import hashlib
import os
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, session, url_for)
from werkzeug.utils import secure_filename

from models import (Company, Customer, CustomerPlan, Invoice,
                    OnlinePaymentOrder, Payment, Plan, db)
from models_ext import RenewalRequest
from services import cashfree, messaging, renewals

portal_bp = Blueprint('portal', __name__, url_prefix='/customer')

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


# --------------------------------------------------------------------------- #
#  Guards & helpers
# --------------------------------------------------------------------------- #
def customer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'customer_id' not in session:
            flash('Please log in as a customer.', 'warning')
            return redirect(url_for('customer_login', next=request.path))
        customer = db.session.get(Customer, session['customer_id'])
        if not customer or not customer.is_active:
            session.pop('customer_id', None)
            flash('Account not found or inactive.', 'danger')
            return redirect(url_for('customer_login'))
        return f(*args, **kwargs)
    return decorated


def _me():
    return db.session.get(Customer, session.get('customer_id'))


def _active_plan(customer):
    return CustomerPlan.query.filter_by(customer_id=customer.id,
                                        status='active').first()


def _outstanding(customer_id):
    rows = Invoice.query.filter_by(customer_id=customer_id).all()
    return float(round(sum(i.balance for i in rows if i.balance > 0)))


def _next_invoice_no():
    from models import Invoice as _I
    today = date.today().strftime('%Y%m%d')
    last = db.session.execute(
        db.select(_I.id).order_by(_I.id.desc()).limit(1)).scalar() or 0
    for attempt in range(20):
        candidate = f"INV-{today}-{last + 1 + attempt:04d}"
        if not _I.query.filter_by(invoice_no=candidate).first():
            return candidate
    return f"INV-{today}-{secrets.token_hex(4).upper()}"


def _log(action, details):
    """Best-effort audit entry - never breaks the request."""
    try:
        from models import AuditLog
        db.session.add(AuditLog(action=action, details=details,
                                ip_address=request.remote_addr))
        db.session.commit()
    except Exception:
        db.session.rollback()


# --------------------------------------------------------------------------- #
#  OTP helpers (no extra table - signed values live in the session)
# --------------------------------------------------------------------------- #
def _hash_otp(code):
    salt = current_app.config.get('SECRET_KEY', '')
    return hashlib.sha256((salt + str(code)).encode()).hexdigest()


def _issue_otp(customer, purpose):
    code = f'{secrets.randbelow(1000000):06d}'
    session['otp'] = {
        'purpose': purpose,
        'customer_id': customer.id,
        'hash': _hash_otp(code),
        'expires': (datetime.utcnow()
                    + timedelta(minutes=OTP_TTL_MINUTES)).isoformat(),
        'attempts': 0,
    }
    session.modified = True

    company = Company.query.first()
    brand = company.name if company else 'Your ISP'
    text = (f'{code} is your {brand} verification code. '
            f'It expires in {OTP_TTL_MINUTES} minutes. Do not share it.')

    delivered = False
    for sender in (messaging.send_whatsapp, messaging.send_sms):
        try:
            result = sender(customer.mobile, text, customer_id=customer.id,
                            template_type='otp')
            if getattr(result, 'ok', False):
                delivered = True
                break
        except Exception:
            continue

    if current_app.debug and not delivered:
        current_app.logger.warning('OTP for %s is %s (no gateway configured)',
                                   customer.mobile, code)
    return delivered


def _verify_otp(code, purpose):
    """Returns ``(customer, error_message)``."""
    data = session.get('otp')
    if not data or data.get('purpose') != purpose:
        return None, 'Please request a new code.'

    try:
        expires = datetime.fromisoformat(data['expires'])
    except (KeyError, ValueError):
        return None, 'Please request a new code.'
    if datetime.utcnow() > expires:
        session.pop('otp', None)
        return None, 'That code has expired. Please request a new one.'

    if data.get('attempts', 0) >= OTP_MAX_ATTEMPTS:
        session.pop('otp', None)
        return None, 'Too many incorrect attempts. Please start again.'

    if _hash_otp((code or '').strip()) != data.get('hash'):
        data['attempts'] = data.get('attempts', 0) + 1
        session['otp'] = data
        session.modified = True
        return None, 'That code is not correct.'

    customer = db.session.get(Customer, data['customer_id'])
    session.pop('otp', None)
    if not customer:
        return None, 'We could not find that account.'
    return customer, None


def _find_customer(identifier):
    identifier = (identifier or '').strip()
    if not identifier:
        return None
    return (Customer.query.filter_by(username=identifier).first()
            or Customer.query.filter_by(mobile=identifier).first()
            or Customer.query.filter_by(reference_id=identifier).first())


def _mask_mobile(mobile):
    m = (mobile or '').strip()
    return ('*' * max(0, len(m) - 4)) + m[-4:] if len(m) > 4 else m


# --------------------------------------------------------------------------- #
#  Forgot / reset password
# --------------------------------------------------------------------------- #
@portal_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        customer = _find_customer(request.form.get('identifier', ''))
        # Do not confirm or deny that an account exists.
        if customer:
            _issue_otp(customer, 'reset')
            flash(f'If that account exists, a code has been sent to '
                  f'{_mask_mobile(customer.mobile)}.', 'success')
            return render_template('customer/reset_password.html',
                                   masked=_mask_mobile(customer.mobile))
        flash('If that account exists, a verification code has been sent to '
              'the registered mobile number.', 'success')
        return render_template('customer/reset_password.html', masked='')

    return render_template('customer/forgot_password.html')


@portal_bp.route('/reset-password', methods=['POST'])
def reset_password():
    customer, error = _verify_otp(request.form.get('otp'), 'reset')
    if error:
        flash(error, 'danger')
        return render_template('customer/reset_password.html', masked='')

    password = request.form.get('password') or ''
    confirm = request.form.get('confirm_password') or ''
    if password != confirm:
        flash('The two passwords do not match.', 'danger')
        return redirect(url_for('portal.forgot_password'))
    if len(password) < 8:
        flash('Please use a password of at least 6 characters.', 'danger')
        return redirect(url_for('portal.forgot_password'))

    customer.set_password(password)
    db.session.commit()
    _log('Portal Password Reset', f'{customer.full_name} reset their password')
    flash('Your password has been changed. Please sign in.', 'success')
    return redirect(url_for('customer_login'))


# --------------------------------------------------------------------------- #
#  Invoices
# --------------------------------------------------------------------------- #
@portal_bp.route('/invoices')
@customer_required
def invoices():
    customer = _me()
    status = request.args.get('status', '')
    query = Invoice.query.filter_by(customer_id=customer.id)

    if status == 'unpaid':
        query = query.filter(Invoice.status.in_(('draft', 'sent', 'overdue')))
    elif status:
        query = query.filter(Invoice.status == status)

    rows = query.order_by(Invoice.issue_date.desc(), Invoice.id.desc()).all()
    if status == 'unpaid':
        rows = [i for i in rows if i.balance > 0]

    return render_template('customer/invoices.html',
                           customer=customer,
                           invoices=rows,
                           status=status,
                           outstanding=_outstanding(customer.id),
                           gateway_ready=cashfree.is_configured(),
                           today=date.today())


@portal_bp.route('/invoices/<int:id>')
@customer_required
def invoice_detail(id):
    invoice = Invoice.query.get_or_404(id)
    if invoice.customer_id != session.get('customer_id'):
        abort(403)
    return render_template('customer/invoice_detail.html',
                           customer=invoice.customer,
                           invoice=invoice,
                           company=Company.query.first(),
                           gateway_ready=cashfree.is_configured(),
                           today=date.today())


@portal_bp.route('/invoices/<int:id>/print')
@customer_required
def invoice_print(id):
    """Print-ready invoice - the browser's Save-as-PDF does the rest."""
    invoice = Invoice.query.get_or_404(id)
    if invoice.customer_id != session.get('customer_id'):
        abort(403)
    return render_template('invoices/summary.html',
                           invoice=invoice,
                           customer=invoice.customer,
                           company=Company.query.first(),
                           today=date.today(),
                           download=True)


# --------------------------------------------------------------------------- #
#  Payments
# --------------------------------------------------------------------------- #
@portal_bp.route('/payments')
@customer_required
def payments():
    customer = _me()
    rows = (Payment.query.filter_by(customer_id=customer.id)
            .filter(Payment.status != 'rejected')
            .order_by(Payment.payment_date.desc(), Payment.id.desc()).all())
    orders = (OnlinePaymentOrder.query.filter_by(customer_id=customer.id)
              .order_by(OnlinePaymentOrder.created_at.desc()).limit(20).all())
    return render_template('customer/payments.html',
                           customer=customer,
                           payments=rows,
                           orders=orders,
                           total_paid=round(sum(float(p.amount or 0)
                                                for p in rows
                                                if p.status == 'approved'), 2),
                           today=date.today())


@portal_bp.route('/payments/<int:id>/receipt')
@customer_required
def payment_receipt(id):
    payment = Payment.query.get_or_404(id)
    if payment.customer_id != session.get('customer_id'):
        abort(403)
    # Payment has no `customer` relationship - only customer_id.
    return render_template('customer/receipt.html',
                           payment=payment,
                           customer=db.session.get(Customer, payment.customer_id),
                           invoice=payment.invoice,
                           company=Company.query.first(),
                           today=date.today())


# --------------------------------------------------------------------------- #
#  Plans / upgrade
# --------------------------------------------------------------------------- #
@portal_bp.route('/plans')
@customer_required
def plans():
    customer = _me()
    active = _active_plan(customer)
    rows = Plan.query.filter_by(is_active=True).order_by(
        Plan.price_monthly).all()
    history = (CustomerPlan.query.filter_by(customer_id=customer.id)
               .order_by(CustomerPlan.id.desc()).all())
    return render_template('customer/plans.html',
                           customer=customer,
                           active_plan=active,
                           plans=rows,
                           history=history,
                           gateway_ready=cashfree.is_configured(),
                           today=date.today())


@portal_bp.route('/plans/<int:plan_id>/request-change', methods=['POST'])
@customer_required
def request_plan_change(plan_id):
    """
    Raise a plan-change invoice. The plan itself switches once the invoice is
    paid - either online (handled here) or at the counter by the office.
    """
    customer = _me()
    plan = db.session.get(Plan, plan_id)
    if not plan or not plan.is_active:
        flash('That plan is no longer available.', 'warning')
        return redirect(url_for('portal.plans'))

    active = _active_plan(customer)
    if active and active.plan_id == plan.id:
        flash('You are already on that plan.', 'info')
        return redirect(url_for('portal.plans'))

    try:
        due_days = int(current_app.config.get('INVOICE_DUE_DAYS', 15) or 15)
    except (TypeError, ValueError):
        due_days = 15

    invoice = Invoice(
        customer_id=customer.id,
        invoice_no=_next_invoice_no(),
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=due_days),
        total_amount=plan.price_monthly,
        tax_amount=0.00,
        caption=f'Plan change - {plan.name}',
        invoice_type='plan',
        status='sent',
        remarks=f'PLAN_CHANGE:{plan.id}',
    )
    db.session.add(invoice)
    db.session.commit()

    _log('Portal Plan Change Request',
         f'{customer.full_name} requested {plan.name} '
         f'(invoice {invoice.invoice_no})')

    flash(f'Invoice {invoice.invoice_no} has been raised for '
          f'{plan.name}. Your plan switches as soon as it is paid.', 'success')
    return redirect(url_for('portal.invoice_detail', id=invoice.id))


# --------------------------------------------------------------------------- #
#  Pay any open invoice online
# --------------------------------------------------------------------------- #
@portal_bp.route('/pay/<int:invoice_id>', methods=['POST'])
@customer_required
def pay_invoice(invoice_id):
    customer = _me()
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.customer_id != customer.id:
        abort(403)

    if not cashfree.is_configured():
        flash('Online payment is not available right now. '
              'Please contact the office.', 'warning')
        return redirect(url_for('portal.invoice_detail', id=invoice.id))

    amount = round(float(invoice.balance or 0), 2)
    if amount <= 0:
        flash('That invoice is already settled.', 'info')
        return redirect(url_for('portal.invoice_detail', id=invoice.id))

    order_id = cashfree.new_order_id()
    order = OnlinePaymentOrder(
        order_id=order_id,
        customer_id=customer.id,
        invoice_id=invoice.id,
        amount=amount,
        status='created',
        note=f'Invoice {invoice.invoice_no}',
    )
    db.session.add(order)
    db.session.commit()

    try:
        data = cashfree.create_order(
            order_id=order_id,
            amount=amount,
            customer_id=customer.id,
            customer_phone=customer.mobile,
            customer_name=customer.full_name,
            customer_email=customer.email or '',
            return_url=url_for('customer_payment_return', _external=True)
            + f'?order_id={order_id}',
            notify_url=url_for('cashfree_webhook', _external=True),
            note=order.note,
        )
    except cashfree.CashfreeError as exc:
        order.status = 'failed'
        order.note = str(exc)[:255]
        db.session.commit()
        current_app.logger.error('Cashfree order failed: %s', exc)
        flash('We could not start the payment. Please try again in a moment.',
              'danger')
        return redirect(url_for('portal.invoice_detail', id=invoice.id))

    order.payment_session_id = data.get('payment_session_id')
    order.cf_order_id = str(data.get('cf_order_id') or '')
    db.session.commit()

    return render_template(
        'customer/checkout.html',
        customer=customer,
        order=order,
        invoice=invoice,
        sdk_url=cashfree.sdk_url(),
        cf_mode=('production' if cashfree.environment() == 'production'
                 else 'sandbox'))


# --------------------------------------------------------------------------- #
#  Notification inbox
# --------------------------------------------------------------------------- #
@portal_bp.route('/notifications')
@customer_required
def notifications():
    from models_api import Notification
    customer = _me()
    rows = (Notification.query.filter_by(customer_id=customer.id)
            .order_by(Notification.created_at.desc()).limit(100).all())
    for row in rows:
        row.mark_read()
    db.session.commit()
    return render_template('customer/notifications.html',
                           customer=customer, notifications=rows)


# --------------------------------------------------------------------------- #
#  Message / usage history
# --------------------------------------------------------------------------- #
@portal_bp.route('/messages')
@customer_required
def messages():
    from models import MessageLog
    customer = _me()
    rows = (MessageLog.query.filter_by(customer_id=customer.id)
            .order_by(MessageLog.created_at.desc()).limit(100).all())
    return render_template('customer/messages.html',
                           customer=customer, messages=rows)



# --------------------------------------------------------------------------- #
#  Renewals - pick a plan and a duration, then pay for it
# --------------------------------------------------------------------------- #
#: Modes a customer may use when entering a payment themselves. Deliberately
#: narrower than the admin list - no "Cash" here, because a customer cannot
#: hand cash to a web form.
PORTAL_PAYMENT_MODES = ('UPI', 'NEFT', 'IMPS', 'RTGS', 'Bank Transfer',
                        'Credit Card', 'Paytm', 'GooglePay', 'PhonePay')

PROOF_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.pdf'}
PROOF_DIR = os.path.join('uploads', 'payment_proofs')


def _due_days():
    try:
        return int(current_app.config.get('INVOICE_DUE_DAYS', 15) or 15)
    except (TypeError, ValueError):
        return 15


def _save_proof(storage):
    """
    Persist an uploaded payment screenshot.

    Returns the path relative to /static, or None when nothing usable was
    uploaded. A bad upload never blocks the payment entry - the customer
    still gets their entry recorded, just without the attachment.
    """
    if storage is None or not getattr(storage, 'filename', ''):
        return None
    name = secure_filename(storage.filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in PROOF_EXTENSIONS:
        return None

    from services.cloudinary import is_enabled, upload
    if is_enabled():
        url = upload(storage, public_id=f'payment-proof-{secrets.token_hex(4)}')
        if url:
            return url
        return None

    unique = f"{datetime.utcnow():%Y%m%d%H%M%S}-{secrets.token_hex(4)}{ext}"
    folder = os.path.join(current_app.root_path, 'static', PROOF_DIR)
    try:
        os.makedirs(folder, exist_ok=True)
        storage.save(os.path.join(folder, unique))
    except OSError:
        current_app.logger.warning('Could not store payment proof %s', name)
        return None
    return f"{PROOF_DIR}/{unique}".replace(os.sep, '/')


@portal_bp.route('/renew')
@customer_required
def renew():
    """
    The renewal screen: keep the current plan or move to another one, for
    1 / 3 / 6 / 12 cycles, with the price worked out for each combination.
    """
    customer = _me()
    cp = renewals.latest_plan(customer.id)
    current = cp.plan if cp else None

    catalogue = Plan.query.filter_by(is_active=True).order_by(
        Plan.speed_mbps, Plan.price_monthly).all()
    # A customer whose plan was retired can still renew onto it.
    if current and current not in catalogue:
        catalogue.insert(0, current)

    quotes = {p.id: {m: renewals.quote(p, m)
                     for m in renewals.DURATION_CHOICES}
              for p in catalogue}

    today = date.today()
    days_left = (cp.end_date - today).days if (cp and cp.end_date) else None

    return render_template('customer/renew.html',
                           customer=customer,
                           customer_plan=cp,
                           current_plan=current,
                           plans=catalogue,
                           quotes=quotes,
                           durations=renewals.DURATION_CHOICES,
                           days_left=days_left,
                           extension_base=renewals.extension_base(cp),
                           pending=RenewalRequest.query.filter_by(
                               customer_id=customer.id, status='pending').first(),
                           gateway_ready=cashfree.is_configured(),
                           today=today)


@portal_bp.route('/renew/confirm', methods=['POST'])
@customer_required
def renew_confirm():
    """Raise the renewal request and its invoice, then offer the ways to pay."""
    customer = _me()

    plan = db.session.get(Plan, request.form.get('plan_id', type=int) or 0)
    if plan is None or not plan.is_active:
        cp_now = renewals.latest_plan(customer.id)
        if not (cp_now and cp_now.plan and plan and cp_now.plan.id == plan.id):
            flash('Please choose a plan to renew.', 'warning')
            return redirect(url_for('portal.renew'))

    months = request.form.get('months', type=int) or 1
    if months not in renewals.DURATION_CHOICES:
        months = 1

    existing = RenewalRequest.query.filter_by(
        customer_id=customer.id, status='pending').first()
    if existing:
        flash('You already have a renewal waiting for confirmation. '
              'Pay for it or cancel it before starting another.', 'info')
        return redirect(url_for('portal.invoice_detail', id=existing.invoice_id)
                        if existing.invoice_id else url_for('portal.renew'))

    req, invoice = renewals.create_request(
        customer, plan, months,
        invoice_no_factory=_next_invoice_no,
        due_days=_due_days(),
        note=(request.form.get('note') or '').strip()[:255] or None)

    _log('Portal Renewal Request',
         f'{customer.full_name} requested {req.plan_label} '
         f'for {req.duration_label} (invoice {invoice.invoice_no})')

    flash(f'Invoice {invoice.invoice_no} raised for Rs.{req.amount:,.2f}. '
          f'Pay it below - your plan is extended once we confirm the payment.',
          'success')
    return redirect(url_for('portal.invoice_detail', id=invoice.id))


@portal_bp.route('/renew/history')
@customer_required
def renewal_history():
    customer = _me()
    return render_template('customer/renew_history.html',
                           customer=customer,
                           requests=renewals.history(customer.id),
                           plan_history=CustomerPlan.query.filter_by(
                               customer_id=customer.id)
                           .order_by(CustomerPlan.id.desc()).all(),
                           today=date.today())


@portal_bp.route('/renewals/<int:id>/cancel', methods=['POST'])
@customer_required
def renewal_cancel(id):
    customer = _me()
    req = RenewalRequest.query.get_or_404(id)
    if req.customer_id != customer.id:
        abort(403)
    if renewals.cancel(req):
        _log('Portal Renewal Cancelled',
             f'{customer.full_name} cancelled renewal #{req.id}')
        flash('Renewal cancelled.', 'info')
    else:
        flash('That renewal has already been dealt with.', 'info')
    return redirect(url_for('portal.renewal_history'))


# --------------------------------------------------------------------------- #
#  Customer payment entry (UPI / bank transfer + UTR + screenshot)
# --------------------------------------------------------------------------- #
@portal_bp.route('/payments/new', methods=['GET', 'POST'])
@customer_required
def payment_new():
    """
    Let a customer record a payment they made outside the gateway.

    The entry is created as *pending* and credits nothing: the invoice
    balance is untouched and no plan moves until an admin has checked the
    UTR against the bank statement and approved it.
    """
    customer = _me()

    open_invoices = [i for i in Invoice.query.filter(
        Invoice.customer_id == customer.id,
        Invoice.status.in_(['draft', 'sent', 'overdue'])).order_by(
        Invoice.issue_date.desc()).all() if i.balance > 0]

    selected_id = request.values.get('invoice_id', type=int)
    selected = next((i for i in open_invoices if i.id == selected_id), None)
    if selected is None and open_invoices:
        selected = open_invoices[0]

    upi_id = _setting_value('upi_id') or _setting_value('company_upi_id')

    ctx = dict(customer=customer,
               invoices=open_invoices,
               selected=selected,
               modes=PORTAL_PAYMENT_MODES,
               upi_id=upi_id,
               outstanding=_outstanding(customer.id),
               today=date.today(),
               form_data={})

    if request.method == 'GET':
        return render_template('customer/payment_new.html', **ctx)

    # ---- validate --------------------------------------------------------- #
    data = {k: (request.form.get(k) or '').strip()
            for k in ('invoice_id', 'amount', 'payment_mode', 'utr',
                      'payment_date', 'remarks')}
    ctx['form_data'] = data
    errors = []

    invoice = next((i for i in open_invoices
                    if str(i.id) == data['invoice_id']), None)
    if invoice is None:
        errors.append('Choose which bill this payment is for.')

    try:
        amount = Decimal(data['amount'] or '0')
    except (InvalidOperation, ValueError):
        amount = Decimal('0')
    if amount <= 0:
        errors.append('Enter the amount you paid.')
    elif invoice is not None and amount > Decimal(str(invoice.balance)) + Decimal('0.01'):
        errors.append(f'That is more than the Rs.{invoice.balance:,.2f} '
                      f'outstanding on this bill.')

    mode = data['payment_mode']
    if mode not in PORTAL_PAYMENT_MODES:
        errors.append('Choose how you paid.')

    utr = data['utr']
    if len(utr) < 6:
        errors.append('Enter the UPI reference / UTR number from your bank.')
    elif Payment.query.filter(Payment.utr == utr,
                              Payment.status != 'rejected').first():
        errors.append('That reference has already been submitted.')

    try:
        paid_on = (datetime.strptime(data['payment_date'], '%Y-%m-%d').date()
                   if data['payment_date'] else date.today())
    except ValueError:
        paid_on = date.today()
    if paid_on > date.today():
        errors.append('The payment date cannot be in the future.')

    if errors:
        for e in errors:
            flash(e, 'danger')
        ctx['selected'] = invoice or selected
        return render_template('customer/payment_new.html', **ctx)

    proof = _save_proof(request.files.get('proof'))

    payment = Payment(
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=amount,
        payment_date=paid_on,
        payment_mode=mode,
        status='pending',              # credits nothing until an admin says so
        source='portal',
        utr=utr[:60],
        gateway_transaction_id=utr[:100],
        proof_file=proof,
        mode_detail=f'Customer entry - {mode}',
        remarks=(data['remarks'] or None),
        authorized_at=None,
    )
    db.session.add(payment)
    db.session.flush()

    # Tie it to the renewal it is paying for, so approving the payment
    # extends the plan in one step.
    req = renewals.open_request_for_invoice(invoice.id)
    if req is not None and req.payment_id is None:
        req.payment_id = payment.id
    db.session.commit()

    _log('Portal Payment Entry',
         f'{customer.full_name} submitted Rs.{amount} via {mode} '
         f'(UTR {utr}) against {invoice.invoice_no}')

    try:
        messaging.send_template(customer, 'payment_submitted',
                                invoice=invoice, payment=payment)
    except Exception:                                    # noqa: BLE001
        pass

    flash('Payment submitted. We will confirm it against our bank statement '
          'and update your account - usually within a few hours.', 'success')
    return redirect(url_for('portal.payments'))


def _setting_value(key):
    try:
        from models_ext import Setting
        row = Setting.query.filter_by(key=key).first()
        return (row.value or '').strip() if row else ''
    except Exception:                                    # noqa: BLE001
        return ''


def register(app):
    app.register_blueprint(portal_bp)

    @app.context_processor
    def _portal_globals():
        """Unread-alert badge for the portal nav bar."""
        if not session.get('customer_id'):
            return {}
        try:
            from models_api import Notification
            unread = Notification.query.filter_by(
                customer_id=session['customer_id'], is_read=False).count()
        except Exception:
            unread = 0
        return {'unread_count': unread}

    return portal_bp
