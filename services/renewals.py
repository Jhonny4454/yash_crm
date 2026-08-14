"""
services/renewals.py
====================

The one place that knows how a renewal actually changes a customer's plan.

Both the portal (customer raises the request) and the admin screens (staff
approve it, or approve the payment that pays for it) call in here, so a
renewal behaves identically no matter which door it came through.

Rules that live here:

* A customer can never move their own expiry date. `create_request()` only
  records intent and raises the invoice; `approve()` is what actually
  extends the plan, and only staff can reach it.
* Renewing early does not lose the days already paid for - the extension is
  measured from the current expiry, not from today. Renewing after expiry
  restarts from today so the customer is not billed for dead time.
* Approving twice is a no-op.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from models import Customer, CustomerPlan, Invoice, Plan, db
from models_ext import RenewalRequest

#: What a customer is allowed to buy in one go.
DURATION_CHOICES = (1, 3, 6, 12)


def _dec(value):
    try:
        return Decimal(str(value or 0))
    except Exception:                                   # noqa: BLE001
        return Decimal('0')


def price_for(plan, months, customer_plan=None):
    """What `months` cycles of ``plan`` cost for this customer.

    A saved customer-plan price only belongs to the same plan.  On a plan
    change the new plan's master price is the agreed starting point; carrying
    the old override across would quietly charge the wrong package.
    """
    months = max(1, int(months or 1))
    unit_price = (
        customer_plan.effective_price
        if customer_plan is not None and customer_plan.plan_id == plan.id
        else plan.price_monthly
    )
    return (_dec(unit_price) * months).quantize(Decimal('0.01'))


def days_for(plan, months):
    """How many days `months` cycles of `plan` are worth."""
    months = max(1, int(months or 1))
    return int(plan.validity_days or 30) * months


def quote(plan, months, customer_plan=None):
    """A price/duration quote for the portal to render."""
    return {
        'plan': plan,
        'months': int(months),
        'days': days_for(plan, months),
        'amount': price_for(plan, months, customer_plan),
    }


def active_plan(customer_id):
    return (CustomerPlan.query
            .filter_by(customer_id=customer_id, status='active')
            .order_by(CustomerPlan.end_date.desc())
            .first())


def latest_plan(customer_id):
    """The active plan, or the most recent one if nothing is active."""
    return (active_plan(customer_id)
            or CustomerPlan.query.filter_by(customer_id=customer_id)
            .order_by(CustomerPlan.id.desc()).first())


def extension_base(customer_plan):
    """
    The date an extension should start from.

    Still running -> extend from the existing expiry, so paying early does
    not throw away the remaining days. Already expired -> start from today,
    so the customer is not charged for the gap.
    """
    today = date.today()
    if customer_plan and customer_plan.end_date and customer_plan.end_date > today:
        return customer_plan.end_date
    return today


# --------------------------------------------------------------------------- #
#  Creating a request
# --------------------------------------------------------------------------- #
def create_request(customer, plan, months, *, invoice_no_factory,
                   due_days=15, note=None):
    """
    Record a renewal the customer asked for and raise its invoice.

    Returns ``(renewal_request, invoice)``. Nothing about the customer's plan
    changes here - that waits for `approve()`.
    """
    months = max(1, int(months or 1))
    if months not in DURATION_CHOICES:
        months = 1

    cp = latest_plan(customer.id)
    current_plan = cp.plan if cp else None
    kind = 'change' if (current_plan and current_plan.id != plan.id) else 'renew'

    amount = price_for(plan, months, cp)
    days = days_for(plan, months)

    label = 'Plan change' if kind == 'change' else 'Renewal'
    caption = f"{label} - {plan.name}"
    if months > 1:
        caption += f" ({months} months)"

    invoice = Invoice(
        customer_id=customer.id,
        customer_plan_id=cp.id if cp else None,
        invoice_no=invoice_no_factory(),
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=due_days),
        total_amount=amount,
        tax_amount=Decimal('0.00'),
        caption=caption[:120],
        invoice_type='plan',
        status='sent',
        remarks=note,
    )
    db.session.add(invoice)
    db.session.flush()

    req = RenewalRequest(
        customer_id=customer.id,
        customer_plan_id=cp.id if cp else None,
        current_plan_id=current_plan.id if current_plan else None,
        requested_plan_id=plan.id,
        months=months,
        days=days,
        amount=amount,
        invoice_id=invoice.id,
        kind=kind,
        status='pending',
        note=(note or '')[:255] or None,
    )
    db.session.add(req)
    db.session.commit()
    return req, invoice


def open_request_for_invoice(invoice_id):
    """The pending renewal an invoice is paying for, if any."""
    if not invoice_id:
        return None
    return RenewalRequest.query.filter_by(invoice_id=invoice_id,
                                          status='pending').first()


def open_request_for_payment(payment):
    """
    The pending renewal a payment settles.

    Matched by the payment's invoice first, then by an explicit link if the
    portal attached one.
    """
    if payment is None:
        return None
    req = RenewalRequest.query.filter_by(payment_id=payment.id,
                                         status='pending').first()
    if req:
        return req
    return open_request_for_invoice(payment.invoice_id)


# --------------------------------------------------------------------------- #
#  Decisions
# --------------------------------------------------------------------------- #
def approve(req, user=None, note=None):
    """
    Apply a renewal: extend the plan, switching it first if this was an
    upgrade or downgrade. Idempotent - approving an already-decided request
    does nothing and returns False.
    """
    if req is None or req.status != 'pending':
        return False

    customer = req.customer or db.session.get(Customer, req.customer_id)
    plan = req.requested_plan or db.session.get(Plan, req.requested_plan_id)
    if customer is None or plan is None:
        return False

    cp = req.customer_plan or latest_plan(customer.id)
    base = extension_base(cp)
    new_end = base + timedelta(days=int(req.days or days_for(plan, req.months)))

    if cp is None:
        cp = CustomerPlan(
            customer_id=customer.id,
            plan_id=plan.id,
            start_date=date.today(),
            end_date=new_end,
            status='active',
            auto_renew=True,
            grace_period_days=1,
        )
        db.session.add(cp)
        db.session.flush()
        req.customer_plan_id = cp.id
    else:
        changing_plan = cp.plan_id != plan.id
        cp.plan_id = plan.id
        cp.end_date = new_end
        cp.status = 'active'
        cp.suspension_review_status = 'none'
        cp.suspended_at = None
        cp.last_invoice_date = date.today()
        # An override is attached to the plan it was negotiated for. A plan
        # switch starts at the new package's master price unless staff later
        # set a new customer-specific price.
        if changing_plan:
            cp.price = None

    # A renewal always brings a suspended customer back online.
    customer.is_active = True

    req.status = 'approved'
    req.decided_at = datetime.utcnow()
    req.decided_by_id = getattr(user, 'id', None)
    req.decision_note = (note or '')[:255] or None
    req.effective_from = base
    req.effective_to = new_end

    db.session.commit()
    return True


def reject(req, user=None, note=None):
    """Turn a renewal down. The invoice is cancelled so it stops chasing."""
    if req is None or req.status != 'pending':
        return False

    req.status = 'rejected'
    req.decided_at = datetime.utcnow()
    req.decided_by_id = getattr(user, 'id', None)
    req.decision_note = (note or '')[:255] or None

    invoice = req.invoice
    if invoice is not None and invoice.paid_amount <= 0:
        invoice.status = 'cancelled'

    db.session.commit()
    return True


def cancel(req):
    """The customer changed their mind before anyone reviewed it."""
    if req is None or req.status != 'pending':
        return False
    req.status = 'cancelled'
    req.decided_at = datetime.utcnow()
    invoice = req.invoice
    if invoice is not None and invoice.paid_amount <= 0:
        invoice.status = 'cancelled'
    db.session.commit()
    return True


def history(customer_id, limit=50):
    return (RenewalRequest.query
            .filter_by(customer_id=customer_id)
            .order_by(RenewalRequest.created_at.desc())
            .limit(limit).all())


def pending_requests():
    return (RenewalRequest.query
            .filter_by(status='pending')
            .order_by(RenewalRequest.created_at.desc())
            .all())
