import os
import re
import traceback

from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
import csv
import time
import io
import secrets
from functools import wraps
from threading import Lock
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from flask import (
    Flask, render_template, redirect, url_for, flash, request, jsonify,
    session, Response, abort, has_request_context
)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix
from urllib.parse import urlsplit

from models import (
    db, User, Customer, Plan, CustomerPlan, Invoice, Payment, AuditLog,
    StaffType, Attendance, Leave, Payroll,
    Vendor, Product, Stock,
    ExpenseCategory, ExpenseAccount, ExpensePayee, Expense,
    Company, Address, Zone, TaxMaster,
    Locality, Area, Building, InventoryAssignment, ServiceProvider, VendorBill, VendorBillItem,
    MessageTemplate, MessageLog, OnlinePaymentOrder,
    AddonCategory,
)
from models_ext import (
    Setting, InvoiceItem, ISPCredential, ISPSyncLog, BackupLog, ImportJob,
)
import models_api  # noqa: F401  - registers device_tokens / notifications tables
from models_api import Notification, NotificationTemplate, seed_notification_templates
from services import isp_providers
from services import messaging
from services import cashfree
from services import renewals as renewal_service
from services import payments as payment_service
from services.invoicing import amount_in_words
from forms import (
    LoginForm, CustomerForm, PlanForm, InvoiceForm, PaymentForm,
    StaffForm, AttendanceForm, LeaveForm, PayrollForm,
    ExpenseForm, VendorForm, ProductForm, StockForm, CompanyForm, ChangePasswordForm,
    ExpenseCategoryForm, ExpenseAccountForm, ExpensePayeeForm, ZoneForm, TaxForm,
    AddonInvoiceForm, PlanDatesForm, PAYMENT_MODE_CHOICES,
    ServiceProviderForm, VendorBillForm,
    CustomerLoginForm, CustomerChangePasswordForm,
)
from config import Config
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from collections import defaultdict

app = Flask(__name__)
app.config.from_object(Config)

# ---------- Security-critical config ----------
if not app.config.get('SECRET_KEY') or app.config.get('SECRET_KEY') in ('dev', 'changeme', '', 'dev-secret-key-change-me'):
    if os.environ.get('FLASK_ENV') == 'production':
        raise RuntimeError(
            "SECRET_KEY is not set. Set the SECRET_KEY environment variable "
            "before starting the app in production."
        )
    app.config['SECRET_KEY'] = secrets.token_hex(32)
    print("WARNING: Using an ephemeral auto-generated SECRET_KEY. "
          "Sessions will not survive a restart. Set SECRET_KEY in your "
          "environment for production.")

_prod = os.environ.get('FLASK_ENV') == 'production'

app.config.update(
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
)
# Disable CSRF protection globally for now
app.config['WTF_CSRF_ENABLED'] = False
# ====================================================================

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Ensure upload folder exists
UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

db.init_app(app)

# CSRF protection is disabled. This line is kept for future re‑enablement.
csrf = CSRFProtect(app)
# ================= DISABLED: csrf = CSRFProtect(app) ================
# The global CSRF protection is turned off via WTF_CSRF_ENABLED=False.
# ====================================================================

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.session_protection = 'strong'

# ---------- Register the REST API (/api/v1) ----------
# register_api() attaches every sub-blueprint, mounts it on the app and marks
# the whole prefix CSRF-exempt (the SPA / mobile app use Bearer tokens).
from blueprints.api import register_api
register_api(app, csrf=csrf)

# The React application is deployed beside the Jinja2 site at /app.  Keeping
# the legacy routes intact makes the migration safe for existing bookmarks.
from blueprints.spa_bp import register as register_spa
register_spa(app)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@login_manager.unauthorized_handler
def unauthorized():
    flash('Please log in to continue.', 'warning')
    return redirect(url_for('login', next=request.path))

# ---------- Feature blueprints (Settings / Backup / Import-Export / ISP) ----------
from blueprints.settings_bp import register as register_settings
register_settings(app)

# ---------- Feature blueprints added in this release ----------
#   portal_bp -> still registered for its OTP helpers, which the REST API
#   imports (blueprints/api/auth.py). Its own screens are server-rendered.
from blueprints.portal_bp import register as register_portal

register_portal(app)

# renewals_bp, gateway_bp, staff_auth_bp and portal_admin_bp were registered
# here and have been removed.
#
# They were the last of the server-rendered admin screens: 1,330 lines across
# four files, every route ending in render_template() or a redirect to another
# one. There is no templates/ directory in this project any more - the React
# app replaced all of it - so every one of those routes answered 500
# TemplateNotFound. They were not a feature waiting to be finished; they were
# URLs that could not work, taking up import time and search results.
#
# The files are in _removed/, not deleted. Nothing else imports them.

# Amount-in-words filter used by the invoice templates
app.jinja_env.globals['amount_in_words'] = amount_in_words

# ---------- Helpers ----------
def generate_invoice_no():
    """Thread-safe invoice number using a DB-level counter fallback."""
    today = date.today().strftime('%Y%m%d')
    for attempt in range(10):
        last = db.session.execute(
            db.select(Invoice.id).order_by(Invoice.id.desc()).limit(1)
        ).scalar()
        seq = (last + 1 + attempt) if last else (1 + attempt)
        candidate = f"INV-{today}-{seq:04d}"
        try:
            if not Invoice.query.filter_by(invoice_no=candidate).first():
                return candidate
        except IntegrityError:
            db.session.rollback()
            continue
    return f"INV-{today}-{secrets.token_hex(4).upper()}"

def _vendor_choices(include_blank=True):
    """(id, label) list for any Vendor SelectField."""
    rows = Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()
    out = [(0, '-- No vendor --')] if include_blank else []
    return out + [(v.id, v.name) for v in rows]

def generate_reference_id():
    return ''.join(secrets.choice('0123456789') for _ in range(8))

# Expose a few helpers to Jinja so templates can call them directly
@app.context_processor
def inject_template_helpers():
    return dict(
        payment_mode_choices=PAYMENT_MODE_CHOICES,
        today=date.today(),
    )

def log_audit(action, details):
    """
    Write one audit row.

    Safe to call from anywhere, including the background scheduler, where
    there is no request and no logged-in user: `current_user` resolves to
    None outside a request context, so touching `.is_authenticated`
    directly used to raise AttributeError and kill the nightly jobs.
    Auditing must never break the operation it is recording, so any failure
    here is logged and swallowed.
    """
    try:
        user_id = None
        try:
            if has_request_context():
                claims = getattr(request, 'jwt', None) or {}
                account = getattr(request, 'jwt_account', None)
                if claims.get('kind') == 'staff' and account is not None:
                    # The React app authenticates with a JWT, not flask_login.
                    # Without this, every API-driven action - which is almost
                    # all of them - would be recorded with no user and the
                    # Customer Log "By" column would read "—" everywhere.
                    user_id = account.id
                elif current_user and current_user.is_authenticated:
                    user_id = current_user.id
        except Exception:                                # noqa: BLE001
            user_id = None

        ip = None
        try:
            if has_request_context():
                ip = request.remote_addr
        except Exception:                                # noqa: BLE001
            ip = None

        db.session.add(AuditLog(
            user_id=user_id,
            action=action,
            details=(details or '')[:500],
            ip_address=ip,
        ))
        db.session.commit()
    except Exception:                                    # noqa: BLE001
        db.session.rollback()
        app.logger.warning("Could not write audit log for %r", action, exc_info=True)

def enable_connection_on_network(customer):
    log_audit('Network Enable (stub)', f"Requested network enable for {customer.full_name}")

def disable_connection_on_network(customer):
    log_audit('Network Disable (stub)', f"Requested network disable for {customer.full_name}")

def reset_mac_on_log2space(mac_address, customer_reference):
    """Call the log2space API to change this device's authenticated MAC.
    Replace the body with the real API call when the credentials are ready."""
    app.logger.info("reset_mac_on_log2space ref=%s mac=%s", customer_reference, mac_address)
    return True

def reset_customer_password_on_log2space(customer, new_password):
    """Call the log2space API to sync the new customer portal password."""
    app.logger.info("reset_password_on_log2space ref=%s", customer.reference_id)
    return True

#  Communication layer
#
#  Everything routes through services/messaging.py, which reads its gateway
#  configuration from the `settings` table (Settings -> Messaging) with an
#  environment-variable fallback. When no gateway is configured the message is
#  logged as a "dry-run" instead of being sent, so nothing ever breaks while
#  the WhatsApp provider is still being wired up.
def send_sms(phone, message, customer_id=None):
    return messaging.send_sms(phone, message, customer_id=customer_id)

def send_whatsapp(phone, message, customer_id=None):
    return messaging.send_whatsapp(phone, message, customer_id=customer_id)

def send_email(to, subject, body, attachment=None):
    from services import mailer
    attachments = None
    if attachment:
        attachments = [attachment]
    return mailer.send_email(to, subject, body, attachments=attachments)

#  Template messaging
def send_template_message(customer, template_type, context=None, *,
                          plan=None, customer_plan=None, invoice=None,
                          payment=None):
    """
    Render the named template for `customer` and send it over WhatsApp AND email.

    Returns a messaging.SendResult. Never raises, so a gateway outage can
    never roll back a renewal or a payment entry.
    """
    result = messaging.send_template(
        customer, template_type,
        plan=plan, customer_plan=customer_plan,
        invoice=invoice, payment=payment, extra=context,
    )
    if result.status in messaging.DELIVERABLE_STATUSES:
        log_audit('Send Message',
                  f"{template_type} -> {getattr(customer, 'full_name', '?')} "
                  f"({result.status})")

    # Also send email if customer has an address
    email = getattr(customer, 'email', None)
    if email:
        from services import mailer
        ctx = messaging.build_context(
            customer=customer, plan=plan, customer_plan=customer_plan,
            invoice=invoice, payment=payment, extra=context,
        )
        body = messaging.render_template_type(template_type, ctx) or ''
        if body.strip():
            _EMAIL_SUBJECTS = {
                'expiry_3d': 'Plan Expiry Reminder - 3 Days',
                'expiry_2d': 'Plan Expiry Reminder - 2 Days',
                'expiry_1d': 'Plan Expiry Reminder - 1 Day',
                'expired': 'Plan Expired',
                'renewal': 'Plan Renewed',
                'payment_received': 'Payment Received',
                'due_reminder': 'Payment Due Reminder',
                'daily_report': 'Daily Report',
                'welcome': 'Welcome to {{company_name}}',
                'bill': 'Invoice',
                'summary_bill': 'Invoice',
                'detailed_bill': 'Invoice',
                'payment_approved': 'Payment Receipt',
                'internet_down': 'Internet Service Down',
                'internet_restored': 'Internet Service Restored',
                'complaint_registered': 'Complaint Registered',
                'issue_resolved': 'Issue Resolved',
                'new_complaint': 'New Complaint',
                'payment_submitted': 'Payment Submitted',
                'payment_rejected': 'Payment Rejected',
                'renewal_approved': 'Renewal Approved',
            }
            subject = _EMAIL_SUBJECTS.get(
                template_type,
                template_type.replace('_', ' ').title()
            )
            if '{{company_name}}' in subject:
                subject = subject.replace('{{company_name}}',
                                          ctx.get('company_name', 'YASH Internet Services'))
            mailer.send_email(email, subject, body.strip())

    return result


# ---------- Access control ----------
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_admin():
            log_audit('Unauthorized Attempt',
                      f"User {current_user.username} tried to access {request.path}")
            flash('You do not have permission to perform this action.', 'danger')
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ---------- Customer Self-Service Access control ----------
def customer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'customer_id' not in session:
            flash('Please log in as a customer.', 'warning')
            return redirect(url_for('customer_login', next=request.path))
        customer = Customer.query.get(session['customer_id'])
        if not customer or not customer.is_active:
            session.pop('customer_id', None)
            flash('Account not found or inactive.', 'danger')
            return redirect(url_for('customer_login'))
        return f(*args, **kwargs)
    return decorated

# ---------- Auto-Invoicing & Scheduler ----------
def generate_auto_invoices():
    with app.app_context():
        today = date.today()
        plans_to_renew = CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.auto_renew == True,
            CustomerPlan.end_date <= today,
            (CustomerPlan.last_invoice_date == None) | (CustomerPlan.last_invoice_date < today)
        ).all()
        for cp in plans_to_renew:
            plan = cp.plan
            customer = cp.customer
            invoice = Invoice(
                customer_id=customer.id,
                invoice_no=generate_invoice_no(),
                issue_date=today,
                due_date=today + timedelta(days=15),
                total_amount=cp.effective_price,
                tax_amount=0.00,
                status='sent'
            )
            db.session.add(invoice)
            cp.last_invoice_date = today
            db.session.commit()
            log_audit('Auto-Invoice', f"Generated invoice {invoice.invoice_no} for {customer.full_name}")
            send_email(customer.email, f"New Invoice {invoice.invoice_no}", "Your invoice is attached.")
            send_sms(customer.mobile, f"Dear {customer.full_name}, invoice {invoice.invoice_no} generated. Due: {invoice.due_date}")
            send_whatsapp(customer.mobile, f"Your invoice {invoice.invoice_no} is ready.")
        unpaid = Invoice.query.filter(Invoice.status.in_(['sent', 'overdue'])).all()
        for inv in unpaid:
            if inv.due_date < today:
                cust = inv.customer
                send_sms(cust.mobile, f"Reminder: Invoice {inv.invoice_no} is overdue. Please pay.")
                send_email(cust.email, f"Overdue Invoice {inv.invoice_no}", "Please clear your dues.")

def send_grace_period_reminders():
    with app.app_context():
        today = date.today()
        due_today_unpaid = Invoice.query.filter(
            Invoice.status.in_(['sent', 'overdue']),
            Invoice.issue_date == today
        ).all()
        for inv in due_today_unpaid:
            cust = inv.customer
            send_sms(cust.mobile, f"Reminder: Invoice {inv.invoice_no} is unpaid. "
                                   f"Service will be suspended after the grace period if unpaid.")
            send_whatsapp(cust.mobile,
                          f"Your invoice {inv.invoice_no} is still unpaid. Please pay to avoid suspension.")

def auto_suspend_overdue():
    with app.app_context():
        today = date.today()
        plans = CustomerPlan.query.filter(CustomerPlan.status == 'active', CustomerPlan.auto_renew == True).all()
        for cp in plans:
            grace = cp.grace_period_days or 1
            unpaid = Invoice.query.filter(
                Invoice.customer_id == cp.customer_id,
                Invoice.status.in_(['sent', 'overdue']),
                Invoice.due_date + timedelta(days=grace) < today
            ).all()
            if unpaid and cp.customer.is_active:
                customer = cp.customer
                customer.is_active = False
                cp.suspension_review_status = 'pending_review'
                cp.suspended_at = datetime.utcnow()
                db.session.commit()
                try:
                    disable_connection_on_network(customer)
                except Exception as exc:
                    app.logger.warning('ISP disable failed for %s: %s',
                                       customer.id, exc)
                log_audit('Auto-Suspend', f"Suspended customer {customer.full_name} due to unpaid invoices. "
                                           f"Moved to Pending Review.")
                try:
                    send_sms(customer.mobile, "Your service has been suspended due to non-payment. Please contact support.")
                    send_email(customer.email, "Service Suspended",
                               "Your service has been suspended due to non-payment. Please contact support.")
                except Exception:
                    pass

def send_expiry_reminders():
    """Send templates for plans expiring in 3 days, 2 days, 1 day, and expired today."""
    with app.app_context():
        today = date.today()
        total_sent = 0

        # 3 days before expiry
        expiring_3d = CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date == today + timedelta(days=3)
        ).all()
        for cp in expiring_3d:
            send_template_message(cp.customer, 'expiry_3d', {'days': 3},
                                  customer_plan=cp)
            total_sent += 1

        # 2 days before expiry
        expiring_2d = CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date == today + timedelta(days=2)
        ).all()
        for cp in expiring_2d:
            send_template_message(cp.customer, 'expiry_2d', {'days': 2},
                                  customer_plan=cp)
            total_sent += 1

        # 1 day before expiry
        expiring_1d = CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date == today + timedelta(days=1)
        ).all()
        for cp in expiring_1d:
            send_template_message(cp.customer, 'expiry_1d', {'days': 1},
                                  customer_plan=cp)
            total_sent += 1

        # Expired today
        expired_today = CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date == today
        ).all()
        for cp in expired_today:
            send_template_message(cp.customer, 'expired', customer_plan=cp)
            total_sent += 1

        # Notify admin of expiry summary
        if total_sent:
            summary = (
                f"Expiry Reminder Summary - {today.strftime('%d %b %Y')}\n\n"
                f"Expiring in 3 days: {len(expiring_3d)}\n"
                f"Expiring in 2 days: {len(expiring_2d)}\n"
                f"Expiring tomorrow: {len(expiring_1d)}\n"
                f"Expired today: {len(expired_today)}\n"
                f"Total messages sent: {total_sent}"
            )

            def _get(key, default=''):
                try:
                    row = Setting.query.filter_by(key=key).first()
                    if row and row.value:
                        from models_ext import ENCRYPTED_SETTINGS, decrypt_setting_value
                        val = row.value
                        if key in ENCRYPTED_SETTINGS:
                            val = decrypt_setting_value(val)
                        return val
                except Exception:
                    pass
                return os.environ.get(key.upper(), default)

            admin_email = _get('admin_email')
            if admin_email:
                from services import mailer
                mailer.send_email(admin_email,
                                  f'Expiry Reminders - {today.strftime("%d %b %Y")}',
                                  summary)
            admin_mobile = _get('admin_mobile')
            if admin_mobile:
                messaging.send_whatsapp(admin_mobile, summary)

            log_audit('Expiry Reminders',
                      f'Sent {total_sent} expiry reminder(s): '
                      f'3d={len(expiring_3d)}, 2d={len(expiring_2d)}, '
                      f'1d={len(expiring_1d)}, expired={len(expired_today)}')


def send_overdue_reminders():
    """Send due_reminder template to customers with unpaid overdue invoices."""
    with app.app_context():
        today = date.today()
        overdue = Invoice.query.filter(
            Invoice.status.in_(['sent', 'overdue']),
            Invoice.due_date < today,
        ).all()
        sent = 0
        for inv in overdue:
            cust = inv.customer
            if cust and cust.is_active:
                result = send_template_message(
                    cust, 'due_reminder',
                    invoice=inv,
                )
                if getattr(result, 'status', '') in ('sent', 'queued'):
                    sent += 1
                inv.status = 'overdue'
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        if sent:
            log_audit('Due Reminders',
                      f'Sent {sent} overdue payment reminder(s)')


def send_daily_report():
    """Build and send a daily summary to the admin via email and WhatsApp."""
    with app.app_context():
        today = date.today()
        yesterday = today - timedelta(days=1)

        from models import Customer, CustomerPlan, Invoice, Payment

        total_customers = Customer.query.filter_by(is_active=True).count()
        active_plans = CustomerPlan.query.filter_by(status='active').count()
        expiring_3d = CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date == today + timedelta(days=3),
        ).count()
        expiring_today = CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date == today,
        ).count()
        new_today = Customer.query.filter(
            Customer.registration_date == today,
        ).count()
        payments_today = Payment.query.filter(
            Payment.payment_date == today,
        ).all()
        payment_total = sum(p.amount for p in payments_today)
        overdue_count = Invoice.query.filter(
            Invoice.status.in_(['sent', 'overdue']),
            Invoice.due_date < today,
        ).count()

        report = (
            f"Daily Report - {today.strftime('%d %b %Y')}\n\n"
            f"Active Customers: {total_customers}\n"
            f"Active Plans: {active_plans}\n"
            f"Expiring in 3 days: {expiring_3d}\n"
            f"Expiring today: {expiring_today}\n"
            f"New customers today: {new_today}\n"
            f"Payments today: {len(payments_today)} (Rs. {payment_total:,.2f})\n"
            f"Overdue invoices: {overdue_count}\n"
        )

        def _get(key, default=''):
            try:
                row = Setting.query.filter_by(key=key).first()
                if row and row.value:
                    from models_ext import ENCRYPTED_SETTINGS, decrypt_setting_value
                    val = row.value
                    if key in ENCRYPTED_SETTINGS:
                        val = decrypt_setting_value(val)
                    return val
            except Exception:
                pass
            return os.environ.get(key.upper(), default)

        from services import mailer
        admin_email = _get('admin_email') or _get('mail_from', '')
        if admin_email:
            mailer.send_email(admin_email, f'Daily Report - {today}', report)

        admin_mobile = _get('admin_mobile') or _get('wa_sender', '')
        if admin_mobile:
            messaging.send_whatsapp(admin_mobile, report,
                                    template_type='daily_report')

        log_audit('Daily Report', f'Report generated for {today}')


scheduler = BackgroundScheduler(timezone=os.environ.get('TZ', 'Asia/Kolkata'))

#: Set RUN_SCHEDULER=0 on any extra worker so the cron jobs only fire once
#: across the whole fleet. With more than one gunicorn worker and the flag left
#: on, every worker would send the same reminder.
_should_start_scheduler = (
    app.config.get('RUN_SCHEDULER', True)
    and (os.environ.get('FLASK_ENV') == 'production'
         or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
         or not app.debug)
)
if _should_start_scheduler and not scheduler.running:
    # coalesce + misfire_grace_time keep a sleeping free-tier dyno from firing
    # a backlog of duplicate reminders the moment it wakes up.
    job_opts = dict(coalesce=True, max_instances=1, misfire_grace_time=3600)
    scheduler.add_job(generate_auto_invoices, CronTrigger(hour=1, minute=0), **job_opts)
    scheduler.add_job(auto_suspend_overdue, CronTrigger(hour=2, minute=0), **job_opts)
    scheduler.add_job(send_grace_period_reminders, CronTrigger(hour=10, minute=0), **job_opts)
    scheduler.add_job(send_expiry_reminders, CronTrigger(hour=9, minute=0), **job_opts)
    scheduler.add_job(send_overdue_reminders, CronTrigger(hour=11, minute=0), **job_opts)
    scheduler.add_job(send_daily_report, CronTrigger(hour=8, minute=0), **job_opts)
    scheduler.start()

# ---------- Error handlers ----------
def _err(code, msg, pre=None):
    """Render an error page; fall back to plain text so a template bug never hides the original error."""
    if pre:
        pre()
    try:
        return render_template('errors/error.html', code=code, message=msg), code
    except Exception:
        return f"{code} {msg}", code

