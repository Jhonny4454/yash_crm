"""
blueprints/renewals_bp.py
=========================

Everything the admin panel was missing around plan renewal.

    GET/POST /customers/<id>/renew          Full renewal screen
    GET      /renewals                      Renewal queue / due board
    POST     /renewals/bulk-renew           Renew many customers at once
    POST     /renewals/send-reminders       WhatsApp/SMS the selected rows
    GET/POST /customers/<id>/addon-charge   Add shifting / device / other charges
    POST     /addon-charges/<id>/delete     Remove a wrongly-raised charge

The renewal screen does in one submit what previously took three screens:
choose the plan, pick the duration, apply a discount and GST, raise the
invoice, optionally collect the money, extend the dates, re-enable the
connection on the ISP, and send the confirmation message.
"""
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_

from models import (AddonCategory, AuditLog, Company, Customer, CustomerPlan,
                    Invoice, Payment, Plan, Product, ServiceProvider, db)
from models_ext import InvoiceItem, Setting

renewals_bp = Blueprint('renewals', __name__)

PAYMENT_MODES = ('Cash', 'Cheque', 'UPI', 'Card', 'NEFT', 'RTGS', 'IMPS',
                 'Bank Transfer', 'Paytm', 'GooglePay', 'PhonePay',
                 'Online Transfer', 'Online', 'Credit Card')


# --------------------------------------------------------------------------- #
#  Small helpers (kept local so this file never imports app.py)
# --------------------------------------------------------------------------- #
def _audit(action, details):
    try:
        db.session.add(AuditLog(
            user_id=getattr(current_user, 'id', None),
            action=action, details=details,
            ip_address=request.remote_addr))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _setting(key, default):
    try:
        return Setting.get(key, default)
    except Exception:
        return default


def _gst_percent():
    try:
        return Decimal(str(_setting('gst_percent', 18) or 18))
    except (InvalidOperation, TypeError):
        return Decimal('18')


def _dec(value, default='0'):
    try:
        return Decimal(str(value if value not in (None, '') else default))
    except (InvalidOperation, TypeError):
        return Decimal(default)


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
    base = amount / (Decimal('1') + rate / Decimal('100'))
    return amount.quantize(cents), (amount - base).quantize(cents)


def _next_invoice_no():
    today = date.today().strftime('%Y%m%d')
    last = db.session.execute(
        db.select(Invoice.id).order_by(Invoice.id.desc()).limit(1)).scalar() or 0
    for attempt in range(20):
        candidate = f"INV-{today}-{last + 1 + attempt:04d}"
        if not Invoice.query.filter_by(invoice_no=candidate).first():
            return candidate
    return f"INV-{today}-{secrets.token_hex(4).upper()}"


def _due_days():
    try:
        return int(_setting('invoice_due_days', 15) or 15)
    except (TypeError, ValueError):
        return 15


def _notify(customer, template_type, **kwargs):
    """Send a template message without ever breaking the transaction."""
    try:
        from services import messaging
        return messaging.send_template(customer, template_type, **kwargs)
    except Exception:
        current_app.logger.warning('Could not send %s to %s',
                                   template_type, customer.id)
        return None


def _enable_on_network(customer):
    try:
        from services import isp_providers
        isp_providers.provision(customer, 'enable')
    except Exception:
        current_app.logger.info('ISP enable skipped for customer %s',
                                customer.id)


def _parse_date(raw, fallback=None):
    if not raw:
        return fallback
    try:
        return datetime.strptime(str(raw)[:10], '%Y-%m-%d').date()
    except ValueError:
        return fallback


def _admin_only():
    return getattr(current_user, 'is_admin', lambda: False)()


