"""
blueprints/api/customer_billing.py
==================================

Everything the redesigned customer-detail screen needs beyond the plain
record: the addon-invoice flow, payment entry, the per-tab histories (SMS log,
customer log, inventory, plan history, payment ledger), plan editing, and
receipt delivery.

Split out from resources.py deliberately. resources.py is generic CRUD over
the core tables; this file is the customer *workspace*, where the rules live
(what a discount may do to a bill, how a payment is spread over bills, which
message goes out on renewal). Keeping them apart means a change to the
addon-invoice rules cannot accidentally alter the customer list endpoint.

As elsewhere in this package, helpers that live in app.py are imported inside
the functions: app.py imports this blueprint at start-up, so a module-level
import would be circular.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, Response, request
from sqlalchemy import or_

from models import (AuditLog, Customer, CustomerPlan, DiscountReason, Invoice,
                    InventoryAssignment, MessageLog, Payment, Plan, db)

from .serializers import customer_plan_dict, invoice_dict, payment_dict
from .utils import (admin_required, body, current_staff_id, fail, iso, money,
                    ok, paginate, staff_required)

bp = Blueprint('api_customer_billing', __name__)

#: Modes the Addon Invoice screen offers, in the order the live CRM lists them.
PAYMENT_MODES = ('Cash', 'Cheque', 'Online Transfer', 'Credit Card', 'Paytm',
                 'GooglePay', 'PhonePay', 'Bank Transfer')

#: Modes where a bank name / transaction reference is expected.
REFERENCED_MODES = {'Cheque', 'Online Transfer', 'Credit Card', 'Paytm',
                    'GooglePay', 'PhonePay', 'Bank Transfer'}


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _customer_or_404(cid):
    customer = db.session.get(Customer, cid)
    return customer, (None if customer else fail('not_found', 404))


def _audit(action, detail, customer_id=None):
    """Best-effort audit write; a logging failure must not fail the action."""
    try:
        from app import log_audit
        log_audit(action, detail)
    except Exception:
        pass

    # log_audit() predates AuditLog.customer_id, so the row it just wrote is
    # not attributable to a customer. Stamp the most recent one rather than
    # writing a duplicate.
    if customer_id:
        try:
            row = AuditLog.query.order_by(AuditLog.id.desc()).first()
            if row and row.action == action and row.customer_id is None:
                row.customer_id = customer_id
                db.session.commit()
        except Exception:
            db.session.rollback()


def _decimal(value, field, default=None):
    """Parse money from JSON. Returns (value, error-response)."""
    if value in (None, ''):
        return default, None
    try:
        return Decimal(str(value)), None
    except (InvalidOperation, TypeError, ValueError):
        return None, fail('invalid_number', 400, detail=f'{field} is not a number.')


def _parse_date(value, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _next_invoice_no():
    try:
        from app import generate_invoice_no
        return generate_invoice_no()
    except Exception:
        seq = (db.session.query(db.func.count(Invoice.id)).scalar() or 0) + 1
        return f'INV-{date.today():%y%m}-{seq:05d}'


def _mode_detail(data):
    """Fold the conditional bank fields into Payment.mode_detail.

    The UI shows Bank Name / Transaction No. / Date only for non-cash modes.
    They are one column in the schema, so join them into a readable string
    rather than inventing three columns used by one screen.
    """
    parts = [data.get('bank_name'), data.get('transaction_no'),
             data.get('transaction_date')]
    return ', '.join(str(p).strip() for p in parts if str(p or '').strip()) or None


# --------------------------------------------------------------------------- #
#  Addon invoice
# --------------------------------------------------------------------------- #
@bp.get('/billing/options')
@staff_required
def billing_options():
    """Everything the Addon Invoice form needs to populate its dropdowns."""
    reasons = DiscountReason.query.filter(
        DiscountReason.is_active.is_(True)).order_by(DiscountReason.name).all()
    return ok({
        'payment_modes': list(PAYMENT_MODES),
        'referenced_modes': sorted(REFERENCED_MODES),
        'discount_reasons': [{
            'id': r.id,
            'name': r.name,
            'default_amount': money(r.default_amount),
            'default_percent': money(r.default_percent),
            'description': r.description or '',
        } for r in reasons],
    })


@bp.post('/customers/<int:cid>/addon-invoice')
@staff_required
def addon_invoice_create(cid):
    """
    Raise an extra bill on the account - installation, a shifting charge, a
    replacement router - and optionally settle it in the same step.

    The discount is validated against the bill, not merely stored: a discount
    larger than the amount would produce a negative invoice, which the ledger
    has no way to represent.
    """
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    data = body()

    amount, error = _decimal(data.get('amount'), 'Amount')
    if error:
        return error
    if amount is None or amount <= 0:
        return fail('amount_required', 400,
                    detail='Enter the amount to bill.')

    discount, error = _decimal(data.get('discount_amount'), 'Discount',
                               default=Decimal('0'))
    if error:
        return error
    discount = abs(discount or Decimal('0'))
    if discount > amount:
        return fail('discount_exceeds_amount', 400,
                    detail='The discount cannot be more than the bill amount.')

    discount_reason = (data.get('discount_reason') or '').strip() or None
    if discount > 0 and not discount_reason:
        return fail('discount_reason_required', 400,
                    detail='Pick a discount type from Discount Master.')

    issue_date = _parse_date(data.get('invoice_date'), date.today())
    if issue_date is None:
        return fail('invalid_invoice_date', 400)

    caption = (data.get('caption') or '').strip() or 'Addon charge'

    invoice = Invoice(
        customer_id=cid,
        invoice_no=_next_invoice_no(),
        issue_date=issue_date,
        due_date=issue_date + timedelta(days=int(data.get('due_days') or 15)),
        total_amount=amount,
        tax_amount=Decimal('0.00'),
        discount_amount=discount,
        discount_reason=discount_reason,
        caption=caption,
        invoice_type='addon',
        remarks=(data.get('remark') or '').strip() or None,
        status='sent',
    )
    db.session.add(invoice)
    db.session.commit()

    _audit('Addon Invoice',
           f'Raised {invoice.invoice_no} ({caption}) for {customer.full_name}: '
           f'{amount} less {discount} discount', customer_id=cid)

    return ok({'invoice': invoice_dict(invoice, detail=True)}), 201


# --------------------------------------------------------------------------- #
#  Pending invoices, and settling them
#
#  Raising a bill and taking the money are two separate acts, and the screens
#  now follow suit. An addon invoice used to collect payment in the same
#  submit, which meant a mistake could not be undone (the bill was already
#  settled) and a customer paying one amount against a renewal AND an addon
#  had to be entered as two payments against two screens.
#
#  So: invoices are raised on their own, can be deleted while nothing has been
#  paid against them, and one payment entry settles however many are
#  outstanding - with the discount spread across them.
# --------------------------------------------------------------------------- #
@bp.get('/customers/<int:cid>/pending-invoices')
@staff_required
def pending_invoices(cid):
    """Everything this customer still owes, with the combined total."""
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    rows = (Invoice.query
            .filter(Invoice.customer_id == cid,
                    Invoice.status.in_(('draft', 'sent', 'overdue')))
            .order_by(Invoice.issue_date.asc(), Invoice.id.asc())
            .all())

    entries = []
    for invoice in rows:
        balance = invoice.balance
        if balance <= 0:
            continue
        entries.append({
            'id': invoice.id,
            'invoice_no': invoice.invoice_no,
            'caption': invoice.display_caption,
            'invoice_type': invoice.invoice_type or 'plan',
            'issue_date': iso(invoice.issue_date),
            'due_date': iso(invoice.due_date),
            'period_start': iso(invoice.period_start),
            'period_end': iso(invoice.period_end),
            'total_amount': money(invoice.total_amount),
            'discount_amount': money(invoice.discount_amount),
            'paid_amount': money(invoice.paid_amount),
            'balance': money(balance),
            'status': invoice.status,
            # An invoice nobody has paid against can still be withdrawn. Once
            # money is attached, deleting it would erase a payment record.
            'can_delete': invoice.paid_amount <= 0,
        })

    reasons = DiscountReason.query.filter(
        DiscountReason.is_active.is_(True)).order_by(DiscountReason.name).all()

    return ok({
        'invoices': entries,
        'total_outstanding': round(sum(e['balance'] for e in entries), 2),
        'count': len(entries),
        'payment_modes': list(PAYMENT_MODES),
        'referenced_modes': sorted(REFERENCED_MODES),
        'discount_reasons': [{'id': r.id, 'name': r.name} for r in reasons],
    })


@bp.delete('/invoices/<int:iid>')
@staff_required
def invoice_delete(iid):
    """
    Withdraw an invoice raised in error.

    Only while nothing has been paid against it. Deleting an invoice that has
    money attached would take the payment record with it, which is how a
    ledger stops adding up.
    """
    invoice = db.session.get(Invoice, iid)
    if invoice is None:
        return fail('not_found', 404)

    if invoice.paid_amount > 0:
        return fail('invoice_has_payments', 409,
                    detail=f'{money(invoice.paid_amount):.2f} has been paid '
                           f'against {invoice.invoice_no}. Cancel it instead '
                           f'of deleting it, so the payment record survives.')

    number = invoice.invoice_no
    customer_id = invoice.customer_id

    try:
        from models_ext import InvoiceItem
        InvoiceItem.query.filter_by(invoice_id=iid).delete()
    except Exception:
        pass

    db.session.delete(invoice)
    db.session.commit()

    _audit('Delete Invoice', f'Deleted {number}', customer_id=customer_id)
    return ok({'status': 'deleted', 'invoice_no': number})


@bp.put('/invoices/<int:iid>')
@staff_required
def invoice_update(iid):
    """
    Correct a bill: the amount, and the period it covers.

    Only those three fields. An invoice number, a customer or a raised date
    that can be edited after the fact is not a record of anything - and the
    period matters because it is what stops the billing run raising a second
    bill for the same month.

    Editing is refused once money is attached. Changing the amount underneath
    a payment silently turns a settled bill into an over- or under-payment
    with no trace of why, so a part-paid invoice has to be settled or
    cancelled instead.
    """
    invoice = db.session.get(Invoice, iid)
    if invoice is None:
        return fail('not_found', 404)

    if invoice.paid_amount > 0:
        return fail('invoice_has_payments', 409,
                    detail=f'{money(invoice.paid_amount):.2f} has been paid '
                           f'against {invoice.invoice_no}. Editing it now '
                           f'would change what was owed after the fact.')

    if invoice.status == 'cancelled':
        return fail('invoice_cancelled', 409,
                    detail='A cancelled invoice cannot be edited.')

    data = body()
    changes = []

    if data.get('amount') not in (None, ''):
        amount, error = _decimal(data.get('amount'), 'Amount')
        if error:
            return error
        if amount is None or amount <= 0:
            return fail('invalid_amount', 400,
                        detail='The amount must be more than zero.')
        discount = Decimal(str(invoice.discount_amount or 0))
        if discount > amount:
            return fail('discount_exceeds_amount', 400,
                        detail=f'This bill carries a {discount} discount, so '
                               f'the amount cannot be less than that.')
        if Decimal(str(invoice.total_amount)) != amount:
            changes.append(f'amount {invoice.total_amount} -> {amount}')
            invoice.total_amount = amount

    start = invoice.period_start
    end = invoice.period_end

    if data.get('period_start') not in (None, ''):
        start = _parse_date(data['period_start'])
        if start is None:
            return fail('invalid_period_start', 400,
                        detail='Use YYYY-MM-DD for the renew date.')

    if data.get('period_end') not in (None, ''):
        end = _parse_date(data['period_end'])
        if end is None:
            return fail('invalid_period_end', 400,
                        detail='Use YYYY-MM-DD for the expiry date.')

    if start and end and end <= start:
        return fail('period_end_before_start', 400,
                    detail='The expiry date must be after the renew date.')

    if start != invoice.period_start or end != invoice.period_end:
        changes.append(f'period {iso(invoice.period_start)}..'
                       f'{iso(invoice.period_end)} -> {iso(start)}..{iso(end)}')
        invoice.period_start = start
        invoice.period_end = end

        # The plan's expiry is what the customer actually has, so an edit to
        # the billed period has to move it too - otherwise the bill says one
        # thing and the connection does another.
        if data.get('sync_plan', True) and end and invoice.customer_plan_id:
            plan_row = db.session.get(CustomerPlan, invoice.customer_plan_id)
            if plan_row is not None and plan_row.status == 'active':
                if plan_row.end_date != end:
                    changes.append(f'plan expiry {iso(plan_row.end_date)} '
                                   f'-> {iso(end)}')
                    plan_row.end_date = end
                if start:
                    plan_row.start_date = start

    if not changes:
        return ok({'invoice': invoice_dict(invoice, detail=True),
                   'changed': []})

    db.session.commit()
    _audit('Edit Invoice', f'{invoice.invoice_no}: ' + '; '.join(changes),
           customer_id=invoice.customer_id)

    return ok({'invoice': invoice_dict(invoice, detail=True),
               'changed': changes})


@bp.post('/customers/<int:cid>/payments')
@staff_required
def payment_entry(cid):
    """
    Record one payment against one or more outstanding invoices.

    This is the counter transaction: the customer owes for a renewal and an
    addon, hands over a single amount, and gets one receipt. The money is
    applied oldest invoice first, and any discount is spread the same way, so
    the older bill is cleared before the newer one starts to move.
    """
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    data = body()
    ids = data.get('invoice_ids') or []
    if not isinstance(ids, list) or not ids:
        return fail('invoice_required', 400,
                    detail='Choose at least one invoice to pay.')

    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return fail('invalid_invoice_ids', 400)

    invoices = (Invoice.query
                .filter(Invoice.id.in_(ids), Invoice.customer_id == cid)
                .order_by(Invoice.issue_date.asc(), Invoice.id.asc())
                .all())
    if len(invoices) != len(set(ids)):
        return fail('invoice_not_found', 404,
                    detail='One of those invoices does not belong to this '
                           'customer.')

    outstanding = float(round(sum(i.balance for i in invoices)))
    if outstanding <= 0:
        return fail('nothing_to_pay', 400,
                    detail='Those invoices are already settled.')

    discount, error = _decimal(data.get('discount_amount'), 'Discount',
                               default=Decimal('0'))
    if error:
        return error
    discount = abs(discount or Decimal('0'))
    if discount > Decimal(str(outstanding)):
        return fail('discount_exceeds_outstanding', 400,
                    detail=f'The discount cannot be more than the '
                           f'{outstanding:.2f} outstanding.')

    discount_reason = (data.get('discount_reason') or '').strip() or None
    if discount > 0 and not discount_reason:
        return fail('discount_reason_required', 400,
                    detail='Pick a discount type from Discount Master.')

    amount, error = _decimal(data.get('amount'), 'Amount')
    if error:
        return error
    if amount is None or amount <= 0:
        return fail('amount_required', 400,
                    detail='Enter the amount received.')

    payable = Decimal(str(outstanding)) - discount
    if amount > payable:
        return fail('amount_exceeds_due', 400,
                    detail=f'Only {payable:.2f} is due after the discount. '
                           f'Reduce the amount or raise another invoice.')

    mode = (data.get('payment_mode') or '').strip()
    if mode not in PAYMENT_MODES:
        return fail('invalid_payment_mode', 400,
                    detail=f"Choose one of: {', '.join(PAYMENT_MODES)}.")

    payment_date = _parse_date(data.get('payment_date'), date.today())
    if payment_date is None:
        return fail('invalid_payment_date', 400)

    receipt_no = (data.get('book_receipt_no') or '').strip() or None
    remark = (data.get('remark') or '').strip() or None

    # Apply oldest first: discount, then cash.
    remaining_discount = discount
    remaining_amount = amount
    created, settled = [], []

    for invoice in invoices:
        if remaining_discount <= 0 and remaining_amount <= 0:
            break

        balance = Decimal(str(invoice.balance))
        if balance <= 0:
            continue

        share_discount = min(remaining_discount, balance)
        if share_discount > 0:
            invoice.discount_amount = (Decimal(str(invoice.discount_amount or 0))
                                       + share_discount)
            if discount_reason:
                invoice.discount_reason = discount_reason[:100]
            remaining_discount -= share_discount
            balance -= share_discount

        share_amount = min(remaining_amount, balance)
        if share_amount > 0:
            payment = Payment(
                invoice_id=invoice.id,
                customer_id=cid,
                amount=share_amount,
                discount_amount=share_discount,
                discount_reason=discount_reason,
                payment_date=payment_date,
                payment_mode=mode,
                mode_detail=_mode_detail(data),
                book_receipt_no=receipt_no,
                remarks=remark,
                status='approved',
                source='admin',
                received_by_user_id=current_staff_id(),
            )
            db.session.add(payment)
            db.session.flush()
            created.append(payment)
            remaining_amount -= share_amount

    # Status is decided in a second pass, AFTER every payment row exists.
    # Checking inside the loop read a stale paid_amount - the invoice was
    # fully settled but kept its 'sent' status, so it stayed in the pending
    # list and carried on chasing a customer who had already paid.
    db.session.flush()
    for invoice in invoices:
        db.session.refresh(invoice)
        if invoice.balance <= 0 and invoice.status != 'cancelled':
            invoice.status = 'paid'
            settled.append(invoice.invoice_no)

    db.session.commit()

    _audit('Payment Entry',
           f'{customer.full_name}: {amount} by {mode} against '
           f'{", ".join(i.invoice_no for i in invoices)}'
           + (f' (discount {discount})' if discount else ''),
           customer_id=cid)

    return ok({
        'payments': [payment_dict(p) for p in created],
        'payment_ids': [p.id for p in created],
        'amount': money(amount),
        'discount': money(discount),
        'settled': settled,
        'invoices': [invoice_dict(i) for i in invoices],
        'remaining_due': float(round(sum(i.balance for i in invoices))),
        # Payment.receipt_no already prefers the book number the operator
        # typed, so this is the same answer the receipt PDF will print.
        'receipt_no': created[0].receipt_no if created else '',
    }), 201


# --------------------------------------------------------------------------- #
#  Plan editing
# --------------------------------------------------------------------------- #
@bp.put('/customer-plans/<int:pid>')
@admin_required
def customer_plan_update(pid):
    """
    The "Edit Customer Plan" modal: total price, start date, end date.

    The price is written to this customer's plan row, never to the Plan master
    - editing the master would silently reprice every other customer on it.
    Unpaid invoices already raised at the old figure are corrected too, so the
    customer is not chasing one number while the system shows another.
    """
    customer_plan = db.session.get(CustomerPlan, pid)
    if not customer_plan:
        return fail('not_found', 404)

    data = body()

    start_date = customer_plan.start_date
    if data.get('start_date'):
        start_date = _parse_date(data['start_date'])
        if start_date is None:
            return fail('invalid_start_date', 400)

    end_date = customer_plan.end_date
    if data.get('end_date'):
        end_date = _parse_date(data['end_date'])
        if end_date is None:
            return fail('invalid_end_date', 400)

    if end_date < start_date:
        return fail('end_date_before_start_date', 400,
                    detail='The end date cannot be before the start date.')

    customer_plan.start_date = start_date
    customer_plan.end_date = end_date

    if 'auto_renew' in data:
        customer_plan.auto_renew = bool(data['auto_renew'])
    if 'status' in data and data['status'] in ('active', 'expired',
                                               'cancelled', 'terminated'):
        customer_plan.status = data['status']

    repriced = []
    if data.get('total_price') not in (None, ''):
        price, error = _decimal(data['total_price'], 'Total price')
        if error:
            return error
        if price < 0:
            return fail('invalid_total_price', 400,
                        detail='The price cannot be negative.')

        # The agreed price for this customer from now on.
        customer_plan.price = price

        unpaid = Invoice.query.filter(
            Invoice.customer_plan_id == pid,
            Invoice.status.in_(('draft', 'sent', 'overdue'))).all()
        for invoice in unpaid:
            invoice.total_amount = price
            repriced.append(invoice.invoice_no)

    db.session.commit()

    _audit('Edit Customer Plan',
           f'Plan {pid}: {start_date} to {end_date}'
           + (f", repriced {', '.join(repriced)}" if repriced else ''),
           customer_id=customer_plan.customer_id)

    return ok({
        'plan': customer_plan_dict(customer_plan),
        'repriced_invoices': repriced,
        'note': ('The new price was applied to this customer\'s unpaid '
                 'invoices. Paid invoices are left as issued.')
        if repriced else '',
    })


# --------------------------------------------------------------------------- #
#  Wallet
# --------------------------------------------------------------------------- #
# NOTE: the wallet was removed at the owner's request. Overpayment is no
# longer parked as credit - the payment screen refuses to take more than is
# actually due, so there is nothing to hold. The wallet_balance column and
# the wallet_entries table are left in place rather than dropped, since
# dropping them would destroy historic rows; they are simply no longer
# written to or read.


@bp.get('/customers/<int:cid>/messages')
@staff_required
def message_log(cid):
    """The SMS Log tab: every WhatsApp / SMS attempt for this customer."""
    _, missing = _customer_or_404(cid)
    if missing:
        return missing

    query = MessageLog.query.filter_by(customer_id=cid).order_by(
        MessageLog.created_at.desc(), MessageLog.id.desc())
    rows, meta = paginate(query, default_per_page=50)

    return ok([{
        'id': r.id,
        'phone': r.phone or '',
        'channel': r.channel or '',
        'template_type': r.template_type or '',
        'body': r.body or '',
        'status': r.status or '',
        'error': r.error or '',
        'created_at': iso(r.created_at),
    } for r in rows], meta=meta)


@bp.get('/customers/<int:cid>/logs')
@staff_required
def customer_log(cid):
    """
    The Customer Log tab: who did what to this account.

    Rows written before AuditLog.customer_id existed have it NULL, so they are
    matched on the customer's name appearing in the detail text. That is a
    fallback for history, not the primary path - anything written from here on
    carries the id.
    """
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    name = customer.full_name or ''
    conditions = [AuditLog.customer_id == cid]
    if name.strip():
        conditions.append(AuditLog.details.ilike(f'%{name}%'))
    for handle in (customer.username, customer.reference_id):
        if handle:
            conditions.append(AuditLog.details.ilike(f'%{handle}%'))

    query = AuditLog.query.filter(or_(*conditions)).order_by(
        AuditLog.created_at.desc(), AuditLog.id.desc())
    rows, meta = paginate(query, default_per_page=50)

    return ok([{
        'id': r.id,
        'action': r.action or '',
        'details': r.details or '',
        'user': r.user.full_name if r.user else '',
        'ip_address': r.ip_address or '',
        'created_at': iso(r.created_at),
    } for r in rows], meta=meta)


@bp.get('/customers/<int:cid>/inventory')
@staff_required
def customer_inventory(cid):
    """The Inventory tab: hardware issued to this customer."""
    _, missing = _customer_or_404(cid)
    if missing:
        return missing

    rows = InventoryAssignment.query.filter_by(customer_id=cid).order_by(
        InventoryAssignment.assigned_date.desc(),
        InventoryAssignment.id.desc()).all()

    return ok([{
        'id': r.id,
        'product_id': r.product_id,
        'product': r.product.name if r.product else '',
        'sku': getattr(r.product, 'sku', '') or '' if r.product else '',
        'serial_number': r.serial_number or '',
        'assigned_date': iso(r.assigned_date),
        'status': r.status or '',
    } for r in rows])


@bp.get('/customers/<int:cid>/plan-history')
@staff_required
def plan_history(cid):
    """The Plan History tab: every plan this account has ever been on."""
    _, missing = _customer_or_404(cid)
    if missing:
        return missing

    rows = CustomerPlan.query.filter_by(customer_id=cid).order_by(
        CustomerPlan.start_date.desc(), CustomerPlan.id.desc()).all()
    return ok([customer_plan_dict(cp) for cp in rows])


@bp.get('/customers/<int:cid>/ledger')
@staff_required
def payment_ledger(cid):
    """
    The Payment Ledger tab: invoices and payments interleaved, oldest first,
    with a running balance.

    The Jinja version sorted newest-first and showed no running total, which
    made it impossible to see how the account arrived at its current due. This
    computes the balance forward and then hands back the rows newest-first for
    display, so the top row's balance is the one that matters.

    Historic wallet movements still post as ledger lines. The wallet itself
    was removed, but rows written while it existed are real money that moved,
    and a statement that quietly dropped them would not reconcile. A credit to
    the wallet was money leaving this ledger, so it posts as a debit; a
    drawdown posts as a credit.
    """
    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    # inv.display_caption reads inv.payments (for the paid mode) and falls
    # back to customer_plan.plan.name - three lazy loads per invoice on a tab
    # that is opened for every payment conversation. Loaded up front instead.
    from sqlalchemy.orm import selectinload
    invoices = Invoice.query.filter_by(customer_id=cid).options(
        selectinload(Invoice.payments),
        selectinload(Invoice.customer_plan).selectinload(CustomerPlan.plan)).all()
    payments = Payment.query.filter_by(customer_id=cid).all()
    # Only ever historic now - nothing writes wallet entries any more.
    try:
        from models import WalletEntry
        wallet = WalletEntry.query.filter_by(customer_id=cid).all()
    except Exception:
        wallet = []

    events = []
    for inv in invoices:
        if inv.status == 'cancelled':
            continue
        events.append({
            'date': inv.issue_date,
            'sort': (inv.issue_date, 0, inv.id),
            'type': 'invoice',
            'reference': inv.invoice_no,
            'description': inv.display_caption,
            'debit': money(inv.net_amount),
            'credit': 0.0,
            'invoice_id': inv.id,
            'payment_id': None,
            'status': inv.status,
        })
    for pay in payments:
        if pay.status == 'rejected':
            continue
        events.append({
            'date': pay.payment_date,
            'sort': (pay.payment_date, 1, pay.id),
            'type': 'payment',
            'reference': pay.receipt_no,
            'description': f'{pay.payment_mode or "Payment"}'
                           + (f' - {pay.reference}' if pay.reference else ''),
            'debit': 0.0,
            'credit': money(pay.amount),
            'invoice_id': pay.invoice_id,
            'payment_id': pay.id,
            'status': pay.status,
        })

    for entry in wallet:
        amount = money(entry.amount)

        # Exactly one kind of wallet movement belongs on this ledger: a credit
        # funded by a payment that is already posted above. That is the
        # overpayment case - the payment credits its full face value, but part
        # of it went to the wallet rather than against the bill, so it has to
        # come back off or the account reads as permanently in credit.
        #
        # Nothing else posts here:
        #   - a drawdown that settles an invoice raises its own Payment, which
        #     is already on the ledger; posting the wallet leg too would credit
        #     the same money twice;
        #   - a withdrawal or refund takes money out of the business entirely;
        #   - a manual credit never touched an invoice.
        # All of them still appear on the Wallet tab, which is their home.
        if not (amount > 0 and entry.payment_id):
            continue

        entry_date = entry.created_at.date() if entry.created_at else date.today()
        events.append({
            'date': entry_date,
            # Sorted after the payment that funded it, so the running balance
            # never passes through a figure that never existed.
            'sort': (entry_date, 2, entry.id),
            'type': 'wallet',
            'reference': f'W{entry.id}',
            'description': entry.reason or 'Credited to wallet',
            'debit': amount,
            'credit': 0.0,
            'invoice_id': entry.invoice_id,
            'payment_id': entry.payment_id,
            'status': entry.kind,
        })

    events.sort(key=lambda e: e['sort'])

    balance = 0.0
    for event in events:
        balance = round(balance + event['debit'] - event['credit'], 2)
        event['balance'] = balance
        event['date'] = iso(event['date'])
        event.pop('sort')

    events.reverse()

    total_debit = round(sum(e['debit'] for e in events), 2)
    total_credit = round(sum(e['credit'] for e in events), 2)

    return ok({
        # The standalone ledger screen is reachable without going through the
        # customer record, so it has nothing else to print a name from.
        'customer': {
            'id': customer.id,
            'full_name': customer.full_name,
            'account_id': f'C{customer.id}',
            'mobile': customer.mobile or '',
            'zone': customer.zone or '',
            'username': customer.username or '',
        },
        'entries': events,
        # Sent explicitly rather than left to the caller to read off the last
        # row: the rows come back newest-first, so "the last one" is the
        # OLDEST balance - a subtle way to print a badly wrong figure.
        'closing_balance': balance,
        'total_debit': total_debit,
        'total_credit': total_credit,
        # Historic only. The wallet was switched off (see the note above the
        # message log), but accounts that held credit before that still hold
        # it in the column, and the wallet rows are still listed among the
        # entries above. Reporting the figure keeps that money visible to
        # whoever has to settle the account; both screens that read this hide
        # the line when it is zero, which is every account opened since.
        'wallet_balance': money(getattr(customer, 'wallet_balance', 0) or 0),
    })


# --------------------------------------------------------------------------- #
#  KYC documents
# --------------------------------------------------------------------------- #
#: Extensions accepted for a KYC upload. An allow-list, not a block-list:
#: anything not named here is refused, so a renamed executable cannot be
#: written into a directory the web server hands back over HTTP.
ALLOWED_DOC_EXT = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.gif'}
MAX_DOC_BYTES = 8 * 1024 * 1024

#: form field -> (Customer column, matching type column)
DOC_FIELDS = {
    'reg_form': ('reg_form_file', None),
    'photo': ('photo_file', None),
    'address_proof': ('address_proof_file', 'address_proof_type'),
    'id_proof': ('id_proof_file', 'id_proof_type'),
}


@bp.post('/customers/<int:cid>/documents')
@staff_required
def customer_documents_upload(cid):
    """
    Multipart KYC upload: reg_form, photo, address_proof, id_proof.

    Any subset may be sent, so this doubles as "replace just the ID proof"
    without the caller having to re-post the other three. The matching
    ``*_type`` value travels as a normal form field alongside the file.
    """
    import os
    import secrets

    from flask import current_app
    from werkzeug.utils import secure_filename

    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    folder = os.path.join(current_app.root_path, 'static', 'uploads', 'kyc')
    os.makedirs(folder, exist_ok=True)

    saved, rejected = [], []

    for field, (column, type_column) in DOC_FIELDS.items():
        # The type can be updated on its own, without re-uploading the file.
        if type_column and field + '_type' in request.form:
            setattr(customer, type_column,
                    (request.form.get(field + '_type') or '').strip() or None)

        upload = request.files.get(field)
        if not upload or not upload.filename:
            continue

        original = secure_filename(upload.filename)
        extension = os.path.splitext(original)[1].lower()
        if extension not in ALLOWED_DOC_EXT:
            rejected.append({'field': field, 'filename': original,
                             'reason': f'{extension or "no extension"} is not '
                                       'an accepted file type. Use PDF or an image.'})
            continue

        upload.stream.seek(0, os.SEEK_END)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > MAX_DOC_BYTES:
            rejected.append({'field': field, 'filename': original,
                             'reason': 'Larger than 8 MB.'})
            continue
        if size == 0:
            rejected.append({'field': field, 'filename': original,
                             'reason': 'The file is empty.'})
            continue

        # Prefix with the customer and a random token: two customers uploading
        # "aadhaar.jpg" must not overwrite one another.
        stored = f'c{cid}-{field}-{secrets.token_hex(4)}{extension}'
        try:
            upload.save(os.path.join(folder, stored))
        except OSError as exc:
            rejected.append({'field': field, 'filename': original,
                             'reason': f'Could not be saved: {str(exc)[:120]}'})
            continue

        previous = getattr(customer, column, None)
        setattr(customer, column, stored)
        saved.append({'field': field, 'filename': stored})

        # Replaced files are removed so the uploads folder does not grow a
        # copy of every superseded proof.
        if previous and previous != stored:
            try:
                os.remove(os.path.join(folder, os.path.basename(previous)))
            except OSError:
                pass

    db.session.commit()

    if saved:
        _audit('Upload KYC',
               f"{', '.join(d['field'] for d in saved)} for {customer.full_name}",
               customer_id=cid)

    from .serializers import customer_documents
    return ok({
        'documents': customer_documents(customer),
        'saved': saved,
        'rejected': rejected,
    })


@bp.delete('/customers/<int:cid>/documents/<field>')
@admin_required
def customer_document_delete(cid, field):
    """Remove one KYC file. Admin-only: proofs are compliance records."""
    import os

    from flask import current_app

    customer, missing = _customer_or_404(cid)
    if missing:
        return missing

    if field not in DOC_FIELDS:
        return fail('unknown_document', 404,
                    detail=f"Expected one of: {', '.join(DOC_FIELDS)}.")

    column, _ = DOC_FIELDS[field]
    filename = getattr(customer, column, None)
    if not filename:
        return fail('nothing_to_delete', 404)

    setattr(customer, column, None)
    db.session.commit()

    try:
        os.remove(os.path.join(current_app.root_path, 'static', 'uploads',
                               'kyc', os.path.basename(filename)))
    except OSError:
        pass

    _audit('Delete KYC', f'{field} for {customer.full_name}', customer_id=cid)

    from .serializers import customer_documents
    return ok({'documents': customer_documents(customer)})


# --------------------------------------------------------------------------- #
#  Receipts
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  Reversing a payment
#
#  Two different things, deliberately kept apart.
#
#  CANCEL is "this entry should never have existed" - a duplicate, the wrong
#  customer, a typo in the amount. The money never really came in, so the
#  entry stops counting and the invoice goes back to owing what it owed.
#
#  SALES RETURN is "the money came in and we are giving it back" - a refund
#  after a downgrade, a service that was never delivered. The original receipt
#  stands, because it happened; a second, negative entry records the refund
#  beside it. The customer's ledger then shows both halves, which is what an
#  auditor and an argumentative customer both need to see.
#
#  Recording a refund by deleting the receipt would leave a customer who paid
#  ₹1,000 and was refunded ₹400 looking identical to one who only ever paid
#  ₹600. They are not the same, and only one of them has a reason to trust us.
# --------------------------------------------------------------------------- #
def _reopen_invoice(invoice):
    """A bill marked paid that is owed again is no longer paid."""
    if invoice and invoice.status == 'paid' and invoice.balance > 0:
        invoice.status = 'sent'


@bp.post('/payments/<int:pid>/cancel')
@admin_required
def payment_cancel(pid):
    """Void a payment entry that should not have been made."""
    payment = db.session.get(Payment, pid)
    if not payment:
        return fail('not_found', 404)

    if payment.status == 'rejected':
        return fail('already_cancelled', 409,
                    detail='That entry has already been cancelled.')

    data = body()
    reason = (data.get('reason') or '').strip()
    if not reason:
        return fail('reason_required', 400,
                    detail='Say why this entry is being cancelled - it stays '
                           'on the record and somebody will read it later.')

    staff_id = current_staff_id()
    payment.status = 'rejected'
    payment.authorized_at = datetime.utcnow()
    payment.authorized_by_user_id = staff_id
    payment.remarks = ((payment.remarks or '') + f'\nCancelled: {reason}').strip()

    invoice = payment.invoice
    db.session.flush()
    _reopen_invoice(invoice)
    db.session.commit()

    _audit('Cancel Payment',
           f'Rs.{int(round(float(payment.amount))):,} receipt {payment.receipt_no} '
           f'cancelled: {reason}',
           customer_id=payment.customer_id)

    return ok({
        'payment': payment_dict(payment),
        'invoice': invoice_dict(invoice) if invoice else None,
        'detail': f'Receipt R{payment.id} cancelled. '
                  + (f'{invoice.invoice_no} is owed again.' if invoice else ''),
    })


@bp.post('/payments/<int:pid>/return')
@admin_required
def payment_sales_return(pid):
    """Refund some or all of a payment, keeping both halves on the ledger."""
    original = db.session.get(Payment, pid)
    if not original:
        return fail('not_found', 404)
    if original.status != 'approved':
        return fail('not_returnable', 409,
                    detail='Only an approved payment can be returned. This '
                           'one is ' + (original.status or 'unknown') + '.')

    data = body()

    amount, error = _decimal(data.get('amount'), 'Return amount',
                             default=Decimal(str(original.amount or 0)))
    if error:
        return error
    amount = abs(amount or Decimal('0'))
    if amount <= 0:
        return fail('amount_required', 400,
                    detail='Enter the amount being returned.')

    # Everything already returned against this receipt, so two part-refunds
    # cannot quietly add up to more than came in.
    returned = db.session.query(
        db.func.coalesce(db.func.sum(Payment.amount), 0)).filter(
        Payment.status == 'approved',
        Payment.amount < 0,
        Payment.remarks.like(f'%Sales return against R{original.id}%')).scalar() or 0
    already = abs(Decimal(str(returned)))
    room = Decimal(str(original.amount or 0)) - already

    if amount > room:
        # money() returns a float, so an f-string renders "600.0". These
        # amounts are read aloud to customers; they should look like money.
        rupees = lambda v: f'{int(round(float(v))):,}'          # noqa: E731
        return fail('exceeds_payment', 400,
                    detail=f'Only Rs.{rupees(room)} of this '
                           f'Rs.{rupees(original.amount)} receipt is left to '
                           f'return'
                           + (f' (Rs.{rupees(already)} already returned).'
                              if already else '.'))

    reason = (data.get('reason') or '').strip()
    if not reason:
        return fail('reason_required', 400,
                    detail='Say why the money is going back.')

    refund = Payment(
        invoice_id=original.invoice_id,
        customer_id=original.customer_id,
        # NEGATIVE on purpose: Invoice.paid_amount sums approved payments, so
        # a negative row reduces what is paid and the balance rises again -
        # no special case anywhere else in the system.
        amount=-amount,
        payment_date=_parse_date(data.get('return_date'), date.today()),
        payment_mode=(data.get('payment_mode') or original.payment_mode
                      or 'Cash')[:50],
        source='return',
        status='approved',
        received_by_user_id=current_staff_id(),
        remarks=f'Sales return against R{original.id}: {reason}',
    )
    db.session.add(refund)
    db.session.flush()

    invoice = original.invoice
    _reopen_invoice(invoice)
    db.session.commit()

    _audit('Sales Return',
           f'Rs.{int(round(float(amount))):,} returned against receipt '
           f'{original.receipt_no}: {reason}',
           customer_id=original.customer_id)

    return ok({
        'payment': payment_dict(refund),
        'original': payment_dict(original),
        'invoice': invoice_dict(invoice) if invoice else None,
        'detail': f'Rs.{int(round(float(amount))):,} returned '
                  f'against R{original.id}.',
    })


@bp.get('/payments/<int:pid>/receipt')
@staff_required
def payment_receipt(pid):
    payment = db.session.get(Payment, pid)
    if not payment:
        return fail('not_found', 404)

    try:
        from services.invoice_pdf import build_receipt_pdf
    except ImportError:
        return fail('pdf_unavailable', 503,
                    detail='ReportLab is not installed. Run: pip install reportlab')

    from .invoices import _logo_path
    try:
        pdf = build_receipt_pdf(payment, logo_path=_logo_path())
    except Exception as exc:
        return fail('pdf_failed', 500, detail=str(exc)[:200])

    name = payment.receipt_no
    return Response(pdf, mimetype='application/pdf', headers={
        'Content-Disposition': f'inline; filename="receipt-{name}.pdf"',
    })


@bp.get('/public/payments/<int:pid>/receipt.pdf')
def public_payment_receipt(pid):
    """The receipt, for the customer - and for Meta, without a login.

    WhatsApp's approved `receipt_attachment` template has a DOCUMENT header,
    and Meta fetches that file from a public URL on its own servers. It cannot
    present a staff token, so the staff route above is no use to it.

    Unauthenticated but not guessable: the link carries an HMAC over the
    payment id and an expiry, so walking the integers reveals nothing about
    which payments exist.
    """
    from services.signed_links import verify

    if not verify('receipt', pid, request.args.get('exp'), request.args.get('sig')):
        return fail('link_invalid_or_expired', 403,
                    detail='This receipt link is no longer valid. Ask us to '
                           'send it again.')

    payment = db.session.get(Payment, pid)
    if not payment:
        return fail('not_found', 404)

    try:
        from services.invoice_pdf import build_receipt_pdf
        from .invoices import _logo_path
        pdf = build_receipt_pdf(payment, logo_path=_logo_path())
    except Exception as exc:
        # `current_app` is imported here rather than at module scope, matching
        # the rest of this file - and it was not imported at all, so this
        # handler raised NameError and replaced the real PDF error with a
        # traceback about logging it.
        from flask import current_app
        current_app.logger.exception('Public receipt PDF failed')
        return fail('pdf_failed', 500, detail=str(exc)[:200])

    name = payment.receipt_no
    return Response(pdf, mimetype='application/pdf', headers={
        'Content-Disposition': f'inline; filename="receipt-{name}.pdf"',
        'Cache-Control': 'private, max-age=300',
    })


@bp.post('/payments/<int:pid>/send')
@staff_required
def payment_send(pid):
    """WhatsApp the payment acknowledgement."""
    payment = db.session.get(Payment, pid)
    if not payment:
        return fail('not_found', 404)

    customer = db.session.get(Customer, payment.customer_id)
    if not customer:
        return fail('customer_missing', 409)
    if not customer.mobile:
        return fail('no_mobile_number', 400,
                    detail='This customer has no mobile number on file.')

    try:
        from app import send_template_message
        result = send_template_message(
            customer, 'payment_received',
            invoice=payment.invoice, payment=payment)
    except Exception as exc:
        return fail('send_failed', 424, detail=str(exc)[:200])

    status = getattr(result, 'status', 'unknown')
    from services.messaging import DELIVERABLE_STATUSES
    if status in DELIVERABLE_STATUSES:
        return ok({
            'status': status,
            'to': customer.mobile,
            'detail': ('WhatsApp gateway is not configured, so the receipt '
                       'was logged instead of sent.') if status == 'dry-run' else '',
        })
    return fail('send_failed', 424,
                detail=getattr(result, 'detail', '')
                or 'The gateway rejected the message.')


# --------------------------------------------------------------------------- #
#  Plan search for the Assign Plan picker
# --------------------------------------------------------------------------- #
@bp.get('/plans/picker')
@staff_required
def plan_picker():
    """
    Plans for the Add Customer / Assign Plan table.

    `kind` splits the catalogue the way the live CRM does: Unlimited plans
    have no FUP in their name or type, FUP plans do. The split is a naming
    convention rather than a column, so it is applied here, once, instead of
    being re-derived in the browser.
    """
    query = Plan.query.filter(Plan.is_active.is_(True))

    q = (request.args.get('q') or '').strip()
    if q:
        like = f'%{q}%'
        query = query.filter(or_(Plan.name.ilike(like),
                                 Plan.plan_code.ilike(like),
                                 Plan.plan_type.ilike(like)))

    rows = query.order_by(Plan.name).all()

    kind = (request.args.get('kind') or '').strip().lower()
    if kind in ('unlimited', 'fup'):
        def is_fup(plan):
            haystack = f'{plan.name or ""} {plan.plan_type or ""}'.lower()
            return 'fup' in haystack
        rows = [p for p in rows if is_fup(p) == (kind == 'fup')]

    return ok([{
        'id': p.id,
        'name': p.name,
        'plan_code': p.plan_code or '',
        'plan_type': p.plan_type or '',
        'speed_mbps': p.speed_mbps,
        'validity_days': p.validity_days or 30,
        'service_provider_id': p.service_provider_id,
        'service_provider': p.service_provider.name if p.service_provider else '',
        # Base is what we owe the upstream ISP; total is what the customer pays.
        'base_amount': money(p.isp_amount),
        'total_amount': money(p.price_monthly),
    } for p in rows])
