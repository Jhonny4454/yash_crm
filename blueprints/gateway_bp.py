"""
blueprints/gateway_bp.py
========================

Razorpay counter-side checkout.

``templates/payments/gateway.html`` already existed and posts its handler
result to ``url_for('payment_callback')`` - an endpoint that was never
defined, so the template raised a BuildError the moment anything rendered it.

This adds:

    GET  /payments/gateway/<invoice_id>   Open the Razorpay checkout page
    POST /payments/callback               Verify the signature, credit the money

The signature check is plain HMAC-SHA256, so no extra dependency is needed.
If ``RAZORPAY_KEY_ID`` / ``RAZORPAY_KEY_SECRET`` are not configured the
screen degrades gracefully and tells the operator to use Cashfree or take the
payment at the counter.
"""
import hashlib
import hmac
import json
from datetime import date, datetime

import requests
from flask import (current_app, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from models import AuditLog, Customer, Invoice, OnlinePaymentOrder, Payment, db

RAZORPAY_API = 'https://api.razorpay.com/v1'


def _keys():
    return (current_app.config.get('RAZORPAY_KEY_ID'),
            current_app.config.get('RAZORPAY_KEY_SECRET'))


def _configured():
    key_id, key_secret = _keys()
    return bool(key_id and key_secret)


def _audit(action, details):
    try:
        db.session.add(AuditLog(user_id=getattr(current_user, 'id', None),
                                action=action, details=details,
                                ip_address=request.remote_addr))
        db.session.commit()
    except Exception:
        db.session.rollback()


# --------------------------------------------------------------------------- #
#  Checkout page
# --------------------------------------------------------------------------- #
@login_required
def payment_gateway(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)

    if not _configured():
        flash('Razorpay is not configured. Set RAZORPAY_KEY_ID and '
              'RAZORPAY_KEY_SECRET, or collect the payment through Cashfree '
              'or at the counter.', 'warning')
        return redirect(url_for('customer_view', id=invoice.customer_id))

    amount = round(float(invoice.balance or 0), 2)
    if amount <= 0:
        flash('That invoice is already settled.', 'info')
        return redirect(url_for('customer_view', id=invoice.customer_id))

    key_id, key_secret = _keys()
    try:
        response = requests.post(
            f'{RAZORPAY_API}/orders',
            auth=(key_id, key_secret),
            json={'amount': int(round(amount * 100)),
                  'currency': 'INR',
                  'receipt': invoice.invoice_no,
                  'notes': {'invoice_id': str(invoice.id),
                            'customer_id': str(invoice.customer_id)}},
            timeout=20)
        response.raise_for_status()
        order_data = response.json()
    except Exception as exc:
        current_app.logger.error('Razorpay order failed: %s', exc)
        flash('We could not start the payment. Please try again in a moment.',
              'danger')
        return redirect(url_for('customer_view', id=invoice.customer_id))

    record = OnlinePaymentOrder(
        order_id=order_data['id'],
        customer_id=invoice.customer_id,
        invoice_id=invoice.id,
        gateway='razorpay',
        amount=amount,
        status='created',
        cf_order_id=order_data['id'],
        note=f'Counter payment for {invoice.invoice_no}',
    )
    db.session.add(record)
    db.session.commit()

    # The template reads order.amount (paise) and order.id
    return render_template('payments/gateway.html',
                           invoice=invoice,
                           customer=invoice.customer,
                           order=type('Order', (), {
                               'id': order_data['id'],
                               'amount': order_data['amount'],
                           })(),
                           key=key_id)


# --------------------------------------------------------------------------- #
#  Callback
# --------------------------------------------------------------------------- #
@login_required
def payment_callback():
    payment_id = (request.form.get('razorpay_payment_id') or '').strip()
    order_id = (request.form.get('razorpay_order_id') or '').strip()
    signature = (request.form.get('razorpay_signature') or '').strip()

    if not (payment_id and order_id and signature):
        flash('The payment response was incomplete. Nothing was credited.',
              'danger')
        return redirect(url_for('dashboard'))

    _key_id, key_secret = _keys()
    expected = hmac.new(key_secret.encode(),
                        f'{order_id}|{payment_id}'.encode(),
                        hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature):
        current_app.logger.warning('Rejected Razorpay callback with a bad '
                                   'signature for order %s', order_id)
        flash('That payment could not be verified and was NOT credited. '
              'Please check the Razorpay dashboard.', 'danger')
        return redirect(url_for('dashboard'))

    record = OnlinePaymentOrder.query.filter_by(order_id=order_id).first()
    if not record:
        flash('We could not match that payment to an order.', 'warning')
        return redirect(url_for('dashboard'))

    if record.status == 'paid':
        flash('That payment has already been credited.', 'info')
        return redirect(url_for('customer_view', id=record.customer_id))

    invoice = record.invoice
    customer = record.customer

    payment = Payment(
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=record.amount,
        payment_date=date.today(),
        payment_mode='Online',
        mode_detail=f'Razorpay | Txn {payment_id} | Order {order_id}',
        gateway_transaction_id=payment_id,
        source='portal',
        status='approved',
        authorized_at=datetime.utcnow(),
        authorized_by_user_id=getattr(current_user, 'id', None),
        remarks='Paid through the Razorpay checkout.',
    )
    db.session.add(payment)
    db.session.flush()

    record.status = 'paid'
    record.transaction_id = payment_id
    record.payment_method = 'Razorpay'
    record.payment_id = payment.id

    if invoice.balance <= 0:
        invoice.status = 'paid'

    db.session.commit()
    _audit('Razorpay Payment',
           f'{customer.full_name} paid Rs.{record.amount} '
           f'(txn {payment_id}) against {invoice.invoice_no}')

    flash(f'Payment of Rs.{record.amount} received. '
          f'Transaction ID: {payment_id}', 'success')
    return redirect(url_for('customer_view', id=customer.id))


def register(app):
    app.add_url_rule('/payments/gateway/<int:invoice_id>', 'payment_gateway',
                     payment_gateway)
    app.add_url_rule('/payments/callback', 'payment_callback',
                     payment_callback, methods=['POST'])