# --------------------------------------------------------------------------- #
#  1. The full renewal screen
# --------------------------------------------------------------------------- #
@renewals_bp.route('/customers/<int:id>/renew', methods=['GET', 'POST'])
@login_required
def renew(id):
    customer = Customer.query.get_or_404(id)
    active = CustomerPlan.query.filter_by(customer_id=id,
                                          status='active').first()
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.name).all()
    company = Company.query.first()

    open_invoices = [i for i in Invoice.query.filter(
        Invoice.customer_id == id,
        Invoice.status.in_(('draft', 'sent', 'overdue'))).all() if i.balance > 0]
    outstanding = round(sum(i.balance for i in open_invoices), 2)

    ctx = dict(customer=customer, active_plan=active, plans=plans,
               company=company, open_invoices=open_invoices,
               outstanding=outstanding, payment_modes=PAYMENT_MODES,
               gst_percent=float(_gst_percent()), today=date.today())

    if request.method == 'GET':
        return render_template('customers/renew.html', **ctx)

    # ---- read the form -----------------------------------------------------
    plan_id = request.form.get('plan_id', type=int)
    plan = db.session.get(Plan, plan_id) if plan_id else (
        active.plan if active else None)
    if not plan:
        flash('Please choose a plan to renew onto.', 'danger')
        return render_template('customers/renew.html', **ctx)

    try:
        periods = max(1, min(int(request.form.get('periods') or 1), 36))
    except (TypeError, ValueError):
        periods = 1

    validity = int(plan.validity_days or 30)
    is_change = bool(active and active.plan_id != plan.id)

    # Start from the later of "today" and the current expiry so a customer who
    # renews early does not lose the days they have already paid for.
    if active:
        base = max(active.end_date, date.today()) if active.auto_renew \
            else active.end_date
    else:
        base = date.today()

    start_date = _parse_date(request.form.get('start_date'),
                             active.start_date if active else date.today())
    end_date = _parse_date(request.form.get('end_date'),
                           base + timedelta(days=validity * periods))

    if end_date <= start_date:
        flash('The new expiry date must be after the start date.', 'danger')
        return render_template('customers/renew.html', **ctx)

    # ---- money -------------------------------------------------------------
    amount = _dec(request.form.get('amount'),
                  str(_dec(plan.price_monthly) * periods))
    discount = _dec(request.form.get('discount_amount'), '0')
    if discount < 0:
        discount = Decimal('0')
    if discount > amount:
        discount = amount

    tax_mode = (request.form.get('tax_applicable') or 'notax').lower()
    taxable = amount - discount
    grand_total, tax_amount = _apply_tax(taxable, tax_mode)

    caption = (request.form.get('caption') or '').strip() or (
        f'Plan change - {plan.name}' if is_change else plan.name)

    # ---- persist -----------------------------------------------------------
    try:
        if is_change:
            active.status = 'cancelled'
            db.session.flush()
            customer_plan = CustomerPlan(
                customer_id=customer.id,
                plan_id=plan.id,
                start_date=start_date,
                end_date=end_date,
                status='active',
                auto_renew=True,
                grace_period_days=(active.grace_period_days if active else 1),
                last_invoice_date=date.today(),
                suspension_review_status='none',
            )
            db.session.add(customer_plan)
        elif active:
            customer_plan = active
            customer_plan.start_date = start_date
            customer_plan.end_date = end_date
            customer_plan.status = 'active'
            customer_plan.last_invoice_date = date.today()
            customer_plan.suspension_review_status = 'none'
            customer_plan.suspended_at = None
        else:
            customer_plan = CustomerPlan(
                customer_id=customer.id,
                plan_id=plan.id,
                start_date=start_date,
                end_date=end_date,
                status='active',
                auto_renew=True,
                last_invoice_date=date.today(),
            )
            db.session.add(customer_plan)
        db.session.flush()

        invoice = Invoice(
            customer_id=customer.id,
            customer_plan_id=customer_plan.id,
            invoice_no=_next_invoice_no(),
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=_due_days()),
            total_amount=grand_total,
            tax_amount=tax_amount,
            discount_amount=discount,
            caption=caption,
            invoice_type='plan',
            status='sent',
            remarks=(request.form.get('remarks') or '').strip() or None,
        )
        db.session.add(invoice)
        db.session.flush()

        db.session.add(InvoiceItem(
            invoice_id=invoice.id,
            description=f'{plan.name} x {periods} '
                        f'({start_date:%d-%b-%Y} to {end_date:%d-%b-%Y})',
            item_type='plan',
            quantity=periods,
            unit_price=_dec(plan.price_monthly),
            discount_amount=discount,
            tax_percent=(_gst_percent() if tax_mode != 'notax'
                         else Decimal('0')),
            period_from=start_date,
            period_to=end_date,
        ))

        # ---- optional payment in the same submit --------------------------
        payment = None
        collect = request.form.get('collect_payment')
        paid_amount = _dec(request.form.get('paid_amount'), '0')
        if collect and paid_amount > 0:
            needs_auth = not _admin_only()
            payment = Payment(
                invoice_id=invoice.id,
                customer_id=customer.id,
                amount=paid_amount,
                payment_date=_parse_date(request.form.get('payment_date'),
                                         date.today()),
                payment_mode=(request.form.get('payment_mode') or 'Cash'),
                mode_detail=(request.form.get('mode_detail') or '').strip() or None,
                book_receipt_no=(request.form.get('book_receipt_no')
                                 or '').strip() or None,
                remarks='Collected on the renewal screen.',
                status='approved',
                source='admin',
                received_by_user_id=getattr(current_user, 'id', None),
                authorized_at=None if needs_auth else datetime.utcnow(),
                authorized_by_user_id=(None if needs_auth
                                       else getattr(current_user, 'id', None)),
            )
            db.session.add(payment)
            db.session.flush()
            if invoice.balance <= 0:
                invoice.status = 'paid'

        # ---- reconnect if they were cut off -------------------------------
        reconnected = False
        if not customer.is_active and request.form.get('reactivate'):
            customer.is_active = True
            reconnected = True

        db.session.commit()

    except Exception:
        db.session.rollback()
        current_app.logger.exception('Renewal failed for customer %s', id)
        flash('The renewal could not be saved. Nothing was charged.', 'danger')
        return render_template('customers/renew.html', **ctx)

    if reconnected:
        _enable_on_network(customer)

    _audit('Renew Plan',
           f'{customer.full_name}: {plan.name} x{periods} until {end_date} '
           f'(invoice {invoice.invoice_no}, Rs.{grand_total})')

    if request.form.get('send_message', '1') == '1':
        _notify(customer, 'renewal', plan=plan, customer_plan=customer_plan,
                invoice=invoice, payment=payment)

    msg = (f'Plan renewed until {end_date:%d-%b-%Y}. '
           f'Invoice {invoice.invoice_no} for Rs.{grand_total:,.2f} raised.')
    if payment is not None:
        msg += f' Payment of Rs.{paid_amount:,.2f} recorded.'
        if payment.authorized_at is None:
            msg += ' It is waiting for admin authorization.'
    flash(msg, 'success')

    if request.form.get('then') == 'print':
        return redirect(url_for('invoice_summary', id=invoice.id))
    return redirect(url_for('customer_view', id=customer.id))