@app.errorhandler(400)
def bad_request(e):   return _err(400, "The request could not be understood by the server.")
@app.errorhandler(403)
def forbidden(e):     return _err(403, "You don't have permission to access this page.")
@app.errorhandler(404)
def not_found(e):     return _err(404, "The page you're looking for doesn't exist.")
@app.errorhandler(413)
def too_large(e):     return _err(413, "The file you uploaded is too large.")
@app.errorhandler(429)
def rate_limited(e):  return _err(429, "Too many requests. Please slow down and try again shortly.")
@app.errorhandler(500)
def server_error(e):
    app.logger.exception("Unhandled server error")
    try:
        db.session.rollback()
    except Exception:
        pass

    # An API caller cannot do anything with an HTML error page - and there is
    # no templates/ folder, so _err() was falling through to the plain string
    # "500 Something went wrong on our end". The React client parses JSON, so
    # every server fault arrived in the browser as an unexplained failure with
    # the actual reason visible only in the Flask console.
    if request.path.startswith('/api/'):
        original = getattr(e, 'original_exception', None) or e
        # In production, never expose exception type or message — they leak
        # SQL fragments, internal paths, and library versions.
        if _is_production_env():
            detail = 'An internal error occurred. It has been logged.'
        else:
            detail = f'{type(original).__name__}: {original}'[:400]
        payload = {
            'ok': False,
            'error': 'server_error',
            'detail': detail,
        }
        # The traceback is a development aid. Never in production - it names
        # file paths and can echo query values back to the browser.
        if os.environ.get('DEBUG_TRACEBACK'):
            payload['traceback'] = traceback.format_exc()[-3000:]
        return jsonify(payload), 500

    return _err(500, "Something went wrong on our end. It's been logged.")


@app.errorhandler(Exception)
def api_exception(exc):
    """Turn any unhandled exception on an /api/ route into JSON.

    errorhandler(500) alone was not enough, and the gap only shows in the one
    place it matters. Flask's `PROPAGATE_EXCEPTIONS` defaults to `debug or
    testing`, and `app.run(debug=True)` is how this app is started locally -
    so an unhandled error was re-raised for the Werkzeug debugger to render as
    an HTML page, and the 500 handler was never consulted. The React client
    then had no JSON to read and reported the useless
    "Request failed with status code 500".

    A handler registered for `Exception` IS consulted before that propagation
    decision, so this runs in development too.

    Non-API routes deliberately re-raise: the interactive debugger is genuinely
    useful on the Jinja pages, and swallowing it there would be a downgrade.
    """
    if isinstance(exc, HTTPException):
        return exc

    if not request.path.startswith('/api/'):
        raise exc

    app.logger.exception('Unhandled error on %s %s', request.method, request.path)
    try:
        db.session.rollback()
    except Exception:
        pass

    if _is_production_env():
        detail = 'An internal error occurred. It has been logged.'
    else:
        detail = f'{type(exc).__name__}: {exc}'[:400]
    payload = {
        'ok': False,
        'error': 'server_error',
        'detail': detail,
    }
    if os.environ.get('DEBUG_TRACEBACK'):
        payload['traceback'] = traceback.format_exc()[-3000:]
    return jsonify(payload), 500


def _is_production_env():
    if os.environ.get('FLASK_ENV', '').lower() == 'production':
        return True
    return any(os.environ.get(k) for k in
               ('RENDER', 'RAILWAY_ENVIRONMENT', 'DYNO', 'FLY_APP_NAME'))

@app.errorhandler(CSRFError)
def csrf_error(e):
    if request.path.startswith('/api/'):
        return jsonify({
            'success': False,
            'message': 'CSRF validation failed'
        }), 400
    flash('Your session expired or the form was tampered with. Please try again.', 'danger')
    return redirect(request.referrer or url_for('dashboard')), 400
# ── Security: CORS, headers, cookie flags, login rate limiting ──────────────
#
# All of it lives in security.py so the rules cannot drift apart. CORS in
# particular was configured here (CORS_ORIGINS) and never applied - harmless
# locally because Vite proxies /api, fatal the moment the front end is deployed
# on its own domain.
try:
    from security import harden
    harden(app)
except RuntimeError as _sec_exc:
    # Only raised when FLASK_ENV=production and the secrets are still the
    # shipped defaults. That must stop a deployment - but it must never stop a
    # local server, which is why _is_production() now requires an explicit
    # flag rather than inferring it from app.debug.
    print(f'\nREFUSING TO START: {_sec_exc}\n')
    raise
except Exception as _sec_exc:  # pragma: no cover
    app.logger.error('Security hardening could not be applied: %s', _sec_exc)

# ---------- Request timing (set SLOW_REQUEST_MS env var to control threshold) ----------
_SLOW_MS = int(os.environ.get('SLOW_REQUEST_MS', 500))

@app.before_request
def _timing_start():
    request._t0 = time.perf_counter()


# The application is now React-first, but this backend used to publish a large
# Jinja site at these same addresses.  The templates were removed during that
# migration, leaving old bookmarks such as /login, /customers/42 and
# /customer/invoices to fail with TemplateNotFound.  Send page requests to the
# maintained SPA before the retired handlers can do database work or try to
# render a file which no longer exists.  API, download, webhook and form POST
# routes deliberately stay untouched.
_LEGACY_SPA_EXACT = {
    '': '',
    'dashboard': '',
    'login': 'login',
    'profile': 'profile',
    'payments/authorizations': 'authorizations',
    'reports/plan-expiry': 'reports/plan-expiry',
    'hr/attendance/report': 'reports/attendance',
    'hr/leaves/report': 'reports/leaves',
    'hr/payroll/report': 'reports/payroll',
    'zones': 'masters/zones',
    'masters': 'masters/zones',
    'masters/company': 'companies',
    'masters/templates': 'masters/message-templates',
    'masters/addon-categories': 'masters/addon-categories',
    'masters/service-providers': 'plan-master/service-providers',
    'customers': 'customers',
    'customers/add': 'customers/add',
    'customers/search': 'customers',
    'customers/ledger': 'customers/ledger',
    'plans': 'plans',
    'invoices': 'invoices',
    'payments': 'payments',
    'staff': 'staff',
    'staff/types': 'staff/types',
    'hr': 'hr/attendance',
    'hr/attendance': 'hr/attendance',
    'hr/leaves': 'hr/leaves',
    'hr/payroll': 'hr/payroll',
    'expenses': 'expenses',
    'expenses/categories': 'expenses/categories',
    'expenses/accounts': 'expenses/accounts',
    'expenses/payees': 'expenses/payees',
    'inventory': 'inventory/vendors',
    'inventory/vendors': 'inventory/vendors',
    'inventory/products': 'inventory/products',
    'inventory/stock': 'inventory/stock',
    'inventory/vendor-bills': 'inventory/vendor-bills',
    'messages/bulk': 'masters/bulk-messages',
    'settings': 'settings',
    'settings/sms-templates': 'masters/message-templates',
    'settings/backup': 'masters/backup',
    'settings/import-export': 'masters/import-export',
    'settings/isp': 'masters/isp',
    'customer/login': 'customer/login',
    'customer/register': 'customer/login',
    'customer/forgot-password': 'customer/forgot-password',
    'customer/dashboard': 'customer',
    'customer/profile': 'customer/profile',
    'customer/invoices': 'customer/invoices',
    'customer/payments': 'customer/payments',
    'customer/payments/new': 'customer/payments',
    'customer/plans': 'customer/plans',
    'customer/renew': 'customer/plans',
    'customer/renew/history': 'customer/plans',
    'customer/notifications': 'customer/notifications',
    'customer/messages': 'customer/notifications',
}


def _legacy_spa_path(path):
    """Return the SPA page for a retired server-rendered page, if any."""
    clean = path.strip('/')
    if clean in _LEGACY_SPA_EXACT:
        return _LEGACY_SPA_EXACT[clean]

    for legacy, spa in (
        ('masters/localities', 'masters/localities'),
        ('masters/areas', 'masters/areas'),
        ('masters/buildings', 'masters/buildings'),
        ('masters/tax', 'masters/tax'),
        ('masters/addresses', 'masters/addresses'),
        ('masters/addon-categories', 'masters/addon-categories'),
        ('masters/templates', 'masters/message-templates'),
        ('masters/service-providers', 'plan-master/service-providers'),
        ('zones', 'masters/zones'),
    ):
        if clean == legacy or clean.startswith(f'{legacy}/'):
            return spa

    match = re.fullmatch(r'customers/edit/(\d+)', clean)
    if match:
        return f'customers/{match.group(1)}/edit'
    match = re.fullmatch(r'customers/(\d+)(?:/(ledger|messages))?', clean)
    if match:
        customer_id, section = match.groups()
        return (f'customers/{customer_id}/ledger' if section == 'ledger'
                else f'customers/{customer_id}')
    if clean.startswith('customers/plan-status'):
        return 'customers/plan-status'

    # The React plan page owns create/edit as dialogs, rather than separate
    # documents.  Preserve the useful destination instead of serving a 500.
    if clean.startswith('plans/'):
        return 'plans'

    match = re.fullmatch(r'invoices/(\d+)(?:/(?:print|summary|detailed))?', clean)
    if match:
        return f'invoices/{match.group(1)}'

    # /payments/add/<invoice_id> was the only retired Jinja GET this map still
    # missed, so it fell through to a handler whose template is gone and
    # answered a logged-in operator with a 500. The React equivalent is the
    # invoice, which carries the Record payment action.
    match = re.fullmatch(r'payments/add/(\d+)', clean)
    if match:
        return f'invoices/{match.group(1)}'

    for legacy, spa in (
        ('inventory/vendor-bills/', 'inventory/vendor-bills'),
        ('inventory/vendors/', 'inventory/vendors'),
        ('inventory/products/', 'inventory/products'),
        ('inventory/stock/', 'inventory/stock'),
        ('expenses/', 'expenses'),
        ('staff/', 'staff'),
        ('hr/', 'hr/attendance'),
        ('settings/isp/', 'masters/isp'),
    ):
        if clean.startswith(legacy):
            return spa

    if re.fullmatch(r'customer/(?:invoice|invoices|payments)/\d+(?:/\w+)?', clean):
        return ('customer/invoices' if clean.startswith('customer/invoices/')
                or clean.startswith('customer/invoice/') else 'customer/payments')
    if clean == 'customer/payment/return':
        return 'customer/payments'
    return None


@app.before_request
def _redirect_retired_server_pages():
    """Keep old GET bookmarks working through the current React interface."""
    if request.method not in {'GET', 'HEAD'}:
        return None
    spa_path = _legacy_spa_path(request.path)
    if spa_path is None:
        return None
    target = f'/app/{spa_path}' if spa_path else '/app/'
    if request.query_string:
        target = f"{target}?{request.query_string.decode('utf-8', 'replace')}"
    return redirect(target)

@app.after_request
def _timing_end(response):
    t0 = getattr(request, '_t0', None)
    if t0 is not None and _SLOW_MS >= 0:
        elapsed = (time.perf_counter() - t0) * 1000
        if elapsed >= _SLOW_MS:
            app.logger.warning('TIMING %s %s -> %.0f ms',
                               request.method, request.path, elapsed)
    return response

# ---------- Authentication ----------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = LoginForm()

    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.is_active and user.check_password(form.password.data):
            session.permanent = True
            login_user(user, remember=form.remember.data)
            log_audit('Login', f"User {user.username} logged in")
            next_page = request.args.get('next')
            if next_page and urlsplit(next_page).netloc == '' and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('dashboard'))
        from security import record_web_login_failure
        record_web_login_failure(form.username.data or '')
        log_audit('Failed Login', f"Failed login attempt for username '{form.username.data}'")
        flash('Invalid username or password.', 'danger')
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    log_audit('Logout', f"User {current_user.username} logged out")
    logout_user()
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data):
            if form.new_password.data == form.current_password.data:
                flash('New password must be different from the current password.', 'danger')
                return render_template('profile.html', form=form)
            current_user.set_password(form.new_password.data)
            db.session.commit()
            log_audit('Change Password', f"User {current_user.username} changed password")
            flash('Password updated successfully.', 'success')
            return redirect(url_for('dashboard'))
        flash('Current password is incorrect.', 'danger')
    return render_template('profile.html', form=form)

# ---------- Dashboard helpers ----------
MODE_GROUPS = {
    'cash':   ['Cash'],
    'cheque': ['Cheque'],
    'online': ['Online Transfer', 'NEFT', 'RTGS', 'IMPS', 'UPI', 'Paytm',
               'GooglePay', 'PhonePay', 'Bank Transfer', 'Online'],
    'other':  ['Credit Card', 'Card'],
}

def _month_bounds(d):
    start = d.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return start, end

def _shift_month(d, months_back):
    y, m = d.year, d.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)

def _group_of(mode):
    for group, modes in MODE_GROUPS.items():
        if mode in modes:
            return group
    return 'other'

def _bucket_payments(payments):
    """Split a list of payments into the cash / cheque / online / other buckets."""
    out = {'cash': 0.0, 'cheque': 0.0, 'online': 0.0, 'other': 0.0}
    for p in payments:
        out[_group_of(p.payment_mode)] += float(p.amount or 0)
    return out

# ---------- Dashboard ----------
@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today()
    month_start, month_end = _month_bounds(today)

    # ===== Current month headline numbers =====
    new_customers = Customer.query.filter(
        Customer.registration_date >= month_start,
        Customer.registration_date <= month_end).count()

    month_invoices = Invoice.query.filter(
        Invoice.issue_date >= month_start,
        Invoice.issue_date <= month_end).all()
    total_invoices = len(month_invoices)
    total_amount = sum(float(i.total_amount or 0) for i in month_invoices)

    month_payments = Payment.query.filter(
        Payment.payment_date >= month_start,
        Payment.payment_date <= month_end).all()

    approved_payments = [p for p in month_payments if p.status == 'approved']
    pending_payments_month = [p for p in month_payments if p.status == 'pending']

    payment_count = len(approved_payments)
    payment_amount = sum(float(p.amount or 0) for p in approved_payments)

    # Donut + legend: Cash / Cheque / Online / Other
    payment_breakdown = _bucket_payments(approved_payments)

    # ===== To be Authorized (all pending payments, any date) =====
    all_pending = Payment.query.filter(
        Payment.status.in_(['approved', 'pending']),
        Payment.authorized_at.is_(None)
    ).all()
    to_authorize_breakdown = _bucket_payments(all_pending)
    to_authorize_total = sum(to_authorize_breakdown.values())
    to_authorize_count = len(all_pending)

    # Authorizing Payment counters
    pending_authorization_count = to_authorize_count
    authorization_done_count = Payment.query.filter(
        Payment.status == 'approved',
        Payment.authorized_at.isnot(None),
        Payment.payment_date >= month_start,
        Payment.payment_date <= month_end).count()

    # "To be Authorized" table -> grouped by date
    to_authorize_rows = {}
    for p in all_pending:
        row = to_authorize_rows.setdefault(
            p.payment_date, {'date': p.payment_date, 'count': 0, 'amount': 0.0})
        row['count'] += 1
        row['amount'] += float(p.amount or 0)
    to_authorize_rows = sorted(to_authorize_rows.values(), key=lambda r: r['date'], reverse=True)

    # ===== Plan lifecycle chips (7 days) =====
    # BATCHED: 2 GROUP BY queries replace 21 individual .count() calls.
    _chip_start = today - timedelta(days=6)
    _chip_end   = today + timedelta(days=6)
    from sqlalchemy import func as _func
    _end_counts = {r[0]: r[1] for r in
                   db.session.query(CustomerPlan.end_date, _func.count())
                   .filter(CustomerPlan.end_date >= _chip_start,
                           CustomerPlan.end_date <= _chip_end)
                   .group_by(CustomerPlan.end_date).all()}
    _start_counts = {r[0]: r[1] for r in
                     db.session.query(CustomerPlan.start_date, _func.count())
                     .filter(CustomerPlan.start_date >= _chip_start,
                             CustomerPlan.start_date <= _chip_end)
                     .group_by(CustomerPlan.start_date).all()}

    expiring_days, expired_days, renewed_days = [], [], []
    for i in range(7):
        day = today + timedelta(days=i)
        expiring_days.append({'date': day, 'label': day.strftime('%d-%b'),
                              'count': _end_counts.get(day, 0)})
    for i in range(7):
        day = today - timedelta(days=i)
        expired_days.append({'date': day, 'label': day.strftime('%d-%b'),
                             'count': _end_counts.get(day, 0) if day < today else 0})
        renewed_days.append({'date': day, 'label': day.strftime('%d-%b'),
                             'count': _start_counts.get(day, 0)})

    expiring_total = CustomerPlan.query.filter(
        CustomerPlan.status == 'active', CustomerPlan.end_date >= today).count()
    expired_total = CustomerPlan.query.filter(
        CustomerPlan.status.in_(['active', 'expired']), CustomerPlan.end_date < today).count()
    renewed_total = CustomerPlan.query.filter(
        CustomerPlan.start_date >= month_start, CustomerPlan.start_date <= month_end).count()

    # ===== Plans expiring in the next 7 days -> full detail rows =====
    # BATCHED: 1 invoice fetch replaces N per-customer queries.
    horizon = today + timedelta(days=7)
    expiring_plans = (CustomerPlan.query
                      .filter(CustomerPlan.status == 'active',
                              CustomerPlan.end_date >= today,
                              CustomerPlan.end_date <= horizon)
                      .order_by(CustomerPlan.end_date.asc())
                      .all())
    _exp_cust_ids = [cp.customer.id for cp in expiring_plans if cp.customer]
    _exp_invs = (Invoice.query
                 .filter(Invoice.customer_id.in_(_exp_cust_ids),
                         Invoice.status.in_(['draft', 'sent', 'overdue']))
                 .all()) if _exp_cust_ids else []
    _outstanding_map = {}
    for _inv in _exp_invs:
        _outstanding_map[_inv.customer_id] = (
            _outstanding_map.get(_inv.customer_id, 0.0) + float(_inv.balance))
    expiring_rows = []
    for cp in expiring_plans:
        cust = cp.customer
        if not cust:
            continue
        expiring_rows.append({
            'plan': cp,
            'customer': cust,
            'plan_name': cp.plan.name if cp.plan else '-',
            'price': float(cp.effective_price),
            'renew_date': cp.start_date,
            'expiry_date': cp.end_date,
            'days_left': (cp.end_date - today).days,
            'outstanding': _outstanding_map.get(cust.id, 0.0),
        })

    # ===== 12-month summary table =====
    # BATCHED: 2 bulk fetches replace 24 individual queries (12 invoices + 12 counts).
    _oldest_month = _shift_month(today, 11)
    _cur_month_end = (today.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    _all12_invs = Invoice.query.filter(
        Invoice.issue_date >= _oldest_month,
        Invoice.issue_date <= _cur_month_end).all()
    _all12_custs = Customer.query.filter(
        Customer.registration_date >= _oldest_month,
        Customer.registration_date <= _cur_month_end).all()
    monthly_summary = []
    for i in range(0, 12):
        m_start = _shift_month(today, i)
        m_end = (m_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        invs    = [x for x in _all12_invs if m_start <= x.issue_date <= m_end]
        paid    = [x for x in invs if x.status == 'paid']
        pending = [x for x in invs if x.status in ('draft', 'sent', 'overdue')]
        nc      = sum(1 for c in _all12_custs if m_start <= c.registration_date <= m_end)
        monthly_summary.append({
            'month': m_start.strftime('%b,%y'),
            'month_key': m_start.isoformat(),
            'new_clients': nc,
            'total_bills': len(invs),
            'total_amount': sum(float(x.total_amount or 0) for x in invs),
            'pending_bills': len(pending),
            'pending_amount': sum(x.balance for x in pending),
            'paid_bills': len(paid),
            'paid_amount': sum(float(x.total_amount or 0) for x in paid),
        })

    # ===== Zone-wise outstanding & collection (current month) =====
    # BATCHED: 2 queries replace 2*N (one pair per zone).
    _zone_cust_map = {}
    for _c in Customer.query.filter(Customer.zone.isnot(None)).all():
        _zone_cust_map.setdefault(_c.zone, []).append(_c.id)
    _all_zcids = [cid for ids in _zone_cust_map.values() for cid in ids]
    _cust_zone  = {cid: z for z, ids in _zone_cust_map.items() for cid in ids}

    _z_invs = (Invoice.query
               .filter(Invoice.customer_id.in_(_all_zcids),
                       Invoice.status.in_(['draft', 'sent', 'overdue']))
               .all()) if _all_zcids else []
    _z_pays = (Payment.query
               .filter(Payment.customer_id.in_(_all_zcids),
                       Payment.status == 'approved',
                       Payment.payment_date >= month_start,
                       Payment.payment_date <= month_end)
               .all()) if _all_zcids else []

    _zi, _zp = {}, {}
    for _inv in _z_invs: _zi.setdefault(_cust_zone[_inv.customer_id], []).append(_inv)
    for _pay in _z_pays: _zp.setdefault(_cust_zone[_pay.customer_id], []).append(_pay)

    zone_outstanding, zone_collection = [], []
    for zone_name in sorted(_zone_cust_map.keys()):
        _unpaid = _zi.get(zone_name, [])
        if _unpaid:
            zone_outstanding.append({'zone': zone_name, 'count': len(_unpaid),
                                     'amount': sum(inv.balance for inv in _unpaid)})
        _pays = _zp.get(zone_name, [])
        if _pays:
            zone_collection.append({'zone': zone_name, 'count': len(_pays),
                                    'amount': sum(float(p.amount or 0) for p in _pays)})

    return render_template(
        'dashboard.html',
        today=today,
        new_customers=new_customers,
        total_invoices=total_invoices,
        total_amount=total_amount,
        payment_count=payment_count,
        payment_amount=payment_amount,
        payment_breakdown=payment_breakdown,
        to_authorize_total=to_authorize_total,
        to_authorize_count=to_authorize_count,
        to_authorize_breakdown=to_authorize_breakdown,
        to_authorize_rows=to_authorize_rows,
        pending_authorization_count=pending_authorization_count,
        authorization_done_count=authorization_done_count,
        expiring_days=expiring_days,
        expired_days=expired_days,
        renewed_days=renewed_days,
        expiring_total=expiring_total,
        expired_total=expired_total,
        renewed_total=renewed_total,
        expiring_rows=expiring_rows,
        monthly_summary=monthly_summary,
        zone_outstanding=zone_outstanding,
        zone_collection=zone_collection,
    )

@app.route('/dashboard/export')
@login_required
def dashboard_export():
    today = date.today()
    month_start, month_end = _month_bounds(today)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['UniCRM Dashboard Snapshot', today.isoformat()])
    writer.writerow([])
    writer.writerow(['Metric', 'Count', 'Amount'])

    invs = Invoice.query.filter(Invoice.issue_date >= month_start,
                                Invoice.issue_date <= month_end).all()
    pays = Payment.query.filter(Payment.payment_date >= month_start,
                                Payment.payment_date <= month_end,
                                Payment.status == 'approved').all()
    writer.writerow(['New Customers (this month)',
                     Customer.query.filter(Customer.registration_date >= month_start,
                                           Customer.registration_date <= month_end).count(), ''])
    writer.writerow(['Invoices (this month)', len(invs),
                     sum(float(i.total_amount or 0) for i in invs)])
    writer.writerow(['Payments (this month)', len(pays),
                     sum(float(p.amount or 0) for p in pays)])
    buckets = _bucket_payments(pays)
    for label in ('cash', 'cheque', 'online', 'other'):
        writer.writerow([f'  {label.title()}', '', buckets[label]])

    writer.writerow([])
    writer.writerow(['Month', 'New Clients', 'Total Bills', 'Total Amount',
                     'Pending Bills', 'Pending Amount', 'Paid Bills', 'Paid Amount'])
    for i in range(0, 12):
        m_start = _shift_month(today, i)
        m_end = (m_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        m_invs = Invoice.query.filter(Invoice.issue_date >= m_start,
                                      Invoice.issue_date <= m_end).all()
        m_paid = [x for x in m_invs if x.status == 'paid']
        m_pending = [x for x in m_invs if x.status in ('draft', 'sent', 'overdue')]
        writer.writerow([
            m_start.strftime('%b,%y'),
            Customer.query.filter(Customer.registration_date >= m_start,
                                  Customer.registration_date <= m_end).count(),
            len(m_invs), sum(float(x.total_amount or 0) for x in m_invs),
            len(m_pending), sum(x.balance for x in m_pending),
            len(m_paid), sum(float(x.total_amount or 0) for x in m_paid),
        ])

    log_audit('Export Dashboard', f"User {current_user.username} exported dashboard snapshot")
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=dashboard_snapshot_{today.isoformat()}.csv'}
    )

# ---------- Plan expiry / renewal reports (dashboard drill-down) ----------
@app.route('/reports/plan-expiry')
@login_required
def plan_expiry_report():
    """Plans expiring, expired or renewed. ?kind=expiring|expired|renewed&days=7&on=YYYY-MM-DD"""
    today = date.today()
    kind = request.args.get('kind', 'expiring')
    days = request.args.get('days', 7, type=int)
    on = request.args.get('on')
    on_date = None
    if on:
        try:
            on_date = datetime.strptime(on, '%Y-%m-%d').date()
        except ValueError:
            on_date = None

    q = CustomerPlan.query
    if kind == 'expired':
        if on_date:
            q = q.filter(CustomerPlan.end_date == on_date)
        else:
            q = q.filter(CustomerPlan.end_date < today,
                         CustomerPlan.end_date >= today - timedelta(days=days))
        q = q.order_by(CustomerPlan.end_date.desc())
        heading = 'Plan Expired'
    elif kind == 'renewed':
        if on_date:
            q = q.filter(CustomerPlan.start_date == on_date)
        else:
            q = q.filter(CustomerPlan.start_date >= today - timedelta(days=days),
                         CustomerPlan.start_date <= today)
        q = q.order_by(CustomerPlan.start_date.desc())
        heading = 'Plan Renewed'
    else:
        kind = 'expiring'
        if on_date:
            q = q.filter(CustomerPlan.end_date == on_date, CustomerPlan.status == 'active')
        else:
            q = q.filter(CustomerPlan.status == 'active',
                         CustomerPlan.end_date >= today,
                         CustomerPlan.end_date <= today + timedelta(days=days))
        q = q.order_by(CustomerPlan.end_date.asc())
        heading = 'Plan Expiring'

    plans = q.all()
    rows = []
    for cp in plans:
        cust = cp.customer
        if not cust:
            continue
        outstanding = sum(
            inv.balance for inv in Invoice.query.filter(
                Invoice.customer_id == cust.id,
                Invoice.status.in_(['draft', 'sent', 'overdue'])).all())
        rows.append({
            'plan': cp, 'customer': cust,
            'plan_name': cp.plan.name if cp.plan else '-',
            'price': float(cp.effective_price),
            'renew_date': cp.start_date, 'expiry_date': cp.end_date,
            'days_left': (cp.end_date - today).days,
            'outstanding': outstanding,
        })
    return render_template('reports/plan_expiry.html', rows=rows, kind=kind,
                           heading=heading, days=days, on_date=on_date, today=today)

@app.route('/customer-plans/<int:plan_id>/dates', methods=['POST'])
@login_required
@admin_required
def customer_plan_update_dates(plan_id):
    """Edit a customer's renew (start) and expiry (end) date inline."""
    cp = CustomerPlan.query.get_or_404(plan_id)
    start_raw = request.form.get('start_date')
    end_raw = request.form.get('end_date')
    status = request.form.get('status')
    try:
        if start_raw:
            cp.start_date = datetime.strptime(start_raw, '%Y-%m-%d').date()
        if end_raw:
            cp.end_date = datetime.strptime(end_raw, '%Y-%m-%d').date()
    except ValueError:
        flash('Dates must be in YYYY-MM-DD format.', 'danger')
        return redirect(request.referrer or url_for('dashboard'))

    if cp.end_date < cp.start_date:
        flash('Expiry date cannot be before the renew date.', 'danger')
        return redirect(request.referrer or url_for('dashboard'))

    if status in ('active', 'expired', 'cancelled', 'terminated'):
        cp.status = status

        # NEW: If plan is cancelled, cancel all unpaid invoices for this customer
        if status == 'cancelled':
            unpaid_invoices = Invoice.query.filter(
                Invoice.customer_id == cp.customer_id,
                Invoice.status.in_(['draft', 'sent', 'overdue'])
            ).all()
            for inv in unpaid_invoices:
                inv.status = 'cancelled'

    db.session.commit()
    log_audit('Update Plan Dates',
              f"Plan #{cp.id} for customer {cp.customer_id}: "
              f"{cp.start_date} -> {cp.end_date} ({cp.status})")
    flash('Plan dates updated.', 'success')
    return redirect(request.referrer or url_for('customer_view', id=cp.customer_id))

# ---------- Payment authorization queue ----------
@app.route('/payments/authorizations')
@login_required
def payment_authorizations():
    # Everything that has been collected (by staff at the counter or by the
    # customer through the portal) but not yet signed off by an admin. The
    # money is already on the customer's account either way - this queue is a
    # review step, not a gate.
    pending = (Payment.query
               .filter(Payment.status.in_(['approved', 'pending']),
                       Payment.authorized_at.is_(None))
               .order_by(Payment.payment_date.desc(), Payment.id.desc())
               .all())
    done = (Payment.query
            .filter(Payment.status == 'approved',
                    Payment.authorized_at.isnot(None))
            .order_by(Payment.authorized_at.desc())
            .limit(200).all())

    pending_total = sum(float(p.amount or 0) for p in pending)
    portal_total = sum(float(p.amount or 0) for p in pending
                       if (p.source or '') == 'portal')

    return render_template('payments/authorizations.html',
                           pending=pending, done=done,
                           pending_total=pending_total,
                           portal_total=portal_total,
                           today=date.today())

@app.route('/payments/<int:id>/reject', methods=['POST'])
@login_required
@admin_required
def payment_reject(id):
    payment = db.session.query(Payment).with_for_update().get_or_404(id)
    reason = (request.form.get('reason') or '').strip()
    ok, _ = payment_service.reject_payment(payment, current_user, reason)
    if not ok:
        flash('That payment is already rejected.', 'info')
        return redirect(request.referrer or url_for('payment_authorizations'))
    log_audit('Reject Payment',
              f"Rejected payment #{id}: {payment.rejection_reason}")
    flash('Payment rejected.', 'warning')
    return redirect(request.referrer or url_for('payment_authorizations'))

# ---------- Zone CRUD ----------
@app.route('/zones')
@login_required
@admin_required
def zone_list():
    zones = Zone.query.all()
    return render_template('zones/list.html', zones=zones)

@app.route('/zones/add', methods=['GET', 'POST'])
@login_required
@admin_required
def zone_add():
    form = ZoneForm()
    if form.validate_on_submit():
        logo_filename = None
        if form.logo.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.logo.data, public_id='zone-logo')
                logo_filename = url or None
            else:
                file = form.logo.data
                filename = secure_filename(file.filename)
                logo_filename = filename
                file.save(os.path.join(app.root_path, 'static', 'uploads', filename))
        zone = Zone(
            name=form.name.data,
            code=form.code.data,
            phone=form.phone.data,
            email=form.email.data,
            address=form.address.data,
            city=form.city.data,
            state=form.state.data,
            country=form.country.data,
            logo=logo_filename,
            l2s_site_name=form.l2s_site_name.data,
            nas=form.nas.data,
            l2s_sync_id=form.l2s_sync_id.data,
            sms_url=form.sms_url.data,
            http_url=form.http_url.data,
            whatsapp_url=form.whatsapp_url.data,
            whatsapp_attachment_url=form.whatsapp_attachment_url.data,
            company=form.company.data
        )
        db.session.add(zone)
        db.session.commit()
        log_audit('Add Zone', f"Added zone {zone.name}")
        flash('Zone added successfully.', 'success')
        return redirect(url_for('zone_list'))
    return render_template('zones/add.html', form=form)

