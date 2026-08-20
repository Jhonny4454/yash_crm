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


def detach_payment_records(invoice):
    """
    Detach every record pointing at an invoice about to be deleted.

    The payment rows ARE the ledger: deleting them with the bill would erase
    the trace of money that changed hands. So the child rows keep their
    amounts, receipt numbers and modes, and lose only the bill link - the
    customer's payment history survives the bill. The other tables that point
    at invoices would block the DELETE outright, so they are detached the same
    way.

    Returns False - and changes nothing - if the bill has a sales return
    (credit note) against it: that is itself a money document and its column
    is NOT NULL, so it cannot be orphaned; the operator must deal with the
    return first.

    Must be called inside the same session, before ``db.session.delete(invoice)``.
    """
    if invoice is None:
        return True

    if invoice.sales_returns:
        return False

    for p in list(invoice.payments):
        p.invoice_id = None

    from models import OnlinePaymentOrder, VendorBill, WalletEntry
    from models_ext import InvoiceItem, RenewalRequest

    OnlinePaymentOrder.query.filter_by(invoice_id=invoice.id).update(
        {'invoice_id': None})
    RenewalRequest.query.filter_by(invoice_id=invoice.id).update(
        {'invoice_id': None})
    VendorBill.query.filter_by(invoice_id=invoice.id).update(
        {'invoice_id': None})
    WalletEntry.query.filter_by(invoice_id=invoice.id).update(
        {'invoice_id': None})
    InvoiceItem.query.filter_by(invoice_id=invoice.id).delete()
    return True


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
            try:
                from app import enable_connection_on_network
                enable_connection_on_network(customer)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    'ISP reconnect failed after payment for %s: %s',
                    customer.full_name, exc)

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
    safe = term.replace('%', '\\%').replace('_', '\\_')
    like = f'%{safe}%'
    return (Payment.query
            .filter(db.or_(Payment.utr.ilike(like, escape='\\'),
                           Payment.gateway_transaction_id.ilike(like, escape='\\'),
                           Payment.book_receipt_no.ilike(like, escape='\\'),
                           Payment.mode_detail.ilike(like, escape='\\')))
            .order_by(Payment.payment_date.desc(), Payment.id.desc())
            .limit(200).all())