# --------------------------------------------------------------------------- #
#  2. Renewal queue / due board
# --------------------------------------------------------------------------- #
@renewals_bp.route('/renewals')
@login_required
def queue():
    today = date.today()
    try:
        window = int(request.args.get('days', 7))
    except (TypeError, ValueError):
        window = 7

    bucket = request.args.get('bucket', 'due')
    zone = (request.args.get('zone') or '').strip()
    search = (request.args.get('q') or '').strip()

    query = CustomerPlan.query.filter(CustomerPlan.status == 'active')

    if bucket == 'overdue':
        query = query.filter(CustomerPlan.end_date < today)
    elif bucket == 'today':
        query = query.filter(CustomerPlan.end_date == today)
    else:  # 'due' - expiring inside the window
        query = query.filter(CustomerPlan.end_date >= today,
                             CustomerPlan.end_date <= today
                             + timedelta(days=window))

    query = query.join(Customer, CustomerPlan.customer_id == Customer.id)
    if zone:
        query = query.filter(Customer.zone == zone)
    if search:
        like = f'%{search}%'
        query = query.filter(or_(Customer.first_name.ilike(like),
                                 Customer.last_name.ilike(like),
                                 Customer.mobile.ilike(like),
                                 Customer.username.ilike(like),
                                 Customer.reference_id.ilike(like)))

    rows = query.order_by(CustomerPlan.end_date).all()

    # Outstanding per customer, computed in one pass
    balances = {}
    for inv in Invoice.query.filter(
            Invoice.status.in_(('draft', 'sent', 'overdue'))).all():
        if inv.balance > 0:
            balances[inv.customer_id] = round(
                balances.get(inv.customer_id, 0) + inv.balance, 2)

    items = []
    for cp in rows:
        customer = cp.customer
        items.append({
            'cp': cp,
            'customer': customer,
            'plan': cp.plan,
            'days_left': (cp.end_date - today).days,
            'outstanding': balances.get(customer.id, 0.0),
        })

    counts = {
        'overdue': CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date < today).count(),
        'today': CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date == today).count(),
        'due_7': CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date >= today,
            CustomerPlan.end_date <= today + timedelta(days=7)).count(),
        'due_30': CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date >= today,
            CustomerPlan.end_date <= today + timedelta(days=30)).count(),
    }

    zones = sorted({c.zone for c in Customer.query.all() if c.zone})

    return render_template('renewals/queue.html',
                           items=items, counts=counts, bucket=bucket,
                           window=window, zone=zone, zones=zones,
                           q=search, today=today,
                           total_due=round(sum(i['outstanding']
                                               for i in items), 2))


