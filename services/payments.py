"""
services/payments.py
====================

Approving and rejecting a payment in one place.

There are three doors into this: the authorisation queue, the customer view,
and the portal-activity screen. They all have to do the same six things -
flip the payment, settle the invoice, apply any renewal the payment was
buying, un-suspend the customer, tell them, and write the audit trail - so
that logic lives here rather than in three routes that drift apart.
"""
from datetime import datetime

from models import db
from services import renewals


def _notify(customer, template_type, **kwargs):
    """Message the customer without ever breaking the transaction."""
    try:
        from services import messaging
        return messaging.send_template(customer, template_type, **kwargs)
    except Exception:                                    # noqa: BLE001
        return None


def approve_payment(payment, user=None):
    """
    Verify and credit a payment.

    Returns ``(ok, renewal_applied)``. ``ok`` is False when the payment was
    already dealt with, so callers can say so instead of double-crediting.
    """
    if payment is None or payment.authorized_at is not None:
        return False, False

    payment.status = 'approved'
    payment.authorized_at = datetime.utcnow()
    payment.authorized_by_user_id = getattr(user, 'id', None)
    payment.rejection_reason = None
    payment.rejected_at = None
    payment.rejected_by_user_id = None

    invoice = payment.invoice
    if invoice is not None:
        db.session.flush()                    # so invoice.balance sees it
        if invoice.balance <= 0:
            invoice.status = 'paid'

    # A payment that settles a renewal invoice extends the plan.
    renewal_applied = False
    req = renewals.open_request_for_payment(payment)
    if req is not None and invoice is not None and invoice.balance <= 0:
        req.payment_id = payment.id
        renewal_applied = renewals.approve(req, user=user,
                                           note='Payment verified')

    customer = payment.customer if hasattr(payment, 'customer') else None
    if customer is None:
        from models import Customer
        customer = db.session.get(Customer, payment.customer_id)

    # Clearing the dues brings a suspended connection back.
    if customer is not None and not customer.is_active:
        outstanding = sum(i.balance for i in customer.invoices
                          if i.balance > 0)
        if outstanding <= 0:
            customer.is_active = True

    db.session.commit()

    if customer is not None:
        if renewal_applied:
            _notify(customer, 'renewal_approved', invoice=invoice,
                    payment=payment, customer_plan=req.customer_plan)
        else:
            _notify(customer, 'payment_approved', invoice=invoice,
                    payment=payment)
    return True, renewal_applied


def reject_payment(payment, user=None, reason=None):
    """
    Turn a payment entry down.

    Returns ``(ok, renewal_rejected)``.
    """
    if payment is None or payment.status == 'rejected':
        return False, False

    payment.status = 'rejected'
    payment.authorized_at = datetime.utcnow()
    payment.authorized_by_user_id = getattr(user, 'id', None)
    payment.rejected_at = datetime.utcnow()
    payment.rejected_by_user_id = getattr(user, 'id', None)
    payment.rejection_reason = (reason or 'Could not be verified')[:255]

    invoice = payment.invoice
    if invoice is not None:
        db.session.flush()
        if invoice.status == 'paid' and invoice.balance > 0:
            invoice.status = 'sent'

    # The renewal this was paying for goes back to waiting, not away - the
    # customer can submit a corrected reference against the same invoice.
    renewal_rejected = False
    req = renewals.open_request_for_payment(payment)
    if req is not None and req.payment_id == payment.id:
        req.payment_id = None
        renewal_rejected = True

    db.session.commit()

    customer = payment.customer if hasattr(payment, 'customer') else None
    if customer is None:
        from models import Customer
        customer = db.session.get(Customer, payment.customer_id)
    if customer is not None:
        _notify(customer, 'payment_rejected', invoice=invoice, payment=payment,
                extra={'reason': payment.rejection_reason})
    return True, renewal_rejected


def pending_portal_entries():
    """Customer-submitted payments waiting on a human."""
    from models import Payment
    return (Payment.query
            .filter(Payment.source == 'portal',
                    Payment.status == 'pending')
            .order_by(Payment.payment_date.desc(), Payment.id.desc())
            .all())


def search_by_reference(term):
    """Find payments by UTR / gateway reference / receipt book number."""
    from models import Payment
    term = (term or '').strip()
    if not term:
        return []
    like = f'%{term}%'
    return (Payment.query
            .filter(db.or_(Payment.utr.ilike(like),
                           Payment.gateway_transaction_id.ilike(like),
                           Payment.book_receipt_no.ilike(like),
                           Payment.mode_detail.ilike(like)))
            .order_by(Payment.payment_date.desc(), Payment.id.desc())
            .limit(200).all())