@app.route('/zones/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def zone_edit(id):
    zone = Zone.query.get_or_404(id)
    form = ZoneForm(obj=zone)
    if form.validate_on_submit():
        if form.logo.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.logo.data, public_id=f'zone-{id}-logo')
                zone.logo = url or zone.logo
            else:
                file = form.logo.data
                filename = secure_filename(file.filename)
                zone.logo = filename
                file.save(os.path.join(app.root_path, 'static', 'uploads', filename))
        form.populate_obj(zone)
        db.session.commit()
        log_audit('Edit Zone', f"Edited zone {zone.name}")
        flash('Zone updated successfully.', 'success')
        return redirect(url_for('zone_list'))
    return render_template('zones/edit.html', form=form, zone=zone)

@app.route('/zones/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def zone_delete(id):
    zone = Zone.query.get_or_404(id)
    db.session.delete(zone)
    db.session.commit()
    log_audit('Delete Zone', f"Deleted zone {zone.name}")
    flash('Zone deleted successfully.', 'success')
    return redirect(url_for('zone_list'))

def _master_add(Model, list_view, form_tpl, audit_label, success_msg):
    """Generic add handler for single-field master tables (name only)."""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            obj = Model(name=name)
            db.session.add(obj)
            db.session.commit()
            log_audit(f'Add {audit_label}', f"Added {audit_label.lower()} {name}")
            flash(success_msg, 'success')
            return redirect(url_for(list_view))
    return render_template(form_tpl)

def _master_edit(Model, list_view, form_tpl, audit_label, success_msg, id):
    obj = Model.query.get_or_404(id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            obj.name = name
            db.session.commit()
            log_audit(f'Edit {audit_label}', f"Edited {audit_label.lower()} {obj.name}")
            flash(success_msg, 'success')
            return redirect(url_for(list_view))
    return render_template(form_tpl, **{audit_label.lower(): obj})

def _master_delete(Model, list_view, audit_label, id):
    obj = Model.query.get_or_404(id)
    db.session.delete(obj)
    db.session.commit()
    log_audit(f'Delete {audit_label}', f"Deleted {audit_label.lower()} {obj.name}")
    flash(f'{audit_label} deleted.', 'success')
    return redirect(url_for(list_view))

# ---------- Locality Master CRUD ----------
@app.route('/masters/localities')
@login_required
@admin_required
def locality_list():
    localities = Locality.query.all()
    return render_template('masters/locality_list.html', localities=localities)

@app.route('/masters/localities/add', methods=['GET', 'POST'])
@login_required
@admin_required
def locality_add():
    return _master_add(Locality, 'locality_list', 'masters/locality_form.html', 'Locality', 'Locality added successfully.')

@app.route('/masters/localities/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def locality_edit(id):
    return _master_edit(Locality, 'locality_list', 'masters/locality_form.html', 'Locality', 'Locality updated successfully.', id)

@app.route('/masters/localities/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def locality_delete(id):
    return _master_delete(Locality, 'locality_list', 'Locality', id)

# ---------- Area Master CRUD ----------
@app.route('/masters/areas')
@login_required
@admin_required
def area_list():
    areas = Area.query.all()
    return render_template('masters/area_list.html', areas=areas)

@app.route('/masters/areas/add', methods=['GET', 'POST'])
@login_required
@admin_required
def area_add():
    return _master_add(Area, 'area_list', 'masters/area_form.html', 'Area', 'Area added successfully.')

@app.route('/masters/areas/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def area_edit(id):
    return _master_edit(Area, 'area_list', 'masters/area_form.html', 'Area', 'Area updated successfully.', id)

@app.route('/masters/areas/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def area_delete(id):
    return _master_delete(Area, 'area_list', 'Area', id)

# ---------- Building Master CRUD ----------
@app.route('/masters/buildings')
@login_required
@admin_required
def building_list():
    buildings = Building.query.all()
    return render_template('masters/building_list.html', buildings=buildings)

@app.route('/masters/buildings/add', methods=['GET', 'POST'])
@login_required
@admin_required
def building_add():
    return _master_add(Building, 'building_list', 'masters/building_form.html', 'Building', 'Building added successfully.')

@app.route('/masters/buildings/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def building_edit(id):
    return _master_edit(Building, 'building_list', 'masters/building_form.html', 'Building', 'Building updated successfully.', id)

@app.route('/masters/buildings/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def building_delete(id):
    return _master_delete(Building, 'building_list', 'Building', id)

# ---------- Company CRUD ----------
@app.route('/masters/company', methods=['GET', 'POST'])
@login_required
@admin_required
def company_edit():
    company = Company.query.first() or Company()
    form = CompanyForm(obj=company)
    form.zones.choices = [(z.id, z.name) for z in Zone.query.all()]
    if form.validate_on_submit():
        company.name = form.name.data
        company.mobile = form.mobile.data
        company.phone = form.phone.data
        company.email = form.email.data
        company.address = form.address.data
        company.bank_account_details = form.bank_account_details.data
        company.gstin = form.gstin.data
        company.pan_no = form.pan_no.data
        company.sac_no = form.sac_no.data
        company.place_of_supply = form.place_of_supply.data
        company.state_code = form.state_code.data
        company.b2b_invoice_series = form.b2b_invoice_series.data
        company.b2c_invoice_series = form.b2c_invoice_series.data
        company.website_url = form.website_url.data
        company.company_type = form.company_type.data
        company.invoice_notes = form.invoice_notes.data
        if form.company_logo.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.company_logo.data, public_id='company-logo')
                company.company_logo = url or company.company_logo
            else:
                file = form.company_logo.data
                filename = secure_filename(file.filename)
                company.company_logo = filename
                file.save(os.path.join(app.root_path, 'static', 'uploads', filename))
        selected_zone_ids = request.form.getlist('zones') if request.form.getlist('zones') else []
        company.zones = Zone.query.filter(Zone.id.in_(selected_zone_ids)).all()
        db.session.add(company)
        db.session.commit()
        log_audit('Edit Company', "Updated company details")
        flash('Company details updated.', 'success')
        return redirect(url_for('masters_index'))
    return render_template('masters/company.html', form=form, company=company)

# ---------- Tax Master CRUD ----------
@app.route('/masters/tax')
@login_required
@admin_required
def tax_list():
    taxes = TaxMaster.query.all()
    return render_template('masters/tax_list.html', taxes=taxes)

@app.route('/masters/tax/add', methods=['GET', 'POST'])
@login_required
@admin_required
def tax_add():
    form = TaxForm()
    if form.validate_on_submit():
        tax = TaxMaster(name=form.name.data, value=form.value.data)
        db.session.add(tax)
        db.session.commit()
        log_audit('Add Tax', f"Added tax {tax.name}")
        flash('Tax added successfully.', 'success')
        return redirect(url_for('tax_list'))
    return render_template('masters/tax_form.html', form=form)

@app.route('/masters/tax/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def tax_edit(id):
    tax = TaxMaster.query.get_or_404(id)
    form = TaxForm(obj=tax)
    if form.validate_on_submit():
        form.populate_obj(tax)
        db.session.commit()
        log_audit('Edit Tax', f"Edited tax {tax.name}")
        flash('Tax updated successfully.', 'success')
        return redirect(url_for('tax_list'))
    return render_template('masters/tax_form.html', form=form, tax=tax)

@app.route('/masters/tax/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def tax_delete(id):
    tax = TaxMaster.query.get_or_404(id)
    db.session.delete(tax)
    db.session.commit()
    log_audit('Delete Tax', f"Deleted tax {tax.name}")
    flash('Tax deleted successfully.', 'success')
    return redirect(url_for('tax_list'))

# ---------- Address Master ----------
@app.route('/masters/addresses')
@login_required
@admin_required
def address_list():
    addresses = Address.query.all()
    return render_template('masters/address_list.html', addresses=addresses)

@app.route('/masters/addresses/add', methods=['GET', 'POST'])
@login_required
@admin_required
def address_add():
    if request.method == 'POST':
        name = request.form.get('name')
        line1 = request.form.get('line1')
        line2 = request.form.get('line2')
        address_line = line1
        if line2:
            address_line += ', ' + line2
        city = request.form.get('city')
        state = request.form.get('state')
        pincode = request.form.get('pincode')
        address = Address(
            name=name,
            address_line=address_line,
            city=city,
            state=state,
            pincode=pincode
        )
        db.session.add(address)
        db.session.commit()
        log_audit('Add Address', f"Added address in {city}")
        flash('Address added successfully! You can add another one.', 'success')
        return redirect(url_for('address_add'))
    return render_template('masters/address_form.html')

@app.route('/masters/addresses/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def address_edit(id):
    address = Address.query.get_or_404(id)
    if request.method == 'POST':
        address.name = request.form.get('name')
        line1 = request.form.get('line1')
        line2 = request.form.get('line2')
        address.address_line = line1
        if line2:
            address.address_line += ', ' + line2
        address.city = request.form.get('city')
        address.state = request.form.get('state')
        address.pincode = request.form.get('pincode')
        db.session.commit()
        log_audit('Edit Address', f"Edited address #{id}")
        flash('Address updated successfully.', 'success')
        return redirect(url_for('address_list'))
    return render_template('masters/address_form.html', address=address)

@app.route('/masters/addresses/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def address_delete(id):
    address = Address.query.get_or_404(id)
    db.session.delete(address)
    db.session.commit()
    log_audit('Delete Address', f"Deleted address #{id}")
    flash('Address deleted.', 'success')
    return redirect(url_for('address_list'))

# ---------- Masters Index ----------
@app.route('/masters')
@login_required
def masters_index():
    return render_template('masters/index.html')


# ---------- Addon Invoice Categories ----------
@app.route('/masters/addon-categories')
@login_required
@admin_required
def addon_category_list():
    from models import AddonCategory
    categories = AddonCategory.query.order_by(AddonCategory.name).all()
    return render_template('masters/addon_categories.html', categories=categories)

@app.route('/masters/addon-categories/add', methods=['GET', 'POST'])
@login_required
@admin_required
def addon_category_add():
    from models import AddonCategory
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        price = request.form.get('price', type=float) or 0
        description = (request.form.get('description') or '').strip()
        if name:
            cat = AddonCategory(name=name, default_price=price, description=description)
            db.session.add(cat)
            db.session.commit()
            log_audit('Add Addon Category', f"Added addon category {name}")
            flash('Addon category added.', 'success')
            return redirect(url_for('addon_category_list'))
        flash('Name is required.', 'danger')
    return render_template('masters/addon_category_form.html')

@app.route('/masters/addon-categories/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def addon_category_edit(id):
    from models import AddonCategory
    cat = AddonCategory.query.get_or_404(id)
    if request.method == 'POST':
        cat.name = (request.form.get('name') or '').strip()
        cat.default_price = request.form.get('price', type=float) or 0
        cat.description = (request.form.get('description') or '').strip()
        db.session.commit()
        log_audit('Edit Addon Category', f"Edited addon category {cat.name}")
        flash('Addon category updated.', 'success')
        return redirect(url_for('addon_category_list'))
    return render_template('masters/addon_category_form.html', category=cat)

@app.route('/masters/addon-categories/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def addon_category_delete(id):
    from models import AddonCategory
    cat = AddonCategory.query.get_or_404(id)
    db.session.delete(cat)
    db.session.commit()
    log_audit('Delete Addon Category', f"Deleted addon category {cat.name}")
    flash('Addon category deleted.', 'success')
    return redirect(url_for('addon_category_list'))

@app.route('/api/addon-categories')
@login_required
def api_addon_categories():
    """JSON endpoint for the addon invoice form to fetch category presets."""
    from models import AddonCategory
    cats = AddonCategory.query.filter_by(is_active=True).order_by(AddonCategory.name).all()
    return jsonify(categories=[{
        'id': c.id, 'name': c.name,
        'price': float(c.default_price or 0),
        'description': c.description or ''
    } for c in cats])

# ---------- Message Templates CRUD (NEW) ----------
@app.route('/masters/templates')
@login_required
@admin_required
def message_template_list():
    templates = MessageTemplate.query.order_by(MessageTemplate.name).all()
    return render_template('masters/message_templates.html',
                           templates=templates,
                           placeholders=MESSAGE_PLACEHOLDERS,
                           gateway_ready=messaging.is_configured())

#: Shown in the editor so operators know exactly what they can drop into a body.
MESSAGE_PLACEHOLDERS = [
    ('{{customer_name}}', "Customer's full name, e.g. Mr. Sumedh Chabukswar"),
    ('{{first_name}}',    'First name only'),
    ('{{username}}',      'Login user name, e.g. rizwan@yn'),
    ('{{mobile}}',        'Registered mobile number'),
    ('{{plan_name}}',     'Current plan name'),
    ('{{speed}}',         'Plan speed in Mbps'),
    ('{{amount}}',        'Invoice / plan amount'),
    ('{{due_amount}}',    'Amount still outstanding'),
    ('{{balance}}',       'Balance after the payment'),
    ('{{paid_amount}}',   'Amount the customer just paid'),
    ('{{days}}',          'Days left before expiry'),
    ('{{expiry_date}}',   'Plan expiry date'),
    ('{{renew_date}}',    'Plan start / renew date'),
    ('{{invoice_no}}',    'Invoice number'),
    ('{{receipt_no}}',    'Receipt number'),
    ('{{transaction_id}}','Online payment transaction ID'),
    ('{{company_name}}',  'Your company name'),
    ('{{company_phone}}', 'Your contact number'),
    ('{{app_link}}',      'App download link'),
    ('{{web_link}}',      'Website link'),
    ('{{today}}',         "Today's date"),
]

def _template_form_ctx(template=None):
    return dict(template=template,
                placeholders=MESSAGE_PLACEHOLDERS,
                system_types=MessageTemplate.SYSTEM_TYPES)

@app.route('/masters/templates/add', methods=['GET', 'POST'])
@login_required
@admin_required
def message_template_add():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        template_type = (request.form.get('template_type') or '').strip().lower()
        body = (request.form.get('body') or '').strip()
        description = (request.form.get('description') or '').strip()
        channel = request.form.get('channel') or 'whatsapp'
        is_active = 'is_active' in request.form

        if not name or not template_type or not body:
            flash('Name, type and message body are all required.', 'danger')
            return render_template('masters/message_template_form.html',
                                   **_template_form_ctx())
        if MessageTemplate.query.filter_by(template_type=template_type).first():
            flash(f'A template of type "{template_type}" already exists. '
                  f'Edit that one instead.', 'danger')
            return render_template('masters/message_template_form.html',
                                   **_template_form_ctx())

        mt = MessageTemplate(name=name, template_type=template_type, body=body,
                             description=description, channel=channel,
                             is_active=is_active)
        db.session.add(mt)
        db.session.commit()
        log_audit('Add Template', f"Added message template {name} ({template_type})")
        flash('Template added.', 'success')
        return redirect(url_for('message_template_list'))

    return render_template('masters/message_template_form.html',
                           **_template_form_ctx())

@app.route('/masters/templates/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def message_template_edit(id):
    mt = MessageTemplate.query.get_or_404(id)
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        body = (request.form.get('body') or '').strip()
        if not name or not body:
            flash('Name and message body are both required.', 'danger')
            return render_template('masters/message_template_form.html',
                                   **_template_form_ctx(mt))

        mt.name = name
        mt.body = body
        mt.description = (request.form.get('description') or '').strip()
        mt.channel = request.form.get('channel') or 'whatsapp'
        mt.is_active = 'is_active' in request.form

        # The type is the key the application code looks templates up by, so it
        # can only be changed on templates the system does not send itself.
        new_type = (request.form.get('template_type') or '').strip().lower()
        if not mt.is_system and new_type and new_type != mt.template_type:
            clash = MessageTemplate.query.filter(
                MessageTemplate.template_type == new_type,
                MessageTemplate.id != mt.id).first()
            if clash:
                flash(f'Another template already uses the type "{new_type}".', 'danger')
                return render_template('masters/message_template_form.html',
                                       **_template_form_ctx(mt))
            mt.template_type = new_type

        db.session.commit()
        log_audit('Edit Template', f"Edited message template {mt.name}")
        flash('Template updated.', 'success')
        return redirect(url_for('message_template_list'))

    return render_template('masters/message_template_form.html',
                           **_template_form_ctx(mt))

@app.route('/masters/templates/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def message_template_delete(id):
    mt = MessageTemplate.query.get_or_404(id)
    if mt.is_system:
        flash(f'"{mt.name}" is sent automatically by the system, so it cannot be '
              f'deleted. Switch it off instead if you do not want it sent.', 'warning')
        return redirect(url_for('message_template_list'))
    name = mt.name
    db.session.delete(mt)
    db.session.commit()
    log_audit('Delete Template', f"Deleted message template {name}")
    flash('Template deleted.', 'success')
    return redirect(url_for('message_template_list'))

@app.route('/masters/templates/<int:id>/test', methods=['POST'])
@login_required
@admin_required
def message_template_test(id):
    """Send one template to a number you type in, to check the wording."""
    mt = MessageTemplate.query.get_or_404(id)
    phone = (request.form.get('phone') or '').strip()
    if not phone:
        flash('Enter a mobile number to send the test to.', 'warning')
        return redirect(url_for('message_template_list'))

    sample = Customer.query.filter(Customer.mobile.isnot(None)).first()
    ctx = messaging.build_context(customer=sample, extra={
        'amount': 500, 'due_amount': 500, 'balance': 0, 'paid_amount': 500,
        'days': 3, 'invoice_no': 'INV-SAMPLE-0001', 'plan_name': 'Sample Plan',
    })
    body = messaging.render(mt.body, ctx)
    result = messaging.send_whatsapp(phone, body, template_type=f"test:{mt.template_type}")

    if result.status == 'sent':
        flash(f'Test message sent to {phone}.', 'success')
    elif result.status == 'dry-run':
        flash('Gateway not configured yet, so the test was logged instead of sent.',
              'warning')
    else:
        flash(f'Test failed: {result.detail}', 'danger')
    return redirect(url_for('message_template_list'))

@app.route('/masters/templates/send-bulk', methods=['POST'])
@login_required
@admin_required
def send_bulk_message():
    template_id = request.form.get('template_id', type=int)
    start_date_str = request.form.get('start_date')
    end_date_str = request.form.get('end_date')
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('Invalid date format. Use YYYY-MM-DD.', 'danger')
        return redirect(url_for('message_template_list'))

    template = MessageTemplate.query.get(template_id)
    if not template:
        flash('Template not found.', 'danger')
        return redirect(url_for('message_template_list'))

    plans = CustomerPlan.query.filter(
        CustomerPlan.end_date >= start_date,
        CustomerPlan.end_date <= end_date
    ).all()

    sent_count = 0
    for cp in plans:
        if cp.customer and cp.customer.mobile:
            send_template_message(cp.customer, template.template_type)
            sent_count += 1

    log_audit('Bulk Message', f"Sent {template.name} to {sent_count} customers")
    flash(f'Message sent to {sent_count} customers.', 'success')
    return redirect(url_for('message_template_list'))

# ---------- Customer CRUD ----------
@app.route('/customers')
@login_required
def customer_list():
    page = request.args.get('page', 1, type=int)
    per_page = 25
    pagination = Customer.query.order_by(Customer.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template('customers/list.html', customers=pagination.items, pagination=pagination)

@app.route('/customers/search')
@login_required
def customer_search():
    from blueprints.api.utils import escape_like
    q = (request.args.get('q') or '').strip()
    if not q:
        return redirect(url_for('customer_list'))
    like = f"%{escape_like(q)}%"
    results = Customer.query.filter(
        db.or_(
            Customer.first_name.ilike(like, escape='\\'),
            Customer.last_name.ilike(like, escape='\\'),
            Customer.mobile.ilike(like, escape='\\'),
            Customer.email.ilike(like, escape='\\'),
            Customer.reference_id.ilike(like, escape='\\'),
        )
    ).limit(50).all()
    return render_template('customers/list.html', customers=results, pagination=None, search_query=q)

# ===== CUSTOMER ADD =====
@app.route('/customers/add', methods=['GET', 'POST'])
@login_required
@admin_required
def customer_add():
    form = CustomerForm()

    form.zone.choices = [(z.name, z.name) for z in Zone.query.all()]
    form.locality.choices = [(l.name, l.name) for l in Locality.query.all()]
    form.area.choices = [(a.name, a.name) for a in Area.query.all()]
    form.building.choices = [(b.name, b.name) for b in Building.query.all()]

    plans = Plan.query.filter_by(is_active=True).all()
    ref_id = generate_reference_id()

    if form.validate_on_submit():
        ref_id = generate_reference_id()
        while Customer.query.filter_by(reference_id=ref_id).first():
            ref_id = generate_reference_id()

        username = form.username.data.strip() if form.username.data else None
        if not username:
            for _ in range(100):
                candidate = f"cust_{secrets.token_hex(4)}"
                if not User.query.filter_by(username=candidate).first() and not Customer.query.filter_by(username=candidate).first():
                    username = candidate
                    break
            else:
                flash('Could not generate a unique username. Please try again.', 'danger')
                return render_template('customers/add.html', form=form, plans=plans, date=date, ref_id=ref_id)
        else:
            if User.query.filter_by(username=username).first() or Customer.query.filter_by(username=username).first():
                flash(f'Username "{username}" is already taken. Please choose another.', 'danger')
                return render_template('customers/add.html', form=form, plans=plans, date=date, ref_id=ref_id)

        password = form.password.data
        if not password:
            flash('Password is required. Please set a password for the customer.', 'danger')
            return render_template('customers/add.html', form=form, plans=plans, date=date, ref_id=ref_id)
        password_hash = generate_password_hash(password)

        reg_form_filename = None
        if form.reg_form.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.reg_form.data, public_id=f'cust-regform-{secrets.token_hex(4)}')
                reg_form_filename = url or None
            else:
                file = form.reg_form.data
                reg_form_filename = secure_filename(file.filename)
                file.save(os.path.join(app.root_path, 'static', 'uploads', reg_form_filename))
        photo_filename = None
        if form.photo.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.photo.data, public_id=f'cust-photo-{secrets.token_hex(4)}')
                photo_filename = url or None
            else:
                file = form.photo.data
                photo_filename = secure_filename(file.filename)
                file.save(os.path.join(app.root_path, 'static', 'uploads', photo_filename))
        address_proof_filename = None
        if form.address_proof.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.address_proof.data, public_id=f'cust-address-{secrets.token_hex(4)}')
                address_proof_filename = url or None
            else:
                file = form.address_proof.data
                address_proof_filename = secure_filename(file.filename)
                file.save(os.path.join(app.root_path, 'static', 'uploads', address_proof_filename))
        id_proof_filename = None
        if form.id_proof.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.id_proof.data, public_id=f'cust-idproof-{secrets.token_hex(4)}')
                id_proof_filename = url or None
            else:
                file = form.id_proof.data
                id_proof_filename = secure_filename(file.filename)
                file.save(os.path.join(app.root_path, 'static', 'uploads', id_proof_filename))

        try:
            customer = Customer(
                title=form.title.data,
                customer_type=form.customer_type.data,
                company_name=form.company_name.data if form.customer_type.data in ('Company', 'Enterprise', 'Commercial') else None,
                first_name=form.first_name.data,
                middle_name=form.middle_name.data,
                last_name=form.last_name.data,
                email=form.email.data,
                home_phone=form.home_phone.data,
                mobile=form.mobile.data,
                username=username,
                password_hash=password_hash,
                gstin=form.gstin.data,
                pan=form.pan.data,
                aadhar=form.aadhar.data,
                tax_type=form.tax_type.data,
                connection_type=form.connection_type.data,
                reference_id=ref_id,
                zone=form.zone.data,
                registration_date=form.registration_date.data or date.today(),
                flat_no=form.flat_no.data,
                locality=form.locality.data,
                area=form.area.data,
                building=form.building.data,
                billing_address=None,
                primary_address=form.primary_address.data,
                reg_form_file=reg_form_filename,
                photo_file=photo_filename,
                address_proof_type=form.address_proof_type.data,
                address_proof_file=address_proof_filename,
                id_proof_type=form.id_proof_type.data,
                id_proof_file=id_proof_filename,
                is_active=True,
                notes=None,
                discount_percent=0,
                discount_amount=0
            )
            db.session.add(customer)
            db.session.commit()

            plan_id = request.form.get('plan_id', type=int)
            plan_start_date_str = request.form.get('plan_start_date')
            if plan_id and plan_start_date_str:
                plan = Plan.query.get(plan_id)
                if plan:
                    try:
                        start_date = datetime.strptime(plan_start_date_str, '%Y-%m-%d').date()
                    except (ValueError, TypeError):
                        flash('Invalid plan start date. Use YYYY-MM-DD.', 'danger')
                        return render_template('customers/add.html', form=form, plans=plans, date=date, ref_id=ref_id)
                    end_date = start_date + timedelta(days=plan.validity_days or 30)
                    customer_plan = CustomerPlan(
                        customer_id=customer.id,
                        plan_id=plan.id,
                        start_date=start_date,
                        end_date=end_date,
                        status='active',
                        auto_renew=True,
                        grace_period_days=1
                    )
                    db.session.add(customer_plan)
                    db.session.commit()
                    log_audit('Assign Plan', f"Assigned plan {plan.name} to {customer.full_name}")

            log_audit('Add Customer', f"Added customer {customer.full_name}")
            flash('Customer added successfully.', 'success')
            return redirect(url_for('customer_view', id=customer.id))

        except Exception as e:
            db.session.rollback()
            app.logger.exception("Database error while saving customer")
            flash('An unexpected error occurred while saving the customer. Please try again.', 'danger')
            return render_template('customers/add.html', form=form, plans=plans, date=date, ref_id=ref_id)
    else:
        if form.errors:
            app.logger.warning("Customer form validation failed: %s", form.errors)
            flash(f'Please correct the following errors: {form.errors}', 'danger')

    return render_template('customers/add.html', form=form, plans=plans, date=date, ref_id=ref_id)

# ===== CUSTOMER EDIT =====
@app.route('/customers/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def customer_edit(id):
    customer = Customer.query.get_or_404(id)
    form = CustomerForm(obj=customer)

    form.zone.choices = [(z.name, z.name) for z in Zone.query.all()]
    form.locality.choices = [(l.name, l.name) for l in Locality.query.all()]
    form.area.choices = [(a.name, a.name) for a in Area.query.all()]
    form.building.choices = [(b.name, b.name) for b in Building.query.all()]

    if form.validate_on_submit():
        if form.password.data:
            customer.set_password(form.password.data)
        if form.reg_form.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.reg_form.data, public_id=f'cust{id}-regform-{secrets.token_hex(4)}')
                if url:
                    customer.reg_form_file = url
            else:
                file = form.reg_form.data
                filename = secure_filename(file.filename)
                customer.reg_form_file = filename
                file.save(os.path.join(app.root_path, 'static', 'uploads', filename))
        if form.photo.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.photo.data, public_id=f'cust{id}-photo-{secrets.token_hex(4)}')
                if url:
                    customer.photo_file = url
            else:
                file = form.photo.data
                filename = secure_filename(file.filename)
                customer.photo_file = filename
                file.save(os.path.join(app.root_path, 'static', 'uploads', filename))
        if form.address_proof.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.address_proof.data, public_id=f'cust{id}-address-{secrets.token_hex(4)}')
                if url:
                    customer.address_proof_file = url
            else:
                file = form.address_proof.data
                filename = secure_filename(file.filename)
                customer.address_proof_file = filename
                file.save(os.path.join(app.root_path, 'static', 'uploads', filename))
        if form.id_proof.data:
            from services.cloudinary import is_enabled, upload
            if is_enabled():
                url = upload(form.id_proof.data, public_id=f'cust{id}-idproof-{secrets.token_hex(4)}')
                if url:
                    customer.id_proof_file = url
            else:
                file = form.id_proof.data
                filename = secure_filename(file.filename)
                customer.id_proof_file = filename
                file.save(os.path.join(app.root_path, 'static', 'uploads', filename))
        form.populate_obj(customer)
        if form.same_as_billing.data:
            customer.primary_address = customer.billing_address
        try:
            db.session.commit()
            log_audit('Edit Customer', f"Edited customer {customer.full_name}")
            flash('Customer updated.', 'success')
            return redirect(url_for('customer_view', id=id))
        except Exception as e:
            db.session.rollback()
            app.logger.exception("Database error while editing customer")
            flash('An unexpected error occurred while saving the customer. Please try again.', 'danger')
    return render_template('customers/edit.html', form=form, customer=customer)

# ===== CUSTOMER LEDGER =====
@app.route('/customers/<int:id>/ledger')
@login_required
def payment_ledger(id):
    customer = Customer.query.get_or_404(id)
    invoices = Invoice.query.filter_by(customer_id=id).order_by(Invoice.issue_date).all()
    payments = Payment.query.filter_by(customer_id=id).order_by(Payment.payment_date).all()

    combined_ledger = []
    for inv in invoices:
        combined_ledger.append({
            'date': inv.issue_date,
            'type': 'invoice',
            'object': inv
        })
    for pay in payments:
        combined_ledger.append({
            'date': pay.payment_date,
            'type': 'payment',
            'object': pay
        })

    combined_ledger.sort(key=lambda x: x['date'], reverse=True)

    return render_template('customers/ledger.html',
                           customer=customer,
                           invoices=invoices,
                           payments=payments,
                           ledger_entries=combined_ledger)

# ===== CUSTOMER VIEW =====
@app.route('/customers/<int:id>')
@login_required
def customer_view(id):
    customer = Customer.query.get_or_404(id)
    active_plan = CustomerPlan.query.filter_by(customer_id=id, status='active').first()
    plans = Plan.query.filter_by(is_active=True).all()
    plans_history = CustomerPlan.query.filter_by(customer_id=id).order_by(CustomerPlan.start_date.desc()).all()
    invoices = Invoice.query.filter_by(customer_id=id).order_by(Invoice.issue_date.desc()).all()

    all_unpaid = Invoice.query.filter(
        Invoice.customer_id == id,
        Invoice.status.in_(['sent', 'overdue'])
    ).all()
    pending_invoices = [inv for inv in all_unpaid if inv.balance > 0]

    payments = Payment.query.filter_by(customer_id=id).order_by(
        Payment.payment_date.desc(), Payment.id.desc()).all()

    total_billed = sum(float(i.total_amount or 0) for i in invoices)
    total_paid = sum(float(p.amount or 0) for p in payments if p.status == 'approved')
    total_outstanding = sum(i.balance for i in invoices
                            if i.status in ('draft', 'sent', 'overdue'))
    company = Company.query.first()
    vendors = (Vendor.query.filter_by(is_active=True)
               .order_by(Vendor.name).all())
    service_providers = ServiceProvider.query.filter_by(is_active=True).all()

    # === FIX: pass service provider name properly ===
    service_provider_name = None
    if active_plan and active_plan.plan and active_plan.plan.service_provider:
        service_provider_name = active_plan.plan.service_provider.name

    try:
        from models import AddonCategory
        addon_categories = AddonCategory.query.filter_by(is_active=True).order_by(AddonCategory.name).all()
    except Exception:
        addon_categories = []

    return render_template('customers/view.html',
                           addon_categories=addon_categories,
                           vendors=vendors,
                           service_providers=service_providers,
                           customer=customer,
                           active_plan=active_plan,
                           service_provider_name=service_provider_name,
                           plans=plans,
                           plans_history=plans_history,
                           invoices=invoices,
                           pending_invoices=pending_invoices,
                           payments=payments,
                           total_billed=total_billed,
                           total_paid=total_paid,
                           total_outstanding=total_outstanding,
                           company=company,
                           payment_modes=PAYMENT_MODE_CHOICES,
                           today=date.today())

@app.route('/customers/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def customer_delete(id):
    customer = Customer.query.get_or_404(id)
    customer.is_active = False
    db.session.commit()
    log_audit('Delete Customer', f"Soft-deleted customer {customer.full_name}")
    flash('Customer deactivated.', 'success')
    return redirect(url_for('customer_list'))

# ---- Customer status actions ----
@app.route('/customers/<int:id>/enable', methods=['POST'])
@login_required
@admin_required
def customer_enable(id):
    customer = Customer.query.get_or_404(id)
    customer.is_active = True
    db.session.commit()
    enable_connection_on_network(customer)
    log_audit('Enable Customer', f"Enabled customer {customer.full_name}")
    flash('Customer enabled.', 'success')
    return redirect(url_for('customer_view', id=id))

@app.route('/customers/<int:id>/disable', methods=['POST'])
@login_required
@admin_required
def customer_disable(id):
    customer = Customer.query.get_or_404(id)
    customer.is_active = False
    db.session.commit()
    disable_connection_on_network(customer)
    log_audit('Disable Customer', f"Disabled customer {customer.full_name}")
    flash('Customer disabled.', 'success')
    return redirect(url_for('customer_view', id=id))

# ---- Reset Customer Password (and send to log2space) ----
@app.route('/customers/<int:id>/reset-password', methods=['POST'])
@login_required
@admin_required
def customer_reset_password(id):
    customer = Customer.query.get_or_404(id)
    temp_password = secrets.token_urlsafe(8)
    customer.set_password(temp_password)
    db.session.commit()
    reset_customer_password_on_log2space(customer, temp_password)
    send_sms(customer.mobile, f"Your portal password has been reset. Temporary password: {temp_password}")
    send_email(customer.email, "Password Reset", f"Your new temporary password is: {temp_password}")
    log_audit('Reset Customer Password', f"Reset portal password for {customer.full_name}")
    flash('Password reset and sent to customer and log2space.', 'success')
    return redirect(url_for('customer_view', id=id))

# ---- Reset MAC (NO DB storage – only API) ----
@app.route('/customers/<int:id>/reset-mac', methods=['POST'])
@login_required
@admin_required
def reset_customer_mac(id):
    customer = Customer.query.get_or_404(id)
    mac = request.form.get('mac_address', '').strip()
    if not mac:
        flash('MAC address is required.', 'danger')
        return redirect(url_for('customer_view', id=id))
    if len(mac) != 17 or mac.count(':') != 5:
        flash('Invalid MAC address format. Use XX:XX:XX:XX:XX:XX', 'danger')
        return redirect(url_for('customer_view', id=id))
    if reset_mac_on_log2space(mac, customer.reference_id):
        log_audit('Reset MAC', f"Reset MAC to {mac} for customer {customer.full_name}")
        flash('MAC address reset successfully on network.', 'success')
    else:
        flash('Failed to reset MAC on network. Please check logs.', 'warning')
    return redirect(url_for('customer_view', id=id))

# ---- Other customer option routes ----
@app.route('/customers/<int:id>/add_note', methods=['POST'])
@login_required
@admin_required
def add_customer_note(id):
    customer = Customer.query.get_or_404(id)
    note = request.form.get('note', '').strip()
    if note:
        customer.notes = note
        db.session.commit()
        log_audit('Add Note', f"Added note to customer {customer.full_name}")
        flash('Note updated successfully.', 'success')
    else:
        flash('Note cannot be empty.', 'danger')
    return redirect(url_for('customer_view', id=id))

@app.route('/customers/<int:id>/add_discount', methods=['POST'])
@login_required
@admin_required
def add_discount(id):
    customer = Customer.query.get_or_404(id)
    discount_type = request.form.get('discount_type')
    value = request.form.get('value', type=float)
    if not value or value < 0:
        flash('Invalid discount value.', 'danger')
        return redirect(url_for('customer_view', id=id))
    if discount_type == 'percent':
        if value > 100:
            flash('Percentage discount cannot exceed 100%.', 'danger')
            return redirect(url_for('customer_view', id=id))
        customer.discount_percent = value
        customer.discount_amount = 0
    elif discount_type == 'amount':
        customer.discount_amount = value
        customer.discount_percent = 0
    else:
        flash('Invalid discount type.', 'danger')
        return redirect(url_for('customer_view', id=id))
    db.session.commit()
    log_audit('Add Discount', f"Added {discount_type} discount of {value} for {customer.full_name}")
    flash('Discount applied.', 'success')
    return redirect(url_for('customer_view', id=id))

@app.route('/customers/<int:id>/remove_discount', methods=['POST'])
@login_required
@admin_required
def remove_discount(id):
    customer = Customer.query.get_or_404(id)
    customer.discount_percent = 0
    customer.discount_amount = 0
    db.session.commit()
    log_audit('Remove Discount', f"Removed discount for {customer.full_name}")
    flash('Discount removed.', 'success')
    return redirect(url_for('customer_view', id=id))

@app.route('/customers/<int:id>/send_sms', methods=['POST'])
@login_required
@admin_required
def send_customer_sms(id):
    customer = Customer.query.get_or_404(id)
    message = request.form.get('sms_message', '').strip()
    if not message:
        flash('Message cannot be empty.', 'danger')
        return redirect(url_for('customer_view', id=id))
    send_sms(customer.mobile, message)
    log_audit('Send SMS', f"Sent SMS to {customer.full_name}: {message[:50]}...")
    flash('SMS sent.', 'success')
    return redirect(url_for('customer_view', id=id))

def _next_vendor_bill_no():
    prefix = f"VB-{date.today():%Y%m}-"
    seq = 1
    last = (VendorBill.query
            .filter(VendorBill.bill_no.like(f"{prefix}%"))
            .order_by(VendorBill.id.desc()).first())
    if last and last.bill_no:
        tail = last.bill_no.rsplit('-', 1)[-1]
        if tail.isdigit():
            seq = int(tail) + 1
    for _ in range(500):
        candidate = f"{prefix}{seq:04d}"
        if not VendorBill.query.filter_by(bill_no=candidate).first():
            return candidate
        seq += 1
    return f"{prefix}{secrets.token_hex(3).upper()}"

def _build_vendor_bills(invoice, customer, lines, issue_date):
    by_vendor = defaultdict(list)
    for ln in lines:
        vid = ln.get('vendor_id')
        if vid:
            by_vendor[vid].append(ln)

    created = []
    for vendor_id, vlines in by_vendor.items():
        vendor = db.session.get(Vendor, vendor_id)
        if vendor is None:
            continue
        bill = VendorBill(
            bill_no=_next_vendor_bill_no(),
            vendor_id=vendor.id,
            invoice_id=invoice.id,
            customer_id=customer.id,
            bill_date=issue_date,
            due_date=issue_date + timedelta(days=30),
            status='pending',
            reference=invoice.invoice_no,
            notes=f"Auto-generated from customer invoice {invoice.invoice_no}",
        )
        db.session.add(bill)
        db.session.flush()
        for ln in vlines:
            product = ln['product']
            unit_cost = float(product.cost_price or 0) or float(ln['price'])
            db.session.add(VendorBillItem(
                bill_id=bill.id,
                product_id=product.id,
                description=product.name,
                serial_number=ln['serial'],
                quantity=ln['qty'],
                unit_cost=unit_cost,
                tax_percent=float(product.tax_percent or 0),
            ))
        db.session.flush()
        bill.recalculate()
        created.append(bill)
    return created

@app.route('/api/vendors/<int:vendor_id>/products')
@login_required
def api_vendor_products(vendor_id):
    vendor = db.session.get(Vendor, vendor_id)
    if vendor is None:
        return jsonify(error='Vendor not found'), 404
    products = (Product.query
                .filter_by(vendor_id=vendor_id, is_active=True)
                .order_by(Product.name).all())
    return jsonify(products=[{
        'id': p.id,
        'name': p.name,
        'sku': p.sku or '',
        'unit_price': float(p.unit_price or 0),
        'tax_percent': float(p.tax_percent or 0),
        'on_hand': p.on_hand,
    } for p in products])

# ---------------------------------------------------------------------------
#  ADDON INVOICE helpers: preset headings + Include / Exclude / No Tax switch
# ---------------------------------------------------------------------------

#: The headings that appear in the "Heading" dropdown of the Addon Invoice
#: pop-up. They are only created once; edit them later under
#: Masters > Addon Categories without this list overwriting your changes.
ADDON_DEFAULT_CATEGORIES = [
    ('16 Port Switch',        0),
    ('Adaptor',               0),
    ('Cheque Bounce Charges', 0),
    ('Installation Charges',  0),
    ('OLT Charges',           0),
    ('ONU Charges',           0),
    ('Opening Balance',       0),
    ('Previous Dues',         0),
    ('Reconnection Charges',  0),
    ('Router charges',        0),
    ('Shifting Charges',      0),
    ('Static IP',             0),
    ('Wrong Billing',         0),
]


def seed_addon_categories():
    """Create the standard addon headings if they are not there yet."""
    from models import AddonCategory
    added = 0
    for name, price in ADDON_DEFAULT_CATEGORIES:
        if not AddonCategory.query.filter_by(name=name).first():
            db.session.add(AddonCategory(name=name, default_price=price,
                                         description=''))
            added += 1
    if added:
        db.session.commit()
    return added


def _gst_percent():
    """GST rate used by the Addon Invoice tax switch.

    Stored in the settings table under `gst_percent` so it can be changed
    without touching the code. Falls back to 18%.
    """
    try:
        return Decimal(str(Setting.get('gst_percent', 18) or 18))
    except Exception:
        return Decimal('18')


def _apply_tax(amount, mode):
    """Turn the amount typed on the form into (grand_total, tax_amount).

    include  -> the amount already has GST inside it; the total does not move,
                the tax is only broken out for the invoice.
    exclude  -> GST is added on top of the amount typed in.
    notax    -> nothing is added and no tax is shown.
    """
    try:
        amount = Decimal(str(amount or 0))
    except (InvalidOperation, TypeError):
        amount = Decimal('0')

    rate = _gst_percent()
    mode = (mode or 'notax').strip().lower()
    cents = Decimal('0.01')

    if amount <= 0 or rate <= 0 or mode == 'notax':
        return amount.quantize(cents), Decimal('0.00')

    if mode == 'exclude':
        tax = amount * rate / Decimal('100')
        total = amount + tax
    else:  # 'include'
        base = amount / (Decimal('1') + rate / Decimal('100'))
        tax = amount - base
        total = amount

    return total.quantize(cents), tax.quantize(cents)


@app.route('/customers/<int:id>/add_invoice', methods=['POST'])
@login_required
def add_customer_invoice(id):
    """Raise an addon invoice.

    Fed by two places on the Pending Invoice tab:
      * the inline quick-entry strip  -> receipt_no, discount_amount,
        invoice_amount, start_date, payment_mode, book_receipt_no, remark
      * the ADDON INVOICE pop-up      -> caption (Heading), start_date,
        end_date, invoice_amount, tax_applicable
    Both post to this one endpoint, so either can be used.
    """
    customer = Customer.query.get_or_404(id)
    back = redirect(url_for('customer_view', id=id) + '#pending-invoice')

    caption = (request.form.get('caption') or '').strip()
    detailed = (request.form.get('detailed_invoice') or '').strip()
    payment_mode = (request.form.get('payment_mode') or '').strip()
    book_receipt_no = (request.form.get('book_receipt_no') or '').strip()
    receipt_no = (request.form.get('receipt_no') or '').strip()
    remark = (request.form.get('remark') or request.form.get('remarks') or '').strip()
    vendor_id = request.form.get('vendor_id', type=int)
    tax_mode = (request.form.get('tax_applicable') or 'notax').strip().lower()

    # How many days an invoice stays open when no End Date is given
    try:
        due_days = int(Setting.get('invoice_due_days', 15) or 15)
    except Exception:
        due_days = 15

    # Start Date / End Date come from the pop-up; the strip only sends a date
    start_date_raw = request.form.get('start_date')
    end_date_raw = request.form.get('end_date')
    try:
        issue_date = (datetime.strptime(start_date_raw, '%Y-%m-%d').date()
                      if start_date_raw else date.today())
        due_date = (datetime.strptime(end_date_raw, '%Y-%m-%d').date()
                    if end_date_raw else issue_date + timedelta(days=due_days))
    except ValueError:
        issue_date = date.today()
        due_date = issue_date + timedelta(days=due_days)

    if due_date < issue_date:
        flash('The end date cannot be before the start date.', 'danger')
        return back

    try:
        discount = Decimal(str(request.form.get('discount_amount', type=float) or 0))
        flat_amount = Decimal(str(request.form.get('invoice_amount', type=float) or 0))
    except (InvalidOperation, TypeError):
        flash('Amount and discount must be valid numbers.', 'danger')
        return back

    # ---- optional vendor product rows -------------------------------------
    prod_ids = request.form.getlist('item_product_id[]')
    serials = request.form.getlist('item_serial[]')
    qtys = request.form.getlist('item_qty[]')
    prices = request.form.getlist('item_price[]')

    lines, items_total = [], Decimal('0')
    for idx, pid in enumerate(prod_ids):
        if not pid or not pid.strip():
            continue
        product = db.session.get(Product, int(pid))
        if product is None:
            flash(f'Row {idx + 1}: that product no longer exists.', 'danger')
            return back

        try:
            qty = int(qtys[idx]) if idx < len(qtys) and qtys[idx] else 1
            price = (Decimal(str(prices[idx])) if idx < len(prices) and prices[idx]
                     else Decimal(str(product.unit_price or 0)))
        except (ValueError, IndexError, InvalidOperation):
            flash(f'Row {idx + 1}: quantity and price must be numbers.', 'danger')
            return back

        if qty <= 0:
            flash(f'Row {idx + 1}: quantity must be at least 1.', 'danger')
            return back
        if price < 0:
            flash(f'Row {idx + 1}: price cannot be negative.', 'danger')
            return back

        stock = Stock.query.filter_by(product_id=product.id).first()
        on_hand = stock.quantity if stock else 0
        if on_hand < qty:
            flash(f'Only {on_hand} x {product.name} in stock - cannot bill {qty}. '
                  f'Add stock under Inventory first.', 'danger')
            return back

        lines.append(dict(
            product=product, stock=stock, qty=qty, price=price,
            vendor_id=product.vendor_id or vendor_id,
            serial=(serials[idx].strip() if idx < len(serials) else '') or None,
        ))
        items_total += price * qty

    # ---- Include / Exclude / No Tax ---------------------------------------
    net_total = items_total + flat_amount
    total, tax_amount = _apply_tax(net_total, tax_mode)

    if total < 0:
        flash('Invoice amount cannot be negative.', 'danger')
        return back
    if discount < 0:
        flash('Discount cannot be negative.', 'danger')
        return back
    if discount > total:
        flash(f'Discount (Rs.{discount}) is greater than the invoice amount '
              f'(Rs.{total}).', 'danger')
        return back
    if total == 0 and discount == 0:
        flash('Enter an amount, or add at least one vendor product row.', 'warning')
        return back

    active_plan = (CustomerPlan.query
                   .filter_by(customer_id=id, status='active')
                   .order_by(CustomerPlan.end_date.desc()).first())

    payment = None
    vendor_bills = []
    try:
        invoice = Invoice(
            customer_id=customer.id,
            customer_plan_id=active_plan.id if active_plan else None,
            invoice_no=generate_invoice_no(),
            issue_date=issue_date,
            due_date=due_date,
            total_amount=total,
            tax_amount=tax_amount,
            discount_amount=discount,
            receipt_number=(book_receipt_no or receipt_no or None),
            remarks=remark or None,
            caption=caption or (detailed or ('Equipment' if lines else None)),
            invoice_type='discount' if (discount and not total) else 'addon',
            status='draft' if total == 0 else 'sent',
            vendor=(db.session.get(Vendor, vendor_id).name
                    if vendor_id and db.session.get(Vendor, vendor_id) else None),
        )
        db.session.add(invoice)
        db.session.flush()

        if flat_amount > 0:
            db.session.add(InvoiceItem(
                invoice_id=invoice.id,
                description=caption or detailed or 'Additional charge',
                item_type='service',
                quantity=1,
                unit_price=flat_amount,
                tax_percent=float(_gst_percent()) if tax_mode in ('include', 'exclude') else 0,
            ))

        for ln in lines:
            db.session.add(InvoiceItem(
                invoice_id=invoice.id,
                description=ln['product'].name,
                item_type='device',
                product_id=ln['product'].id,
                vendor_id=ln['vendor_id'],
                serial_number=ln['serial'],
                quantity=ln['qty'],
                unit_price=ln['price'],
                tax_percent=ln['product'].tax_percent or 0,
            ))
            if ln['stock']:
                ln['stock'].quantity -= ln['qty']
            else:
                db.session.add(Stock(product_id=ln['product'].id, quantity=0))
            db.session.add(InventoryAssignment(
                customer_id=customer.id,
                product_id=ln['product'].id,
                serial_number=ln['serial'],
                assigned_date=issue_date,
                status='Active'))

        if lines:
            vendor_bills = _build_vendor_bills(invoice, customer, lines, issue_date)

        # A mode was picked on the quick-entry strip -> money collected now.
        if payment_mode and total > 0:
            needs_auth = not current_user.is_admin()
            payment = Payment(
                invoice_id=invoice.id,
                customer_id=customer.id,
                amount=total - discount,
                payment_date=issue_date,
                payment_mode=payment_mode,
                mode_detail=remark or None,
                book_receipt_no=book_receipt_no or receipt_no or None,
                remarks=remark or None,
                received_by_user_id=current_user.id,
                source='admin',
                # The money is credited straight away; a non-admin entry still
                # shows up in the authorization queue for review.
                status='approved',
                authorized_at=None if needs_auth else datetime.utcnow(),
                authorized_by_user_id=None if needs_auth else current_user.id,
            )
            db.session.add(payment)
            db.session.flush()
            if not needs_auth:
                invoice.status = 'paid'

        db.session.commit()

    except Exception:
        db.session.rollback()
        app.logger.exception("Addon invoice failed for customer %s", id)
        flash('The invoice could not be saved. Nothing was charged and no '
              'stock was moved.', 'danger')
        return back

    log_audit('Addon Invoice',
              f"{invoice.invoice_no} for {customer.full_name}: {len(lines)} item(s), "
              f"Rs.{total - discount} (tax {tax_mode}: Rs.{tax_amount})"
              + (f", {len(vendor_bills)} vendor bill(s)" if vendor_bills else ""))

    msg = f'Invoice {invoice.invoice_no} created for Rs.{(total - discount):,.2f}.'
    if tax_amount > 0:
        msg += f' (GST {_gst_percent()}%: Rs.{tax_amount:,.2f} {tax_mode}d.)'
    if vendor_bills:
        msg += (' Vendor bill' + ('s ' if len(vendor_bills) > 1 else ' ')
                + ', '.join(b.bill_no for b in vendor_bills) + ' raised.')
    if payment is not None and payment.authorized_at is None:
        msg += ' The payment is waiting for admin authorization.'
    flash(msg, 'success')
    return back

@app.route('/customers/<int:id>/terminate', methods=['POST'])
@login_required
@admin_required
def terminate_customer(id):
    customer = Customer.query.get_or_404(id)
    customer.is_active = False
    active_plan = CustomerPlan.query.filter_by(customer_id=id, status='active').first()
    if active_plan:
        active_plan.status = 'terminated'
    db.session.commit()
    disable_connection_on_network(customer)
    log_audit('Terminate Customer', f"Terminated customer {customer.full_name}")
    flash('Customer terminated successfully.', 'success')
    return redirect(url_for('customer_view', id=id))

# ---- Dashboard drill-down ----
@app.route('/customers/plan-status/<status>')
@login_required
def customer_plan_status(status):
    today = date.today()
    if status in ('expiring', 'active'):
        plans = CustomerPlan.query.filter(CustomerPlan.end_date >= today, CustomerPlan.status == 'active').all()
    elif status == 'expired':
        plans = CustomerPlan.query.filter(CustomerPlan.end_date < today, CustomerPlan.status == 'active').all()
    elif status == 'renewed':
        plans = CustomerPlan.query.filter(CustomerPlan.start_date == today).all()
    elif status == 'suspended':
        plans = CustomerPlan.query.filter(CustomerPlan.status.in_(('cancelled', 'terminated'))).all()
    elif status == 'all':
        plans = CustomerPlan.query.order_by(CustomerPlan.end_date.desc()).all()
    else:
        abort(404)
    return render_template('customers/plan_status_list.html', plans=plans,
                           status=status, today=today)

# ===== PLAN ASSIGNMENT & RENEWAL =====
@app.route('/customers/<int:customer_id>/assign-plan', methods=['POST'])
@login_required
@admin_required
def assign_plan(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    plan_id = request.form.get('plan_id', type=int)
    start_date_str = request.form.get('start_date')
    if not plan_id or not start_date_str:
        flash('Please select a plan and start date.', 'danger')
        return redirect(url_for('customer_view', id=customer_id))
    plan = Plan.query.get_or_404(plan_id)
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        flash('Invalid date format. Use YYYY-MM-DD.', 'danger')
        return redirect(url_for('customer_view', id=customer_id))
    end_date = start_date + timedelta(days=plan.validity_days)
    # Same rule as the REST endpoint: every open row closes, so this screen
    # cannot leave a customer on two plans either.
    from services.plans import close_active_plans
    close_active_plans(customer_id)
    new_plan = CustomerPlan(
        customer_id=customer_id,
        plan_id=plan.id,
        start_date=start_date,
        end_date=end_date,
        status='active',
        auto_renew=True,
        grace_period_days=1
    )
    db.session.add(new_plan)
    db.session.commit()
    log_audit('Assign Plan', f"Assigned plan {plan.name} to {customer.full_name}")
    flash(f'Plan "{plan.name}" assigned successfully.', 'success')
    return redirect(url_for('customer_view', id=customer_id))

@app.route('/customers/<int:customer_id>/renew-plan', methods=['POST'])
@login_required
@admin_required
def renew_plan(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    active_plan = CustomerPlan.query.filter_by(customer_id=customer_id, status='active').first()
    if not active_plan:
        flash('No active plan to renew.', 'warning')
        return redirect(url_for('customer_view', id=customer_id))
    plan = active_plan.plan
    custom_end = request.form.get('new_end_date')
    custom_start = request.form.get('new_start_date')
    base = max(active_plan.end_date, date.today()) if active_plan.auto_renew else active_plan.end_date
    new_end_date = base + timedelta(days=plan.validity_days)
    try:
        if custom_start:
            active_plan.start_date = datetime.strptime(custom_start, '%Y-%m-%d').date()
        if custom_end:
            new_end_date = datetime.strptime(custom_end, '%Y-%m-%d').date()
    except ValueError:
        flash('Invalid date supplied — used the default renewal period instead.', 'warning')
    active_plan.end_date = new_end_date
    active_plan.status = 'active'
    active_plan.last_invoice_date = date.today()
    invoice = Invoice(
        customer_id=customer_id,
        invoice_no=generate_invoice_no(),
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=15),
        total_amount=active_plan.effective_price,
        tax_amount=0.00,
        customer_plan_id=active_plan.id,
        caption=plan.name,
        invoice_type='plan',
        status='sent'
    )
    db.session.add(invoice)
    db.session.commit()
    log_audit('Renew Plan', f"Renewed plan {plan.name} for {customer.full_name} until {new_end_date}")
    flash(f'Plan renewed successfully. New expiry: {new_end_date.strftime("%d-%b-%Y")}', 'success')

    # Send Renewal Notification
    send_template_message(customer, 'renewal', {
        'customer_name': customer.full_name,
        'username': customer.username,
        'amount': active_plan.effective_price
    })

    send_sms(customer.mobile, f"Dear {customer.full_name}, your plan has been renewed until {new_end_date.strftime('%d-%b-%Y')}.")
    return redirect(url_for('customer_view', id=customer_id))

# ===== PAYMENT RECORDING WITH EXTEND PLAN =====
@app.route('/payments/record/<int:invoice_id>', methods=['POST'])
@login_required
def record_payment(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    customer = invoice.customer
    amount = request.form.get('amount', type=float)
    payment_date_str = request.form.get('payment_date')
    payment_mode = request.form.get('payment_mode')
    mode_detail = request.form.get('mode_detail', '')
    bank_name = request.form.get('bank_name', '')
    transaction_number = request.form.get('transaction_number', '')
    remarks = request.form.get('remarks', '')
    renew_date_str = request.form.get('renew_plan_end_date')

    if not amount or amount <= 0:
        flash('Invalid amount.', 'danger')
        return redirect(url_for('customer_view', id=customer.id))

    try:
        payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date() if payment_date_str else date.today()
    except (ValueError, TypeError):
        flash('Invalid payment date format. Use YYYY-MM-DD.', 'danger')
        return redirect(url_for('customer_view', id=customer.id))

    final_mode_detail = mode_detail
    if bank_name:
        final_mode_detail += f" Bank: {bank_name}"
    if transaction_number:
        final_mode_detail += f" Transaction: {transaction_number}"

    # The payment is credited to the customer straight away. When it is taken
    # by a non-admin it still shows up in the authorization queue for review,
    # but the customer's balance never waits on that sign-off.
    needs_auth = not current_user.is_admin()
    payment = Payment(
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=amount,
        payment_date=payment_date,
        payment_mode=payment_mode or 'Cash',
        mode_detail=final_mode_detail,
        book_receipt_no=request.form.get('book_receipt_no') or None,
        remarks=remarks or None,
        received_by_user_id=current_user.id,
        source='admin',
        status='pending' if needs_auth else 'approved',
        authorized_at=None if needs_auth else datetime.utcnow(),
        authorized_by_user_id=None if needs_auth else current_user.id,
    )
    db.session.add(payment)
    db.session.flush()
    if not invoice.caption:
        invoice.caption = payment.payment_mode
    if invoice.balance <= 0:
        invoice.status = 'paid'
    db.session.commit()
    log_audit('Record Payment',
              f"Recorded {payment.payment_mode} payment Rs.{amount} for invoice {invoice.invoice_no}")

    if renew_date_str:
        try:
            new_end = datetime.strptime(renew_date_str, '%Y-%m-%d').date()
            active_plan = CustomerPlan.query.filter_by(customer_id=customer.id, status='active').first()
            if active_plan:
                active_plan.end_date = new_end
                db.session.commit()
                log_audit('Plan Extension via Payment', f"Extended plan for {customer.full_name} to {new_end}")
        except ValueError:
            flash('Invalid renew date format.', 'warning')

    send_template_message(customer, 'payment_received',
                          invoice=invoice, payment=payment)

    flash('Payment recorded and credited to the account'
          + (' \u2014 it is queued for authorization.' if needs_auth else '.'),
          'success')
    return redirect(url_for('customer_view', id=customer.id) + '#payment-history')

# ---- Delete a recorded payment (admin only) ----
@app.route('/payments/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def payment_delete(id):
    payment = Payment.query.get_or_404(id)
    customer_id = payment.customer_id
    invoice = payment.invoice
    amount = payment.amount
    db.session.delete(payment)
    db.session.commit()
    if invoice and invoice.balance > 0 and invoice.status == 'paid':
        invoice.status = 'sent'
        db.session.commit()
    log_audit('Delete Payment', f"Deleted payment #{id} (₹{amount}) for invoice "
                                 f"{invoice.invoice_no if invoice else 'N/A'}")
    flash('Payment deleted and invoice balance recalculated.', 'success')
    return redirect(url_for('customer_view', id=customer_id))

# ---------- Invoice & Payment ----------
@app.route('/invoices/generate/<int:customer_id>')
@login_required
@admin_required
def generate_invoice(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    active_plan = CustomerPlan.query.filter_by(customer_id=customer_id, status='active').first()
    if not active_plan:
        flash('No active plan for this customer.', 'warning')
        return redirect(url_for('customer_view', id=customer_id))
    invoice = Invoice(
        customer_id=customer_id,
        invoice_no=generate_invoice_no(),
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=15),
        total_amount=active_plan.effective_price,
        tax_amount=0.00,
        status='sent'
    )
    db.session.add(invoice)
    db.session.commit()
    log_audit('Generate Invoice', f"Generated invoice {invoice.invoice_no} for {customer.full_name}")
    send_email(customer.email, f"New Invoice {invoice.invoice_no}", "Your invoice is attached.")
    flash('Invoice generated and emailed to the customer.', 'success')
    return redirect(url_for('customer_view', id=customer_id))

def _invoice_context(invoice):
    customer = invoice.customer
    company = Company.query.first()
    payments = [p for p in invoice.payments if p.status == 'approved']
    return dict(invoice=invoice, customer=customer, company=company,
                payments=payments, today=date.today())

def _serve_invoice(template, invoice, download, filename):
    html = render_template(template, download=download, **_invoice_context(invoice))
    if not download:
        return html
    return Response(html, mimetype='text/html',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})

@app.route('/invoices/<int:id>/print')
@login_required
def invoice_print(id):
    return redirect(url_for('invoice_summary', id=id))

@app.route('/invoices/<int:id>/summary')
@login_required
def invoice_summary(id):
    invoice = Invoice.query.get_or_404(id)
    download = request.args.get('download') == '1'
    return _serve_invoice('invoices/summary.html', invoice, download,
                          f'summary-{invoice.invoice_no}.html')

@app.route('/invoices/<int:id>/detailed')
@login_required
def invoice_detailed(id):
    invoice = Invoice.query.get_or_404(id)
    download = request.args.get('download') == '1'
    return _serve_invoice('invoices/detailed.html', invoice, download,
                          f'detailed-{invoice.invoice_no}.html')

@app.route('/invoices/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def invoice_delete(id):
    invoice = Invoice.query.get_or_404(id)
    customer_id = invoice.customer_id
    if any(p.status == 'approved' for p in invoice.payments):
        flash('Cannot delete an invoice that already has authorized payments.', 'danger')
        return redirect(url_for('customer_view', id=customer_id))
    for p in list(invoice.payments):
        db.session.delete(p)
    db.session.delete(invoice)
    db.session.commit()
    log_audit('Delete Invoice', f"Deleted invoice #{id}")
    flash('Invoice deleted.', 'success')
    return redirect(url_for('customer_view', id=customer_id) + '#pending-invoice')

@app.route('/customers/<int:id>/invoices/export')
@login_required
def customer_invoices_export(id):
    customer = Customer.query.get_or_404(id)
    invoices = Invoice.query.filter_by(customer_id=id).order_by(Invoice.issue_date.desc()).all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['Invoice History', customer.full_name, date.today().isoformat()])
    w.writerow([])
    w.writerow(['#', 'Invoice No', 'Caption', 'Invoice Date', 'Due Date', 'Status',
                'Invoice Amount', 'Discount', 'Paid', 'Pending', 'Payment Mode'])
    for i, inv in enumerate(invoices, start=1):
        w.writerow([i, inv.invoice_no, inv.display_caption,
                    inv.issue_date.isoformat(), inv.due_date.isoformat(), inv.status,
                    float(inv.total_amount or 0), float(inv.discount_amount or 0),
                    inv.paid_amount, inv.balance, ' / '.join(inv.paid_modes) or '-'])
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':
                             f'attachment; filename=invoice-history-{customer.id}.csv'})

@app.route('/payments/add/<int:invoice_id>', methods=['GET', 'POST'])
@login_required
def add_payment(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    form = PaymentForm()
    if form.validate_on_submit():
        if form.amount.data <= 0:
            flash('Payment amount must be greater than zero.', 'danger')
            return render_template('payments/add.html', form=form, invoice=invoice)
        payment = Payment(
            invoice_id=invoice.id,
            customer_id=invoice.customer_id,
            amount=form.amount.data,
            payment_date=form.payment_date.data or date.today(),
            payment_mode=form.payment_mode.data,
            mode_detail=form.mode_detail.data,
            status=form.status.data,
            book_receipt_no=form.book_receipt_no.data,
            remarks=form.remarks.data,
            received_by_user_id=current_user.id
        )
        db.session.add(payment)
        db.session.flush()
        invoice.status = 'paid' if invoice.total_amount <= invoice.paid_amount else 'sent'
        db.session.commit()
        log_audit('Add Payment', f"Added payment {payment.amount} to invoice {invoice.invoice_no}")
        flash('Payment recorded.', 'success')
        return redirect(url_for('customer_view', id=invoice.customer_id))
    return render_template('payments/add.html', form=form, invoice=invoice)

@app.route('/payments/<int:id>/approve', methods=['POST'])
@login_required
@admin_required
def payment_approve(id):
    payment = db.session.query(Payment).with_for_update().get_or_404(id)
    # Shared with the portal-activity screen so approving from either place
    # settles the invoice AND applies any renewal the payment was buying.
    ok, renewal_applied = payment_service.approve_payment(payment, current_user)
    if not ok:
        flash('That payment is already authorized.', 'info')
        return redirect(request.referrer or url_for('payment_authorizations'))
    log_audit('Authorize Payment',
              f"Authorized {payment.source_label.lower()} #{payment.id} "
              f"of Rs.{payment.amount}")
    if renewal_applied:
        flash('Payment authorized and the plan was renewed.', 'success')
    else:
        flash('Payment authorized.', 'success')
    return redirect(request.referrer or url_for('customer_view', id=payment.customer_id))

# ---------- Service Provider Master CRUD ----------
@app.route('/masters/service-providers')
@login_required
@admin_required
def service_provider_list():
    providers = ServiceProvider.query.all()
    return render_template('masters/service_provider_list.html', providers=providers)

@app.route('/masters/service-providers/add', methods=['GET', 'POST'])
@login_required
@admin_required
def service_provider_add():
    form = ServiceProviderForm()
    if form.validate_on_submit():
        provider = ServiceProvider(
            name=form.name.data,
            is_active=form.is_active.data,
            api_url=form.api_url.data,
            api_username=form.api_username.data,
            api_password=form.api_password.data
        )
        db.session.add(provider)
        db.session.commit()
        log_audit('Add Service Provider', f"Added provider {provider.name}")
        flash('Service Provider added successfully.', 'success')
        return redirect(url_for('service_provider_list'))
    return render_template('masters/service_provider_form.html', form=form)

@app.route('/masters/service-providers/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def service_provider_edit(id):
    provider = ServiceProvider.query.get_or_404(id)
    form = ServiceProviderForm(obj=provider)
    if form.validate_on_submit():
        form.populate_obj(provider)
        db.session.commit()
        log_audit('Edit Service Provider', f"Edited provider {provider.name}")
        flash('Service Provider updated successfully.', 'success')
        return redirect(url_for('service_provider_list'))
    return render_template('masters/service_provider_form.html', form=form, provider=provider)

@app.route('/masters/service-providers/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def service_provider_delete(id):
    provider = ServiceProvider.query.get_or_404(id)
    db.session.delete(provider)
    db.session.commit()
    log_audit('Delete Service Provider', f"Deleted provider {provider.name}")
    flash('Service Provider deleted.', 'success')
    return redirect(url_for('service_provider_list'))

# ---------- Inventory ----------
@app.route('/inventory')
@login_required
def inventory_index():
    return render_template('inventory/index.html')

@app.route('/inventory/vendors')
@login_required
def vendor_list():
    vendors = Vendor.query.all()
    return render_template('inventory/vendors.html', vendors=vendors)

@app.route('/inventory/vendors/add', methods=['GET', 'POST'])
@login_required
@admin_required
def vendor_add():
    form = VendorForm()
    if form.validate_on_submit():
        vendor = Vendor()
        form.populate_obj(vendor)
        db.session.add(vendor)
        db.session.commit()
        log_audit('Add Vendor', f"Added vendor {vendor.name}")
        flash('Vendor added.', 'success')
        return redirect(url_for('vendor_list'))
    return render_template('inventory/vendor_form.html', form=form)

@app.route('/inventory/vendors/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def vendor_edit(id):
    vendor = Vendor.query.get_or_404(id)
    form = VendorForm(obj=vendor)
    if form.validate_on_submit():
        form.populate_obj(vendor)
        db.session.commit()
        log_audit('Edit Vendor', f"Edited vendor {vendor.name}")
        flash('Vendor updated.', 'success')
        return redirect(url_for('vendor_list'))
    return render_template('inventory/vendor_form.html', form=form, vendor=vendor)

@app.route('/inventory/vendors/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def vendor_delete(id):
    vendor = Vendor.query.get_or_404(id)
    if vendor.products or vendor.bills:
        vendor.is_active = False
        db.session.commit()
        log_audit('Deactivate Vendor', f"Deactivated vendor {vendor.name}")
        flash('Vendor has products or bills, so it was deactivated rather than '
              'deleted. Its history is preserved.', 'warning')
        return redirect(url_for('vendor_list'))
    db.session.delete(vendor)
    db.session.commit()
    log_audit('Delete Vendor', f"Deleted vendor {vendor.name}")
    flash('Vendor deleted.', 'success')
    return redirect(url_for('vendor_list'))

@app.route('/inventory/products')
@login_required
def product_list():
    products = Product.query.all()
    return render_template('inventory/products.html', products=products)

@app.route('/inventory/products/add', methods=['GET', 'POST'])
@login_required
@admin_required
def product_add():
    form = ProductForm()
    form.vendor_id.choices = _vendor_choices()
    if form.validate_on_submit():
        product = Product(
            name=form.name.data,
            vendor_id=form.vendor_id.data or None,
            sku=form.sku.data or None,
            hsn_code=form.hsn_code.data or None,
            description=form.description.data,
            cost_price=form.cost_price.data or 0,
            unit_price=form.unit_price.data or 0,
            tax_percent=form.tax_percent.data or 0,
            is_active=form.is_active.data,
        )
        db.session.add(product)
        db.session.flush()
        if not Stock.query.filter_by(product_id=product.id).first():
            db.session.add(Stock(product_id=product.id, quantity=0))
        db.session.commit()
        log_audit('Add Product', f"Added product {product.name}")
        flash('Product added. Set its opening stock under Inventory > Stock.', 'success')
        return redirect(url_for('product_list'))
    return render_template('inventory/product_form.html', form=form)

@app.route('/inventory/products/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def product_edit(id):
    product = Product.query.get_or_404(id)
    form = ProductForm(obj=product)
    form.vendor_id.choices = _vendor_choices()
    if request.method == 'GET':
        form.vendor_id.data = product.vendor_id or 0
    if form.validate_on_submit():
        form.populate_obj(product)
        product.vendor_id = form.vendor_id.data or None
        db.session.commit()
        log_audit('Edit Product', f"Edited product {product.name}")
        flash('Product updated.', 'success')
        return redirect(url_for('product_list'))
    return render_template('inventory/product_form.html', form=form, product=product)

@app.route('/inventory/products/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def product_delete(id):
    product = Product.query.get_or_404(id)
    product.is_active = False
    db.session.commit()
    log_audit('Delete Product', f"Deactivated product {product.name}")
    flash('Product deactivated. Existing invoices keep their history.', 'success')
    return redirect(url_for('product_list'))

@app.route('/inventory/stock')
@login_required
def stock_list():
    stock = Stock.query.all()
    return render_template('inventory/stock.html', stock=stock)

@app.route('/inventory/stock/update', methods=['GET', 'POST'])
@login_required
@admin_required
def stock_update():
    form = StockForm()
    form.product_id.choices = [(p.id, p.name) for p in Product.query.all()]
    if form.validate_on_submit():
        if form.quantity.data < 0:
            flash('Stock quantity cannot be negative.', 'danger')
            return render_template('inventory/stock_form.html', form=form)
        stock = Stock.query.filter_by(product_id=form.product_id.data).first()
        if stock:
            stock.quantity = form.quantity.data
        else:
            stock = Stock(product_id=form.product_id.data, quantity=form.quantity.data)
            db.session.add(stock)
        db.session.commit()
        log_audit('Update Stock', f"Updated stock for product {stock.product_id}")
        flash('Stock updated.', 'success')
        return redirect(url_for('stock_list'))
    return render_template('inventory/stock_form.html', form=form)

# ---------- Expenses ----------
@app.route('/expenses/categories')
@login_required
def expense_category_list():
    categories = ExpenseCategory.query.all()
    return render_template('expenses/categories.html', categories=categories)

@app.route('/expenses/categories/add', methods=['GET', 'POST'])
@login_required
@admin_required
def expense_category_add():
    form = ExpenseCategoryForm()
    if form.validate_on_submit():
        category = ExpenseCategory(name=form.name.data)
        db.session.add(category)
        db.session.commit()
        log_audit('Add Expense Category', f"Added category {category.name}")
        flash('Category added.', 'success')
        return redirect(url_for('expense_category_list'))
    return render_template('expenses/category_form.html', form=form)

@app.route('/expenses/categories/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def expense_category_edit(id):
    category = ExpenseCategory.query.get_or_404(id)
    form = ExpenseCategoryForm(obj=category)
    if form.validate_on_submit():
        form.populate_obj(category)
        db.session.commit()
        log_audit('Edit Expense Category', f"Edited category {category.name}")
        flash('Category updated.', 'success')
        return redirect(url_for('expense_category_list'))
    return render_template('expenses/category_form.html', form=form, category=category)

@app.route('/expenses/categories/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def expense_category_delete(id):
    category = ExpenseCategory.query.get_or_404(id)
    if Expense.query.filter_by(category_id=id).first():
        flash('Cannot delete a category that has expenses recorded against it.', 'danger')
        return redirect(url_for('expense_category_list'))
    db.session.delete(category)
    db.session.commit()
    log_audit('Delete Expense Category', f"Deleted category {category.name}")
    flash('Category deleted.', 'success')
    return redirect(url_for('expense_category_list'))

@app.route('/expenses/accounts')
@login_required
def expense_account_list():
    accounts = ExpenseAccount.query.all()
    return render_template('expenses/accounts.html', accounts=accounts)

@app.route('/expenses/accounts/add', methods=['GET', 'POST'])
@login_required
@admin_required
def expense_account_add():
    form = ExpenseAccountForm()
    if form.validate_on_submit():
        account = ExpenseAccount(name=form.name.data)
        db.session.add(account)
        db.session.commit()
        log_audit('Add Expense Account', f"Added account {account.name}")
        flash('Account added.', 'success')
        return redirect(url_for('expense_account_list'))
    return render_template('expenses/account_form.html', form=form)

@app.route('/expenses/accounts/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def expense_account_edit(id):
    account = ExpenseAccount.query.get_or_404(id)
    form = ExpenseAccountForm(obj=account)
    if form.validate_on_submit():
        form.populate_obj(account)
        db.session.commit()
        log_audit('Edit Expense Account', f"Edited account {account.name}")
        flash('Account updated.', 'success')
        return redirect(url_for('expense_account_list'))
    return render_template('expenses/account_form.html', form=form, account=account)

@app.route('/expenses/accounts/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def expense_account_delete(id):
    account = ExpenseAccount.query.get_or_404(id)
    if Expense.query.filter_by(account_id=id).first():
        flash('Cannot delete an account that has expenses recorded against it.', 'danger')
        return redirect(url_for('expense_account_list'))
    db.session.delete(account)
    db.session.commit()
    log_audit('Delete Expense Account', f"Deleted account {account.name}")
    flash('Account deleted.', 'success')
    return redirect(url_for('expense_account_list'))

@app.route('/expenses/payees')
@login_required
def expense_payee_list():
    payees = ExpensePayee.query.all()
    return render_template('expenses/payees.html', payees=payees)

@app.route('/expenses/payees/add', methods=['GET', 'POST'])
@login_required
@admin_required
def expense_payee_add():
    form = ExpensePayeeForm()
    if form.validate_on_submit():
        payee = ExpensePayee(
            name=form.name.data,
            mobile=form.mobile.data,
            email=form.email.data,
            address=form.address.data
        )
        db.session.add(payee)
        db.session.commit()
        log_audit('Add Expense Payee', f"Added payee {payee.name}")
        flash('Payee added.', 'success')
        return redirect(url_for('expense_payee_list'))
    return render_template('expenses/payee_form.html', form=form)

@app.route('/expenses/payees/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def expense_payee_edit(id):
    payee = ExpensePayee.query.get_or_404(id)
    form = ExpensePayeeForm(obj=payee)
    if form.validate_on_submit():
        form.populate_obj(payee)
        db.session.commit()
        log_audit('Edit Expense Payee', f"Edited payee {payee.name}")
        flash('Payee updated.', 'success')
        return redirect(url_for('expense_payee_list'))
    return render_template('expenses/payee_form.html', form=form, payee=payee)

@app.route('/expenses/payees/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def expense_payee_delete(id):
    payee = ExpensePayee.query.get_or_404(id)
    if Expense.query.filter_by(payee_id=id).first():
        flash('Cannot delete a payee that has expenses recorded against it.', 'danger')
        return redirect(url_for('expense_payee_list'))
    db.session.delete(payee)
    db.session.commit()
    log_audit('Delete Expense Payee', f"Deleted payee {payee.name}")
    flash('Payee deleted.', 'success')
    return redirect(url_for('expense_payee_list'))

# ---- Expenses main list with filters and totals ----
@app.route('/expenses')
@login_required
def expenses_index():
    query = Expense.query
    category_id = request.args.get('category', type=int)
    account_id = request.args.get('account', type=int)
    payee_id = request.args.get('payee', type=int)
    prepared_by = request.args.get('prepared_by', type=int)
    status = request.args.get('status')

    if category_id:
        query = query.filter_by(category_id=category_id)
    if account_id:
        query = query.filter_by(account_id=account_id)
    if payee_id:
        query = query.filter_by(payee_id=payee_id)
    if prepared_by:
        query = query.filter_by(prepared_by_id=prepared_by)
    if status:
        query = query.filter_by(status=status)

    expenses = query.order_by(Expense.expense_date.desc(), Expense.id.desc()).all()
    total_amount = sum(float(e.amount or 0) for e in expenses)

    categories = ExpenseCategory.query.all()
    accounts = ExpenseAccount.query.all()
    payees = ExpensePayee.query.all()
    users = User.query.filter_by(is_active=True).all()
    statuses = ['draft', 'pending', 'approved', 'rejected']

    return render_template('expenses/index.html',
                           expenses=expenses,
                           total_amount=total_amount,
                           categories=categories,
                           accounts=accounts,
                           payees=payees,
                           users=users,
                           statuses=statuses,
                           selected={
                               'category': category_id,
                               'account': account_id,
                               'payee': payee_id,
                               'prepared_by': prepared_by,
                               'status': status
                           })

@app.route('/expenses/add', methods=['GET', 'POST'])
@login_required
@admin_required
def expense_add():
    form = ExpenseForm()
    form.category_id.choices = [(c.id, c.name) for c in ExpenseCategory.query.all()]
    form.account_id.choices = [(a.id, a.name) for a in ExpenseAccount.query.all()]
    form.payee_id.choices = [(p.id, p.name) for p in ExpensePayee.query.all()]
    form.prepared_by_id.choices = [(u.id, u.full_name) for u in User.query.filter_by(is_active=True).all()]
    form.passed_by_id.choices = [(u.id, u.full_name) for u in User.query.filter_by(is_active=True).all()]

    if form.validate_on_submit():
        if form.amount.data <= 0:
            flash('Expense amount must be greater than zero.', 'danger')
            return render_template('expenses/add.html', form=form)

        expense = Expense(
            category_id=form.category_id.data,
            account_id=form.account_id.data,
            payee_id=form.payee_id.data,
            amount=form.amount.data,
            expense_date=form.expense_date.data,
            description=form.description.data,
            prepared_by_id=form.prepared_by_id.data,
            passed_by_id=form.passed_by_id.data,
            status=form.status.data
        )
        db.session.add(expense)
        db.session.commit()
        log_audit('Add Expense', f"Added expense {expense.id}")
        flash('Expense added.', 'success')
        return redirect(url_for('expenses_index'))
    return render_template('expenses/add.html', form=form)

@app.route('/expenses/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def expense_edit(id):
    expense = Expense.query.get_or_404(id)
    form = ExpenseForm(obj=expense)
    form.category_id.choices = [(c.id, c.name) for c in ExpenseCategory.query.all()]
    form.account_id.choices = [(a.id, a.name) for a in ExpenseAccount.query.all()]
    form.payee_id.choices = [(p.id, p.name) for p in ExpensePayee.query.all()]
    form.prepared_by_id.choices = [(u.id, u.full_name) for u in User.query.filter_by(is_active=True).all()]
    form.passed_by_id.choices = [(u.id, u.full_name) for u in User.query.filter_by(is_active=True).all()]

    if form.validate_on_submit():
        form.populate_obj(expense)
        db.session.commit()
        log_audit('Edit Expense', f"Edited expense {expense.id}")
        flash('Expense updated.', 'success')
        return redirect(url_for('expenses_index'))
    return render_template('expenses/edit.html', form=form, expense=expense)

@app.route('/expenses/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def expense_delete(id):
    expense = Expense.query.get_or_404(id)
    db.session.delete(expense)
    db.session.commit()
    log_audit('Delete Expense', f"Deleted expense {expense.id}")
    flash('Expense deleted.', 'success')
    return redirect(url_for('expenses_index'))

# ---------- Staff ----------
@app.route('/staff')
@login_required
@admin_required
def staff_list():
    staff = User.query.all()
    return render_template('staff/list.html', staff=staff)

@app.route('/staff/add', methods=['GET', 'POST'])
@login_required
@admin_required
def staff_add():
    form = StaffForm()
    form.staff_type_id.choices = [(t.id, t.name) for t in StaffType.query.all()]
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash('That username is already taken.', 'danger')
            return render_template('staff/add.html', form=form)
        user = User(
            username=form.username.data,
            full_name=form.full_name.data,
            email=form.email.data,
            mobile=form.mobile.data,
            role=form.role.data,
            staff_type_id=form.staff_type_id.data,
            is_active=form.is_active.data,
            monthly_salary=form.monthly_salary.data or 0.00
        )
        if form.password.data:
            user.set_password(form.password.data)
        else:
            temp_password = secrets.token_urlsafe(10)
            user.set_password(temp_password)
            flash(f'No password was set — a temporary password was generated: {temp_password}. '
                  f'Share it securely and ask the user to change it on first login.', 'warning')
        db.session.add(user)
        db.session.commit()
        log_audit('Add Staff', f"Added staff {user.full_name}")
        flash('Staff added.', 'success')
        return redirect(url_for('staff_list'))
    return render_template('staff/add.html', form=form)

@app.route('/staff/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def staff_edit(id):
    user = User.query.get_or_404(id)
    form = StaffForm(obj=user)
    form.staff_type_id.choices = [(t.id, t.name) for t in StaffType.query.all()]
    if form.validate_on_submit():
        form.populate_obj(user)
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        log_audit('Edit Staff', f"Edited staff {user.full_name}")
        flash('Staff updated.', 'success')
        return redirect(url_for('staff_list'))
    return render_template('staff/edit.html', form=form, user=user)

@app.route('/staff/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def staff_delete(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('You cannot delete yourself.', 'danger')
        return redirect(url_for('staff_list'))
    user.is_active = False
    db.session.commit()
    log_audit('Delete Staff', f"Soft-deleted staff {user.full_name}")
    flash('Staff deactivated.', 'success')
    return redirect(url_for('staff_list'))

# ---- Staff Type ----
@app.route('/staff/types')
@login_required
@admin_required
def staff_type_list():
    types = StaffType.query.all()
    return render_template('staff/types.html', types=types)

@app.route('/staff/types/add', methods=['GET', 'POST'])
@login_required
@admin_required
def staff_type_add():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        if not name:
            flash('Staff type name is required.', 'danger')
            return render_template('staff/type_form.html')
        if StaffType.query.filter_by(name=name).first():
            flash('That staff type already exists.', 'danger')
            return render_template('staff/type_form.html')
        staff_type = StaffType(name=name)
        db.session.add(staff_type)
        db.session.commit()
        log_audit('Add Staff Type', f"Added staff type {name}")
        flash('Staff type added.', 'success')
        return redirect(url_for('staff_type_list'))
    return render_template('staff/type_form.html')

@app.route('/staff/types/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def staff_type_delete(id):
    staff_type = StaffType.query.get_or_404(id)
    if User.query.filter_by(staff_type_id=id).first():
        flash('Cannot delete a staff type that is assigned to staff members.', 'danger')
        return redirect(url_for('staff_type_list'))
    db.session.delete(staff_type)
    db.session.commit()
    log_audit('Delete Staff Type', f"Deleted staff type {staff_type.name}")
    flash('Staff type deleted.', 'success')
    return redirect(url_for('staff_type_list'))

# ---------- HR ----------
@app.route('/hr')
@login_required
def hr_index():
    return render_template('hr/index.html')

@app.route('/hr/attendance')
@login_required
def attendance_list():
    attendances = Attendance.query.all()
    return render_template('hr/attendance.html', attendances=attendances)

@app.route('/hr/attendance/add', methods=['GET', 'POST'])
@login_required
@admin_required
def attendance_add():
    form = AttendanceForm()
    form.user_id.choices = [(u.id, u.full_name) for u in User.query.filter_by(is_active=True).all()]
    if form.validate_on_submit():
        existing = Attendance.query.filter_by(user_id=form.user_id.data, date=form.date.data).first()
        if existing:
            flash('Attendance for this user and date is already recorded.', 'danger')
            return render_template('hr/attendance_form.html', form=form)
        attendance = Attendance(
            user_id=form.user_id.data,
            date=form.date.data,
            status=form.status.data
        )
        db.session.add(attendance)
        db.session.commit()
        log_audit('Add Attendance', f"Recorded attendance for user {attendance.user_id}")
        flash('Attendance recorded.', 'success')
        return redirect(url_for('attendance_list'))
    return render_template('hr/attendance_form.html', form=form)

@app.route('/hr/leaves')
@login_required
def leave_list():
    leaves = Leave.query.all()
    return render_template('hr/leaves.html', leaves=leaves)

@app.route('/hr/leaves/add', methods=['GET', 'POST'])
@login_required
@admin_required
def leave_add():
    form = LeaveForm()
    form.user_id.choices = [(u.id, u.full_name) for u in User.query.filter_by(is_active=True).all()]
    if form.validate_on_submit():
        if form.end_date.data < form.start_date.data:
            flash('Leave end date cannot be before the start date.', 'danger')
            return render_template('hr/leave_form.html', form=form)
        leave = Leave(
            user_id=form.user_id.data,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            reason=form.reason.data,
            status=form.status.data
        )
        db.session.add(leave)
        db.session.commit()
        log_audit('Add Leave', f"Added leave for user {leave.user_id}")
        flash('Leave added.', 'success')
        return redirect(url_for('leave_list'))
    return render_template('hr/leave_form.html', form=form)

@app.route('/hr/leaves/<int:id>/approve', methods=['POST'])
@login_required
@admin_required
def leave_approve(id):
    leave = Leave.query.get_or_404(id)
    leave.status = 'approved'
    db.session.commit()
    log_audit('Approve Leave', f"Approved leave #{leave.id}")
    flash('Leave approved.', 'success')
    return redirect(url_for('leave_list'))

@app.route('/hr/leaves/<int:id>/reject', methods=['POST'])
@login_required
@admin_required
def leave_reject(id):
    leave = Leave.query.get_or_404(id)
    leave.status = 'rejected'
    db.session.commit()
    log_audit('Reject Leave', f"Rejected leave #{leave.id}")
    flash('Leave rejected.', 'success')
    return redirect(url_for('leave_list'))

@app.route('/hr/payroll')
@login_required
@admin_required
def payroll_list():
    payrolls = Payroll.query.all()
    return render_template('hr/payroll.html', payrolls=payrolls)

@app.route('/hr/payroll/add', methods=['GET', 'POST'])
@login_required
@admin_required
def payroll_add():
    form = PayrollForm()
    form.user_id.choices = [(u.id, u.full_name) for u in User.query.filter_by(is_active=True).all()]
    if form.validate_on_submit():
        existing = Payroll.query.filter_by(user_id=form.user_id.data, month_year=form.month_year.data).first()
        if existing:
            flash('Payroll for this user and month already exists.', 'danger')
            return render_template('hr/payroll_form.html', form=form)
        payroll = Payroll(
            user_id=form.user_id.data,
            month_year=form.month_year.data,
            salary=form.salary.data,
            paid=form.paid.data
        )
        db.session.add(payroll)
        db.session.commit()
        log_audit('Add Payroll', f"Added payroll for user {payroll.user_id}")
        flash('Payroll added.', 'success')
        return redirect(url_for('payroll_list'))
    return render_template('hr/payroll_form.html', form=form)

@app.route('/hr/payroll/<int:id>/mark-paid', methods=['POST'])
@login_required
@admin_required
def payroll_mark_paid(id):
    payroll = Payroll.query.get_or_404(id)
    payroll.paid = True
    db.session.commit()
    log_audit('Mark Payroll Paid', f"Marked payroll #{payroll.id} as paid")
    flash('Payroll marked as paid.', 'success')
    return redirect(url_for('payroll_list'))

# ---------- HR Reports ----------
def _hr_month(default=None):
    """Parse ?month=YYYY-MM from query string; fall back to current month."""
    month_str = request.args.get('month')
    today = date.today()
    if month_str:
        try:
            y, m = map(int, month_str.split('-'))
            return date(y, m, 1)
        except ValueError:
            pass
    return (default or today).replace(day=1)

@app.route('/hr/attendance/report')
@login_required
def attendance_report():
    employee_id = request.args.get('employee_id', type=int)
    start_date = _hr_month()
    end_date = (start_date + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    query = Attendance.query.filter(Attendance.date >= start_date, Attendance.date <= end_date)
    if employee_id:
        query = query.filter_by(user_id=employee_id)
    attendances = query.all()

    user_stats = defaultdict(lambda: {'present': 0, 'absent': 0, 'half': 0})
    for a in attendances:
        stats = user_stats[a.user_id]
        if a.status == 'present':
            stats['present'] += 1
        elif a.status == 'absent':
            stats['absent'] += 1
        elif a.status == 'half-day':
            stats['half'] += 1

    users = User.query.filter_by(is_active=True).all()
    user_dict = {u.id: u for u in users}

    report_rows = []
    for uid, stats in user_stats.items():
        user = user_dict.get(uid)
        if user:
            report_rows.append({
                'user': user,
                'present': stats['present'],
                'absent': stats['absent'],
                'half': stats['half'],
                'total': stats['present'] + stats['absent'] + stats['half']
            })

    employees = User.query.filter_by(is_active=True).all()
    return render_template('hr/attendance_report.html',
                           report_rows=report_rows,
                           employees=employees,
                           selected_employee=employee_id,
                           month=start_date.strftime('%Y-%m'),
                           month_display=start_date.strftime('%B %Y'))

@app.route('/hr/leaves/report')
@login_required
def leaves_report():
    employee_id = request.args.get('employee_id', type=int)
    start_date = _hr_month()
    end_date = (start_date + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    query = Leave.query.filter(Leave.status == 'approved',
                               Leave.start_date >= start_date,
                               Leave.end_date <= end_date)
    if employee_id:
        query = query.filter_by(user_id=employee_id)
    leaves = query.all()

    user_leave_days = defaultdict(int)
    user_leave_dates = defaultdict(list)
    for lv in leaves:
        days = (lv.end_date - lv.start_date).days + 1
        user_leave_days[lv.user_id] += days
        user_leave_dates[lv.user_id].append(lv)

    users = User.query.filter_by(is_active=True).all()
    user_dict = {u.id: u for u in users}

    report_rows = []
    for uid, days in user_leave_days.items():
        user = user_dict.get(uid)
        if user:
            report_rows.append({
                'user': user,
                'leave_days': days,
                'leave_dates': user_leave_dates.get(uid, [])
            })

    employees = User.query.filter_by(is_active=True).all()
    return render_template('hr/leaves_report.html',
                           report_rows=report_rows,
                           employees=employees,
                           selected_employee=employee_id,
                           month=start_date.strftime('%Y-%m'),
                           month_display=start_date.strftime('%B %Y'))

@app.route('/hr/payroll/report')
@login_required
def payroll_report():
    employee_id = request.args.get('employee_id', type=int)
    month_date = _hr_month()

    query = Payroll.query.filter(Payroll.month_year == month_date)
    if employee_id:
        query = query.filter_by(user_id=employee_id)
    payrolls = query.all()

    users = User.query.filter_by(is_active=True).all()
    payroll_dict = {p.user_id: p for p in payrolls}

    report_rows = []
    for user in users:
        payroll = payroll_dict.get(user.id)
        if payroll:
            paid = payroll.paid
            amount = float(payroll.salary or 0)
            base_salary = float(user.monthly_salary or 0)
            remaining = base_salary - amount if not paid else 0
        else:
            paid = False
            amount = 0
            base_salary = float(user.monthly_salary or 0)
            remaining = base_salary

        report_rows.append({
            'user': user,
            'base_salary': base_salary,
            'paid_amount': amount,
            'is_paid': paid,
            'remaining': remaining,
            'payroll': payroll
        })

    employees = User.query.filter_by(is_active=True).all()
    return render_template('hr/payroll_report.html',
                           report_rows=report_rows,
                           employees=employees,
                           selected_employee=employee_id,
                           month=month_date.strftime('%Y-%m'),
                           month_display=month_date.strftime('%B %Y'))

# ---------- Plan CRUD ----------
@app.route('/plans')
@login_required
def plan_list():
    plans = Plan.query.all()
    return render_template('plans/list.html', plans=plans)

@app.route('/plans/add', methods=['GET', 'POST'])
@login_required
@admin_required
def plan_add():
    form = PlanForm()
    form.service_provider_id.choices = [(sp.id, sp.name) for sp in ServiceProvider.query.all()]

    if form.validate_on_submit():
        plan = Plan(
            service_provider_id=form.service_provider_id.data or None,
            plan_code=form.plan_code.data,
            isp_amount=form.isp_amount.data or 0.00,
            plan_type=form.plan_type.data,
            name=form.name.data,
            speed_mbps=form.speed_mbps.data,
            price_monthly=form.price_monthly.data,
            validity_days=form.validity_days.data,
            is_active=form.is_active.data
        )
        db.session.add(plan)
        db.session.commit()
        log_audit('Add Plan', f"Added plan {plan.name}")
        flash('Plan added successfully.', 'success')
        return redirect(url_for('plan_list'))
    return render_template('plans/add.html', form=form)

@app.route('/plans/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def plan_edit(id):
    plan = Plan.query.get_or_404(id)
    form = PlanForm(obj=plan)
    form.service_provider_id.choices = [(sp.id, sp.name) for sp in ServiceProvider.query.all()]

    if form.validate_on_submit():
        form.populate_obj(plan)
        db.session.commit()
        log_audit('Edit Plan', f"Edited plan {plan.name}")
        flash('Plan updated successfully.', 'success')
        return redirect(url_for('plan_list'))
    return render_template('plans/edit.html', form=form, plan=plan)

@app.route('/plans/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def plan_delete(id):
    plan = Plan.query.get_or_404(id)
    plan.is_active = False
    db.session.commit()
    log_audit('Delete Plan', f"Soft-deactivated plan {plan.name}")
    flash('Plan deactivated.', 'success')
    return redirect(url_for('plan_list'))

# =========================================================================== #
#  VENDOR BILLS  (Inventory -> Vendor Bills)
# =========================================================================== #
@app.route('/inventory/vendor-bills')
@login_required
def vendor_bill_list():
    status = request.args.get('status', '')
    vendor_id = request.args.get('vendor_id', type=int)
    q = VendorBill.query
    if status:
        q = q.filter_by(status=status)
    if vendor_id:
        q = q.filter_by(vendor_id=vendor_id)
    bills = q.order_by(VendorBill.bill_date.desc(), VendorBill.id.desc()).all()
    totals = {
        'count': len(bills),
        'billed': sum(float(b.total_amount or 0) for b in bills),
        'paid': sum(float(b.paid_amount or 0) for b in bills),
    }
    totals['outstanding'] = totals['billed'] - totals['paid']
    return render_template('inventory/vendor_bills.html', bills=bills,
                           vendors=Vendor.query.order_by(Vendor.name).all(),
                           totals=totals, status=status, vendor_id=vendor_id)

@app.route('/inventory/vendor-bills/<int:id>')
@login_required
def vendor_bill_view(id):
    bill = VendorBill.query.get_or_404(id)
    return render_template('inventory/vendor_bill_view.html', bill=bill,
                           company=Company.query.first(), today=date.today())

@app.route('/inventory/vendor-bills/add', methods=['GET', 'POST'])
@login_required
@admin_required
def vendor_bill_add():
    """Raise a purchase bill by hand and receive the stock in one step."""
    form = VendorBillForm()
    form.vendor_id.choices = _vendor_choices(include_blank=False)
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    if not form.vendor_id.choices:
        flash('Add a vendor first under Inventory > Vendors.', 'warning')
        return redirect(url_for('vendor_add'))

    if form.validate_on_submit():
        prod_ids = request.form.getlist('item_product_id[]')
        qtys = request.form.getlist('item_qty[]')
        costs = request.form.getlist('item_cost[]')
        serials = request.form.getlist('item_serial[]')

        rows = []
        for idx, pid in enumerate(prod_ids):
            if not pid or not pid.strip():
                continue
            product = db.session.get(Product, int(pid))
            if product is None:
                flash(f'Row {idx + 1}: product not found.', 'danger')
                return render_template('inventory/vendor_bill_form.html',
                                       form=form, products=products)
            try:
                qty = int(qtys[idx]) if idx < len(qtys) and qtys[idx] else 1
                cost = (Decimal(str(costs[idx])) if idx < len(costs) and costs[idx]
                        else Decimal(str(product.cost_price or 0)))
            except (ValueError, IndexError, InvalidOperation):
                flash(f'Row {idx + 1}: quantity and cost must be numbers.', 'danger')
                return render_template('inventory/vendor_bill_form.html',
                                       form=form, products=products)
            if qty <= 0 or cost < 0:
                flash(f'Row {idx + 1}: quantity must be positive and cost cannot '
                      f'be negative.', 'danger')
                return render_template('inventory/vendor_bill_form.html',
                                       form=form, products=products)
            rows.append((product, qty, cost,
                         (serials[idx].strip() if idx < len(serials) else '') or None))

        if not rows:
            flash('Add at least one product row to the bill.', 'warning')
            return render_template('inventory/vendor_bill_form.html',
                                   form=form, products=products)

        try:
            bill = VendorBill(
                bill_no=_next_vendor_bill_no(),
                vendor_id=form.vendor_id.data,
                bill_date=form.bill_date.data or date.today(),
                due_date=form.due_date.data,
                reference=form.reference.data or None,
                notes=form.notes.data or None,
                status='pending',
            )
            db.session.add(bill)
            db.session.flush()
            for product, qty, cost, serial in rows:
                db.session.add(VendorBillItem(
                    bill_id=bill.id, product_id=product.id,
                    description=product.name, serial_number=serial,
                    quantity=qty, unit_cost=cost,
                    tax_percent=float(product.tax_percent or 0)))
                stock = Stock.query.filter_by(product_id=product.id).first()
                if stock:
                    stock.quantity = (stock.quantity or 0) + qty
                else:
                    db.session.add(Stock(product_id=product.id, quantity=qty))
            db.session.flush()
            bill.recalculate()
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Vendor bill creation failed")
            flash('The bill could not be saved. No stock was changed.', 'danger')
            return render_template('inventory/vendor_bill_form.html',
                                   form=form, products=products)

        log_audit('Vendor Bill', f"Created {bill.bill_no} for {bill.vendor.name}")
        flash(f'Vendor bill {bill.bill_no} created and stock received.', 'success')
        return redirect(url_for('vendor_bill_view', id=bill.id))

    return render_template('inventory/vendor_bill_form.html', form=form,
                           products=products)

@app.route('/inventory/vendor-bills/<int:id>/pay', methods=['POST'])
@login_required
@admin_required
def vendor_bill_pay(id):
    bill = VendorBill.query.get_or_404(id)
    amount = request.form.get('amount', type=float) or 0
    if amount <= 0:
        flash('Enter a payment amount greater than zero.', 'danger')
        return redirect(url_for('vendor_bill_view', id=id))
    if amount > bill.balance + 0.01:
        flash(f'That is more than the outstanding balance of '
              f'Rs.{bill.balance:,.2f}.', 'danger')
        return redirect(url_for('vendor_bill_view', id=id))
    bill.paid_amount = float(bill.paid_amount or 0) + amount
    bill.status = 'paid' if bill.balance <= 0.01 else 'partial'
    db.session.commit()
    log_audit('Vendor Bill Payment', f"Rs.{amount} against {bill.bill_no}")
    flash(f'Recorded Rs.{amount:,.2f} against {bill.bill_no}.', 'success')
    return redirect(url_for('vendor_bill_view', id=id))

@app.route('/inventory/vendor-bills/<int:id>/cancel', methods=['POST'])
@login_required
@admin_required
def vendor_bill_cancel(id):
    bill = VendorBill.query.get_or_404(id)
    if float(bill.paid_amount or 0) > 0:
        flash('This bill has payments recorded against it and cannot be '
              'cancelled.', 'danger')
        return redirect(url_for('vendor_bill_view', id=id))
    bill.status = 'cancelled'
    db.session.commit()
    log_audit('Vendor Bill Cancelled', bill.bill_no)
    flash(f'Bill {bill.bill_no} cancelled.', 'success')
    return redirect(url_for('vendor_bill_list'))

@app.route('/inventory/vendor-bills/export')
@login_required
def vendor_bill_export():
    bills = VendorBill.query.order_by(VendorBill.bill_date.desc()).all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Bill No', 'Vendor', 'Bill Date', 'Due Date', 'Reference',
                'Linked Invoice', 'Total', 'Paid', 'Balance', 'Status'])
    for b in bills:
        w.writerow([b.bill_no, b.vendor.name if b.vendor else '',
                    b.bill_date.isoformat() if b.bill_date else '',
                    b.due_date.isoformat() if b.due_date else '',
                    b.reference or '',
                    b.invoice.invoice_no if b.invoice else '',
                    float(b.total_amount or 0), float(b.paid_amount or 0),
                    b.balance, b.status])
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':
                             'attachment; filename=vendor-bills.csv'})

#  OUTBOUND MESSAGING  (bills, due reminders, bulk campaigns)
def _bill_context(invoice):
    """Placeholder values for a bill/receipt message."""
    cp = invoice.customer_plan
    return dict(plan=cp.plan if cp else None, customer_plan=cp, invoice=invoice)

@app.route('/invoices/<int:id>/send', methods=['POST'])
@login_required
def invoice_send(id):
    """Send one invoice to the customer's registered WhatsApp number."""
    invoice = Invoice.query.get_or_404(id)
    customer = invoice.customer
    if not customer or not customer.mobile:
        flash('This customer has no mobile number on file.', 'warning')
        return redirect(request.referrer or url_for('customer_view', id=invoice.customer_id))

    template_type = request.form.get('template_type') or 'bill'
    result = send_template_message(customer, template_type, **_bill_context(invoice))

    if result.status == 'sent':
        flash(f'Bill {invoice.invoice_no} sent to {customer.mobile}.', 'success')
    elif result.status == 'dry-run':
        flash('WhatsApp gateway is not configured yet, so the message was '
              'logged instead of sent. Add your gateway under Settings.', 'warning')
    else:
        flash(f'Could not send the bill: {result.detail}', 'danger')
    return redirect(request.referrer or url_for('customer_view', id=invoice.customer_id))

@app.route('/customers/<int:id>/send-reminder', methods=['POST'])
@login_required
def customer_send_reminder(id):
    """The bell icon on the customer overview: send an outstanding-dues nudge."""
    customer = Customer.query.get_or_404(id)
    if not customer.mobile:
        flash('This customer has no mobile number on file.', 'warning')
        return redirect(url_for('customer_view', id=id))

    unpaid = [i for i in Invoice.query.filter(
        Invoice.customer_id == id,
        Invoice.status.in_(['draft', 'sent', 'overdue'])).all() if i.balance > 0]
    due_total = sum(i.balance for i in unpaid)

    if due_total <= 0:
        flash('Nothing is outstanding for this customer.', 'info')
        return redirect(url_for('customer_view', id=id))

    latest = max(unpaid, key=lambda i: (i.issue_date, i.id))
    active_plan = CustomerPlan.query.filter_by(customer_id=id, status='active').first()
    result = send_template_message(
        customer, 'due_reminder',
        {'due_amount': due_total, 'balance': due_total},
        customer_plan=active_plan, invoice=latest)

    if result.status == 'sent':
        flash(f'Due reminder for Rs.{due_total:.0f} sent to {customer.mobile}.', 'success')
    elif result.status == 'dry-run':
        flash('WhatsApp gateway is not configured yet - the reminder was logged '
              'instead of sent.', 'warning')
    else:
        flash(f'Could not send the reminder: {result.detail}', 'danger')
    return redirect(url_for('customer_view', id=id))

@app.route('/customers/<int:id>/send-template', methods=['POST'])
@login_required
def customer_send_template(id):
    """Send any chosen template to one customer (WhatsApp icon in Plan tab)."""
    customer = Customer.query.get_or_404(id)
    template_type = request.form.get('template_type')
    if not template_type:
        flash('Please choose a message template.', 'warning')
        return redirect(url_for('customer_view', id=id))

    active_plan = CustomerPlan.query.filter_by(customer_id=id, status='active').first()
    result = send_template_message(customer, template_type,
                                   customer_plan=active_plan)
    if result.status == 'sent':
        flash(f'Message sent to {customer.mobile}.', 'success')
    elif result.status == 'dry-run':
        flash('WhatsApp gateway is not configured yet - the message was logged '
              'instead of sent.', 'warning')
    else:
        flash(f'Could not send: {result.detail}', 'danger')
    return redirect(url_for('customer_view', id=id))

@app.route('/customers/<int:id>/messages')
@login_required
def customer_message_log(id):
    """WhatsApp / SMS delivery log for one customer."""
    customer = Customer.query.get_or_404(id)
    logs = (MessageLog.query.filter_by(customer_id=id)
            .order_by(MessageLog.created_at.desc()).limit(200).all())
    return render_template('customers/message_log.html',
                           customer=customer, logs=logs)

@app.route('/messages/bulk', methods=['GET', 'POST'])
@login_required
@admin_required
def bulk_messages():

    templates = MessageTemplate.query.filter_by(is_active=True).order_by(
        MessageTemplate.name).all()
    today = date.today()
    recipients, preview_only = [], True

    if request.method == 'POST':
        audience = request.form.get('audience') or 'expired'
        template_type = request.form.get('template_type') or 'due_reminder'
        preview_only = request.form.get('action') != 'send'

        try:
            start = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
            end = datetime.strptime(request.form['end_date'], '%Y-%m-%d').date()
        except (KeyError, ValueError):
            flash('Please give a valid start and end date (YYYY-MM-DD).', 'danger')
            return redirect(url_for('bulk_messages'))

        if end < start:
            flash('The end date cannot be before the start date.', 'danger')
            return redirect(url_for('bulk_messages'))

        recipients = _bulk_audience(audience, start, end)

        if not preview_only:
            sent = failed = 0
            for customer, cp in recipients:
                res = send_template_message(customer, template_type,
                                            customer_plan=cp)
                if res.status in messaging.DELIVERABLE_STATUSES:
                    sent += 1
                else:
                    failed += 1
            log_audit('Bulk Message',
                      f"{template_type} -> {sent} customers "
                      f"({audience} {start}..{end}), {failed} failed")
            msg = f'Message queued for {sent} customer(s).'
            if failed:
                msg += f' {failed} could not be sent.'
            flash(msg, 'success' if not failed else 'warning')
            return redirect(url_for('bulk_messages'))

    return render_template('masters/bulk_messages.html',
                           templates=templates,
                           recipients=recipients,
                           preview_only=preview_only,
                           gateway_ready=messaging.is_configured(),
                           today=today,
                           form_data=request.form)

def _bulk_audience(audience, start, end):
    """Return a list of (customer, customer_plan) tuples for a campaign."""
    q = (db.session.query(CustomerPlan, Customer)
         .join(Customer, Customer.id == CustomerPlan.customer_id)
         .filter(Customer.mobile.isnot(None), Customer.mobile != ''))

    if audience == 'expiring':
        q = q.filter(CustomerPlan.status == 'active',
                     CustomerPlan.end_date >= start,
                     CustomerPlan.end_date <= end)
    elif audience == 'expired':
        q = q.filter(CustomerPlan.end_date >= start,
                     CustomerPlan.end_date <= end,
                     CustomerPlan.end_date < date.today())
    else:  # 'dues' - anyone with an unpaid invoice raised in the window
        rows = []
        seen = set()
        invoices = Invoice.query.filter(
            Invoice.status.in_(['draft', 'sent', 'overdue']),
            Invoice.issue_date >= start,
            Invoice.issue_date <= end).all()
        for inv in invoices:
            if inv.balance <= 0 or inv.customer_id in seen:
                continue
            cust = inv.customer
            if not cust or not cust.mobile:
                continue
            seen.add(inv.customer_id)
            cp = CustomerPlan.query.filter_by(
                customer_id=cust.id, status='active').first()
            rows.append((cust, cp))
        return rows

    out, seen = [], set()
    for cp, cust in q.order_by(CustomerPlan.end_date).all():
        if cust.id in seen:
            continue
        seen.add(cust.id)
        out.append((cust, cp))
    return out

#  CUSTOMER SELF-SERVICE PORTAL
def _current_customer():
    return db.session.get(Customer, session.get('customer_id'))

@app.route('/customer/login', methods=['GET', 'POST'])
def customer_login():
    if session.get('customer_id'):
        return redirect(url_for('customer_dashboard'))
    form = CustomerLoginForm()
    if form.validate_on_submit():
        ip = request.remote_addr
        uname = (form.username.data or '').strip()

        customer = Customer.query.filter_by(username=uname).first()
        if customer and customer.password_hash and customer.check_password(form.password.data):
            if not customer.is_active:
                flash('Your connection is currently disabled. Please contact support.',
                      'warning')
                return render_template('customer/login.html', form=form)
            session['customer_id'] = customer.id
            session.permanent = True
            log_audit('Customer Login', f"Customer {customer.full_name} logged in")
            nxt = request.args.get('next')
            if nxt and urlsplit(nxt).netloc == '' and nxt.startswith('/'):
                return redirect(nxt)
            return redirect(url_for('customer_dashboard'))
        from security import record_web_login_failure
        record_web_login_failure(uname)
        flash('Invalid username or password.', 'danger')
    return render_template('customer/login.html', form=form)

@app.route('/customer/logout')
def customer_logout():
    session.pop('customer_id', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('customer_login'))

@app.route('/customer/dashboard')
@customer_required
def customer_dashboard():
    customer = _current_customer()
    active_plan = CustomerPlan.query.filter_by(
        customer_id=customer.id, status='active').first()
    invoices = (Invoice.query.filter_by(customer_id=customer.id)
                .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
                .limit(50).all())
    payments = (Payment.query.filter_by(customer_id=customer.id)
                .filter(Payment.status != 'rejected')
                .order_by(Payment.payment_date.desc(), Payment.id.desc())
                .limit(50).all())

    outstanding = sum(i.balance for i in invoices
                      if i.status in ('draft', 'sent', 'overdue') and i.balance > 0)
    days_left = None
    if active_plan and active_plan.end_date:
        days_left = (active_plan.end_date - date.today()).days

    return render_template('customer/dashboard.html',
                           customer=customer,
                           active_plan=active_plan,
                           invoices=invoices,
                           payments=payments,
                           outstanding=outstanding,
                           days_left=days_left,
                           gateway_ready=cashfree.is_configured(),
                           today=date.today())

@app.route('/customer/profile', methods=['GET', 'POST'])
@customer_required
def customer_profile():
    customer = _current_customer()
    form = CustomerChangePasswordForm()
    if form.validate_on_submit():
        if not customer.password_hash or not customer.check_password(form.current_password.data):
            flash('Your current password is not correct.', 'danger')
        else:
            customer.set_password(form.new_password.data)
            db.session.commit()
            log_audit('Customer Password Change',
                      f"{customer.full_name} changed their portal password")
            flash('Password updated successfully.', 'success')
            return redirect(url_for('customer_profile'))
    return render_template('customer/profile.html', customer=customer, form=form)

@app.route('/customer/invoice/<int:id>')
@customer_required
def customer_invoice_view(id):
    """Print-ready invoice. The browser's own Print -> Save as PDF is used,
    so there is no server-side PDF dependency to break on Render/Railway."""
    invoice = Invoice.query.get_or_404(id)
    if invoice.customer_id != session.get('customer_id'):
        abort(403)
    return render_template('invoices/summary.html',
                           invoice=invoice,
                           customer=invoice.customer,
                           company=Company.query.first(),
                           today=date.today(),
                           download=False)

# --------------------------------------------------------------------------- #
#  Online renewal via Cashfree
# --------------------------------------------------------------------------- #
def _portal_renewal_invoice(customer, active_plan):
    """Reuse an open renewal invoice if there is one, else raise a fresh one."""
    plan = active_plan.plan
    existing = [i for i in Invoice.query.filter(
        Invoice.customer_id == customer.id,
        Invoice.status.in_(['draft', 'sent', 'overdue'])).all() if i.balance > 0]
    if existing:
        return max(existing, key=lambda i: (i.issue_date, i.id)), False

    invoice = Invoice(
        customer_id=customer.id,
        customer_plan_id=active_plan.id,
        invoice_no=generate_invoice_no(),
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=15),
        total_amount=active_plan.effective_price,
        tax_amount=0.00,
        caption='Online Payment',
        invoice_type='plan',
        status='sent',
    )
    db.session.add(invoice)
    db.session.commit()
    return invoice, True

@app.route('/customer/renew', methods=['POST'])
@customer_required
def customer_renew_plan():
    """Create a Cashfree order for the outstanding invoice and show the checkout page."""
    customer = _current_customer()
    active_plan = CustomerPlan.query.filter_by(
        customer_id=customer.id, status='active').first()
    if not active_plan or not active_plan.plan:
        flash('You do not have a plan assigned. Please contact support.', 'warning')
        return redirect(url_for('customer_dashboard'))

    if not cashfree.is_configured():
        flash('Online payment is not available right now. Please contact the office.',
              'warning')
        return redirect(url_for('customer_dashboard'))

    invoice, _created = _portal_renewal_invoice(customer, active_plan)
    amount = round(float(invoice.balance) or float(active_plan.effective_price), 2)
    if amount <= 0:
        flash('There is nothing outstanding on your account.', 'info')
        return redirect(url_for('customer_dashboard'))

    order_id = cashfree.new_order_id()
    order = OnlinePaymentOrder(
        order_id=order_id,
        customer_id=customer.id,
        invoice_id=invoice.id,
        amount=amount,
        status='created',
        note=f"Renewal - {active_plan.plan.name}",
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
            + f"?order_id={order_id}",
            notify_url=url_for('cashfree_webhook', _external=True),
            note=order.note,
        )
    except cashfree.CashfreeError as exc:
        order.status = 'failed'
        order.note = str(exc)[:255]
        db.session.commit()
        app.logger.error("Cashfree order failed: %s", exc)
        flash('We could not start the payment. Please try again in a moment.',
              'danger')
        return redirect(url_for('customer_dashboard'))

    order.payment_session_id = data.get('payment_session_id')
    order.cf_order_id = str(data.get('cf_order_id') or '')
    db.session.commit()

    return render_template('customer/checkout.html',
                           customer=customer,
                           order=order,
                           invoice=invoice,
                           sdk_url=cashfree.sdk_url(),
                           cf_mode='production' if cashfree.environment() == 'production' else 'sandbox')

def _settle_online_order(order):
    """Confirm a Cashfree order and credit the money. Idempotent – safe to call from both the return URL and the webhook."""
    order = db.session.query(OnlinePaymentOrder).with_for_update().get(order.id)
    if order.status == 'paid':
        return order

    try:
        data = cashfree.fetch_order(order.order_id)
    except cashfree.CashfreeError as exc:
        app.logger.warning("Cashfree status check failed for %s: %s",
                           order.order_id, exc)
        return order

    if not cashfree.is_paid(data):
        status = str(data.get('order_status', '')).upper()
        if status in ('EXPIRED', 'TERMINATED'):
            order.status = 'expired'
            db.session.commit()
        return order

    # ---- confirmed paid ----------------------------------------------------
    detail = cashfree.successful_payment(order.order_id) or {}
    txn_id = str(detail.get('cf_payment_id')
                 or detail.get('bank_reference')
                 or order.cf_order_id
                 or order.order_id)
    method = detail.get('payment_group') or detail.get('payment_method') or 'Online'
    if isinstance(method, dict):
        method = next(iter(method), 'Online')

    customer = order.customer
    invoice = order.invoice

    payment = Payment(
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=order.amount,
        payment_date=date.today(),
        payment_mode='Online',
        mode_detail=f"Cashfree {method} | Txn {txn_id} | Order {order.order_id}",
        gateway_transaction_id=txn_id,
        source='portal',
        # Credited to the account immediately; the admin still sees it in the
        # authorization queue for review.
        status='approved',
        authorized_at=None,
        authorized_by_user_id=None,
        remarks='Paid by customer through the self-service portal.',
    )
    db.session.add(payment)
    db.session.flush()

    order.status = 'paid'
    order.transaction_id = txn_id
    order.payment_method = str(method)[:50]
    order.payment_id = payment.id

    invoice.caption = 'Online Payment'
    if invoice.balance <= 0:
        invoice.status = 'paid'

    # Extend the plan now that the money has landed
    active_plan = CustomerPlan.query.filter_by(
        customer_id=customer.id, status='active').first()
    new_end = None
    if active_plan and active_plan.plan:
        base = max(active_plan.end_date, date.today())
        new_end = base + timedelta(days=active_plan.plan.validity_days or 30)
        active_plan.end_date = new_end
        active_plan.status = 'active'
        active_plan.last_invoice_date = date.today()
        if not customer.is_active:
            customer.is_active = True
            enable_connection_on_network(customer)

    db.session.commit()

    log_audit('Online Payment',
              f"{customer.full_name} paid Rs.{order.amount} online "
              f"(txn {txn_id}); plan extended to {new_end}")

    send_template_message(customer, 'payment_received',
                          invoice=invoice, payment=payment,
                          customer_plan=active_plan)
    return order

@app.route('/customer/payment/return')
@customer_required
def customer_payment_return():
    """Cashfree sends the customer back here after checkout."""
    order_id = request.args.get('order_id', '')
    order = OnlinePaymentOrder.query.filter_by(order_id=order_id).first()
    if not order or order.customer_id != session.get('customer_id'):
        flash('We could not find that payment. Please check your payment history.',
              'warning')
        return redirect(url_for('customer_dashboard'))

    _settle_online_order(order)

    if order.status == 'paid':
        flash(f'Payment of Rs.{order.amount} received. Your plan has been renewed. '
              f'Transaction ID: {order.transaction_id}', 'success')
    else:
        flash('We have not received a confirmation for that payment yet. '
              'If money has left your account it will be credited automatically '
              'within a few minutes.', 'warning')
    return redirect(url_for('customer_dashboard'))

@app.route('/webhooks/cashfree', methods=['POST'])
@csrf.exempt
def cashfree_webhook():
    """
    Server-to-server payment confirmation from Cashfree.

    The HMAC signature is verified against the *raw* body before anything is
    trusted, so a forged POST cannot mark an order paid.
    """
    raw = request.get_data()
    signature = request.headers.get('x-webhook-signature', '')
    timestamp = request.headers.get('x-webhook-timestamp', '')

    if not cashfree.verify_webhook(raw, signature, timestamp):
        app.logger.warning('Rejected Cashfree webhook with a bad signature.')
        return jsonify(status='invalid signature'), 401

    try:
        ts = int(timestamp)
        if abs(datetime.utcnow().timestamp() - ts) > 300:
            app.logger.warning('Rejected stale Cashfree webhook (timestamp %s)', timestamp)
            return jsonify(status='stale webhook'), 410
    except (ValueError, TypeError):
        return jsonify(status='invalid timestamp'), 400

    payload = request.get_json(silent=True) or {}
    order_id = (payload.get('data', {}).get('order', {}) or {}).get('order_id')
    if not order_id:
        return jsonify(status='ignored'), 200

    order = OnlinePaymentOrder.query.filter_by(order_id=order_id).first()
    if order:
        _settle_online_order(order)
    return jsonify(status='ok'), 200

#  Settings seeding for the messaging + payment gateways
GATEWAY_SETTING_DEFAULTS = {
    'wa_enabled':          ('1' if app.config.get('WA_ENABLED') else '0'),
    'wa_provider':         app.config.get('WA_PROVIDER', 'generic'),
    'wa_api_url':          app.config.get('WA_API_URL', ''),
    'wa_api_token':        app.config.get('WA_API_TOKEN', ''),
    'wa_instance_id':      app.config.get('WA_INSTANCE_ID', ''),
    'wa_sender':           '',
    'wa_http_method':      'POST',
    'wa_payload_template': messaging.DEFAULTS['wa_payload_template'],
    'wa_country_code':     app.config.get('WA_COUNTRY_CODE', '91'),
    'wa_document_url':     '',
    'cashfree_app_id':     app.config.get('CASHFREE_APP_ID', ''),
    'cashfree_secret_key': app.config.get('CASHFREE_SECRET_KEY', ''),
    'cashfree_env':        app.config.get('CASHFREE_ENV', 'sandbox'),
    'app_link':            app.config.get('APP_LINK', ''),
    'web_link':            app.config.get('WEB_LINK', ''),
    'admin_email':         app.config.get('ADMIN_EMAIL', ''),
    'admin_mobile':        app.config.get('ADMIN_MOBILE', ''),
}

def _seed_gateway_settings():
    """Create any missing settings rows. Never overwrites an edited value."""
    from models_ext import Setting, ENCRYPTED_SETTINGS, encrypt_setting_value
    added = 0
    for key, value in GATEWAY_SETTING_DEFAULTS.items():
        if not Setting.query.filter_by(key=key).first():
            stored = value or ''
            if key in ENCRYPTED_SETTINGS and stored:
                stored = encrypt_setting_value(stored)
            db.session.add(Setting(key=key, value=stored))
            added += 1
    if added:
        db.session.commit()
    return added

# ---------- Startup ----------
def init_database(flask_app=None):
    """Create tables and ensure a usable admin account exists."""
    flask_app = flask_app or app
    with flask_app.app_context():
        db.create_all()

        # Add any column the models gained since this database was created,
        # so an upgrade does not need a separate manual migration step.
        # Only ever adds - never drops or rewrites. `python migrate.py` runs
        # the same logic with verbose output.
        try:
            from services.schema_sync import sync_schema
            changes = sync_schema(db)
            if changes['added_columns']:
                flask_app.logger.info(
                    "Schema sync added %s column(s): %s",
                    len(changes['added_columns']),
                    ', '.join(f"{t}.{c}" for t, c in changes['added_columns']))
            for table, col, msg in changes['failed']:
                flask_app.logger.warning("Schema sync could not add %s.%s: %s",
                                         table, col, msg)
        except Exception as exc:                        # noqa: BLE001
            flask_app.logger.warning("Schema sync skipped: %s", exc)

        # Indexes, on every boot.
        #
        # `db.create_all()` creates missing TABLES and never touches a table
        # that already exists, so the seventeen indexes on the hot columns -
        # customer_plans(status, end_date), invoices(customer_id / status /
        # issue_date), payments(invoice_id / customer_id / status /
        # payment_date), customers(mobile / zone / is_active) - only existed if
        # somebody had remembered to run `python upgrade_schema.py` by hand
        # against the live database. Nobody had. Without them every dashboard
        # load and every expiry report is a full table scan, which is most of
        # what "the whole app is slow" actually was.
        #
        # The step is idempotent (each index is checked first) and every
        # failure is caught and reported rather than being allowed to stop the
        # process from starting. Set AUTO_INDEX=0 to skip it - for instance
        # while running a migration by hand.
        if os.environ.get('AUTO_INDEX', '1') != '0':
            try:
                from upgrade_schema import add_missing_indexes
                add_missing_indexes(db)
            except Exception as exc:                    # noqa: BLE001
                flask_app.logger.warning("Index check skipped: %s", exc)

        try:
            from blueprints.settings_bp import seed_settings
            seed_settings()
        except Exception:
            flask_app.logger.warning("Could not seed settings table.")

        # WhatsApp / Cashfree settings rows, so they are editable in the UI
        try:
            _seed_gateway_settings()
        except Exception as exc:
            flask_app.logger.warning("Could not seed gateway settings: %s", exc)

        # Standard Addon Invoice headings for the pop-up dropdown
        try:
            seed_addon_categories()
        except Exception as exc:
            flask_app.logger.warning("Could not seed addon categories: %s", exc)

        # The seven standard customer message templates
        try:
            created = messaging.seed_default_templates()
            if created:
                flask_app.logger.info("Seeded %s message templates.", created)
        except Exception as exc:
            flask_app.logger.warning("Could not seed message templates: %s", exc)

        # Push / in-app notification templates used by the portal + mobile app
        try:
            created = seed_notification_templates()
            if created:
                flask_app.logger.info("Seeded %s notification templates.", created)
        except Exception as exc:
            flask_app.logger.warning("Could not seed notification templates: %s", exc)

        # One service provider, named after this company.
        #
        # Nothing ever created one, so the Service Provider dropdown on the
        # plan and customer forms opened with nothing in it - which meant
        # `service_provider_id` could never be set, and every screen that
        # prints a provider printed a dash. A single row makes the field
        # usable; resellers can add their upstream providers beside it under
        # Masters.
        try:
            from models import Company, ServiceProvider
            if not ServiceProvider.query.first():
                company = Company.query.first()
                name = (company.name if company and company.name
                        else 'YASH Internet Services')
                db.session.add(ServiceProvider(name=name, is_active=True))
                db.session.commit()
                flask_app.logger.info('Seeded service provider "%s".', name)
        except Exception as exc:                            # noqa: BLE001
            db.session.rollback()
            flask_app.logger.warning('Could not seed service provider: %s', exc)

        admin = User.query.filter_by(username='admin').first()
        if not admin:
            default_pw = os.environ.get('ADMIN_PASSWORD')
            if not default_pw:
                import secrets as _secrets
                default_pw = _secrets.token_urlsafe(12)
                flask_app.logger.warning(
                    'ADMIN_PASSWORD env var not set. Generated a random '
                    'password for the admin account: %s', default_pw)
            admin = User(
                username='admin',
                full_name='Administrator',
                role='admin',
                email='admin@yash.com',
                is_active=True,
            )
            admin.set_password(default_pw)
            db.session.add(admin)
            db.session.commit()
            flask_app.logger.warning(
                "Default admin account created (username: admin). "
                "Change the password immediately after first login."
            )
        elif not admin.is_active:
            admin.is_active = True
            db.session.commit()
            flask_app.logger.info("Admin account re-enabled.")

        # One-time: remove all staff except the four authorised accounts.
        try:
            KEEP_STAFF = {'admin', 'dinesh', 'nitesh', 'ram'}
            all_active = User.query.filter(User.is_active.is_(True)).all()
            to_remove = [u for u in all_active if u.username.lower() not in KEEP_STAFF]
            if to_remove:
                for u in to_remove:
                    db.session.delete(u)
                db.session.commit()
                flask_app.logger.info(
                    "Removed %d non-authorised staff accounts.", len(to_remove))
        except Exception as exc:                            # noqa: BLE001
            db.session.rollback()
            flask_app.logger.warning('Could not clean staff: %s', exc)


# --------------------------------------------------------------------------- #
#  Schema check on import
# --------------------------------------------------------------------------- #
def ensure_schema(flask_app=None):
    """Add any column the models have and the live database does not.

    Why this runs at IMPORT and not only inside ``init_database()``
    --------------------------------------------------------------
    ``init_database()`` is called from ``wsgi.py`` and from ``python app.py``.
    It is NOT called by ``flask run``, by an IDE run configuration, by a
    WSGI server pointed straight at ``app:app``, or by any of the helper
    scripts - and in every one of those cases the application starts happily
    with a model that has a column the database has never heard of. The first
    query then dies with

        (1054, "Unknown column 'users.permissions' in 'field list'")

    which is not a code error anybody can find by reading the code: the code is
    right, the database is behind. It happened on the deploy that added
    ``users.permissions``, and it would happen again on the next column.

    So the check moves to where it cannot be skipped. ``app.py`` is imported by
    every entry point there is, so importing it is now enough to guarantee the
    schema matches the models.

    It only ever ADDS columns and tables - never drops, never rewrites - and it
    can never stop the process starting: a database that is unreachable at
    import time is a problem for the first request to report, not a reason to
    refuse to boot. Set AUTO_MIGRATE=0 to skip it (for instance while running a
    migration by hand).
    """
    flask_app = flask_app or app
    if os.environ.get('AUTO_MIGRATE', '1') == '0':
        return None
    if getattr(flask_app, '_schema_checked', False):
        return None
    flask_app._schema_checked = True

    try:
        with flask_app.app_context():
            from services.schema_sync import sync_schema
            changes = sync_schema(db)
    except Exception as exc:                            # noqa: BLE001
        # Deliberately swallowed. The database being unreachable while the
        # process starts is common (a Railway instance waking up, a container
        # ordered before its database) and must not turn into a boot loop.
        flask_app.logger.warning('Schema check could not run: %s', exc)
        return None

    for table, column in changes['added_columns']:
        flask_app.logger.warning('Schema: added missing column %s.%s',
                                 table, column)
    for table, column, message in changes['failed']:
        # Loud, because this is the state that produces "Unknown column" on
        # every request afterwards, and a warning nobody reads is how it
        # reaches a customer.
        flask_app.logger.error(
            'Schema: could NOT add %s.%s - %s. Run "python upgrade_schema.py" '
            'against this database.', table, column, message)
    return changes


ensure_schema(app)


if __name__ == '__main__':
    init_database(app)
    debug_mode = os.environ.get('FLASK_ENV') != 'production'
    app.run(debug=debug_mode, host='0.0.0.0',
            port=int(os.environ.get('PORT', 5000)))