@renewals_bp.route('/renewals/bulk-renew', methods=['POST'])
@login_required
def bulk_renew():
    ids = request.form.getlist('plan_ids')
    if not ids:
        flash('Select at least one customer to renew.', 'warning')
        return redirect(request.referrer or url_for('renewals.queue'))

    send_message = request.form.get('send_message') == '1'
    renewed, failed = 0, 0

    for raw in ids:
        try:
            cp = db.session.get(CustomerPlan, int(raw))
            if not cp or not cp.plan:
                failed += 1
                continue

            plan = cp.plan
            validity = int(plan.validity_days or 30)
            base = max(cp.end_date, date.today()) if cp.auto_renew else cp.end_date
            cp.end_date = base + timedelta(days=validity)
            cp.status = 'active'
            cp.last_invoice_date = date.today()
            cp.suspension_review_status = 'none'

            invoice = Invoice(
                customer_id=cp.customer_id,
                customer_plan_id=cp.id,
                invoice_no=_next_invoice_no(),
                issue_date=date.today(),
                due_date=date.today() + timedelta(days=_due_days()),
                total_amount=plan.price_monthly,
                tax_amount=0.00,
                caption=plan.name,
                invoice_type='plan',
                status='sent',
            )
            db.session.add(invoice)
            db.session.commit()
            renewed += 1

            if send_message:
                _notify(cp.customer, 'renewal', plan=plan,
                        customer_plan=cp, invoice=invoice)
        except Exception:
            db.session.rollback()
            failed += 1
            current_app.logger.exception('Bulk renew failed for plan %s', raw)

    _audit('Bulk Renew', f'{renewed} renewed, {failed} failed')
    if renewed:
        flash(f'{renewed} customer(s) renewed and invoiced.', 'success')
    if failed:
        flash(f'{failed} row(s) could not be renewed.', 'warning')
    return redirect(request.referrer or url_for('renewals.queue'))


@renewals_bp.route('/renewals/send-reminders', methods=['POST'])
@login_required
def send_reminders():
    ids = request.form.getlist('plan_ids')
    template_type = request.form.get('template_type') or 'due_reminder'
    if not ids:
        flash('Select at least one customer first.', 'warning')
        return redirect(request.referrer or url_for('renewals.queue'))

    sent, failed = 0, 0
    for raw in ids:
        cp = db.session.get(CustomerPlan, int(raw)) if str(raw).isdigit() else None
        if not cp:
            failed += 1
            continue
        result = _notify(cp.customer, template_type, plan=cp.plan,
                         customer_plan=cp)
        from services.messaging import DELIVERABLE_STATUSES
        if result is not None and getattr(result, 'status', '') in DELIVERABLE_STATUSES:
            sent += 1
        else:
            failed += 1

    _audit('Renewal Reminders', f'{sent} sent, {failed} failed')
    flash(f'{sent} reminder(s) sent.'
          + (f' {failed} could not be delivered.' if failed else ''),
          'success' if sent else 'warning')
    return redirect(request.referrer or url_for('renewals.queue'))


# --------------------------------------------------------------------------- #
#  3. Addon charges (shifting / device / installation / other)
# --------------------------------------------------------------------------- #
@renewals_bp.route('/customers/<int:id>/addon-charge', methods=['GET', 'POST'])
@login_required
def addon_charge(id):
    customer = Customer.query.get_or_404(id)
    categories = AddonCategory.query.filter_by(is_active=True).order_by(
        AddonCategory.name).all()
    products = Product.query.filter_by(is_active=True).order_by(
        Product.name).all()

    ctx = dict(customer=customer, categories=categories, products=products,
               payment_modes=PAYMENT_MODES, gst_percent=float(_gst_percent()),
               today=date.today())

    if request.method == 'GET':
        return render_template('customers/addon_charge.html', **ctx)

    # ---- read the line items ----------------------------------------------
    descriptions = request.form.getlist('description')
    quantities = request.form.getlist('quantity')
    prices = request.form.getlist('unit_price')
    serials = request.form.getlist('serial_number')
    product_ids = request.form.getlist('product_id')

    lines, subtotal = [], Decimal('0')
    for index, description in enumerate(descriptions):
        description = (description or '').strip()
        if not description:
            continue
        try:
            qty = max(1, int(quantities[index] or 1))
        except (IndexError, ValueError):
            qty = 1
        price = _dec(prices[index] if index < len(prices) else 0)
        if price <= 0 and qty <= 0:
            continue

        line_total = price * qty
        subtotal += line_total
        lines.append({
            'description': description[:255],
            'quantity': qty,
            'unit_price': price,
            'serial_number': (serials[index][:100]
                              if index < len(serials) and serials[index]
                              else None),
            'product_id': (int(product_ids[index])
                           if index < len(product_ids)
                           and str(product_ids[index]).isdigit() else None),
        })

    if not lines:
        flash('Add at least one charge line with a description and an amount.',
              'danger')
        return render_template('customers/addon_charge.html', **ctx)

    discount = _dec(request.form.get('discount_amount'), '0')
    if discount > subtotal:
        discount = subtotal
    tax_mode = (request.form.get('tax_applicable') or 'notax').lower()
    grand_total, tax_amount = _apply_tax(subtotal - discount, tax_mode)

    caption = (request.form.get('caption') or '').strip() or 'Addon Charges'
    issue_date = _parse_date(request.form.get('issue_date'), date.today())
    due_date = _parse_date(request.form.get('due_date'),
                           issue_date + timedelta(days=_due_days()))
    if due_date < issue_date:
        flash('The due date cannot be before the invoice date.', 'danger')
        return render_template('customers/addon_charge.html', **ctx)

    try:
        invoice = Invoice(
            customer_id=customer.id,
            invoice_no=_next_invoice_no(),
            issue_date=issue_date,
            due_date=due_date,
            total_amount=grand_total,
            tax_amount=tax_amount,
            discount_amount=discount,
            caption=caption[:120],
            invoice_type='addon',
            status='sent',
            remarks=(request.form.get('remarks') or '').strip() or None,
        )
        db.session.add(invoice)
        db.session.flush()

        tax_percent = _gst_percent() if tax_mode != 'notax' else Decimal('0')
        for line in lines:
            db.session.add(InvoiceItem(
                invoice_id=invoice.id,
                description=line['description'],
                item_type='device' if line['product_id'] else 'service',
                quantity=line['quantity'],
                unit_price=line['unit_price'],
                tax_percent=tax_percent,
                serial_number=line['serial_number'],
                product_id=line['product_id'],
            ))

        payment = None
        paid_amount = _dec(request.form.get('paid_amount'), '0')
        if request.form.get('collect_payment') and paid_amount > 0:
            needs_auth = not _admin_only()
            payment = Payment(
                invoice_id=invoice.id,
                customer_id=customer.id,
                amount=paid_amount,
                payment_date=_parse_date(request.form.get('payment_date'),
                                         date.today()),
                payment_mode=(request.form.get('payment_mode') or 'Cash'),
                mode_detail=(request.form.get('mode_detail') or '').strip() or None,
                book_receipt_no=(request.form.get('book_receipt_no')
                                 or '').strip() or None,
                remarks='Collected against an addon charge.',
                status='approved',
                source='admin',
                received_by_user_id=getattr(current_user, 'id', None),
                authorized_at=None if needs_auth else datetime.utcnow(),
                authorized_by_user_id=(None if needs_auth
                                       else getattr(current_user, 'id', None)),
            )
            db.session.add(payment)
            db.session.flush()
            if invoice.balance <= 0:
                invoice.status = 'paid'

        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Addon charge failed for customer %s', id)
        flash('The charge could not be saved. Nothing was billed.', 'danger')
        return render_template('customers/addon_charge.html', **ctx)

    _audit('Addon Charge',
           f'{invoice.invoice_no} for {customer.full_name}: {caption} '
           f'Rs.{grand_total} ({len(lines)} line(s))')

    if request.form.get('send_message', '1') == '1':
        _notify(customer, 'bill', invoice=invoice, payment=payment)

    flash(f'Invoice {invoice.invoice_no} raised for Rs.{grand_total:,.2f}.',
          'success')

    if request.form.get('then') == 'print':
        return redirect(url_for('invoice_detailed', id=invoice.id))
    return redirect(url_for('customer_view', id=customer.id))


@renewals_bp.route('/addon-charges/<int:id>/delete', methods=['POST'])
@login_required
def addon_delete(id):
    if not _admin_only():
        flash('Only an administrator can delete an invoice.', 'danger')
        return redirect(request.referrer or url_for('dashboard'))

    invoice = Invoice.query.get_or_404(id)
    if invoice.payments:
        # Not `paid_amount > 0` - that only counts approved payments, so an
        # invoice with a pending or rejected payment slipped through and the
        # delete then made the ORM null that row's invoice_id, which the
        # NOT NULL column rejects.
        flash('This invoice has payment records against it and cannot be '
              'deleted. Reverse the payment first.', 'warning')
        return redirect(url_for('customer_view', id=invoice.customer_id))

    customer_id = invoice.customer_id
    number = invoice.invoice_no
    InvoiceItem.query.filter_by(invoice_id=invoice.id).delete()
    db.session.delete(invoice)
    db.session.commit()
    _audit('Delete Addon Invoice', f'{number} removed')
    flash(f'Invoice {number} deleted.', 'success')
    return redirect(url_for('customer_view', id=customer_id))


def register(app):
    app.register_blueprint(renewals_bp)
    return renewals_bp
