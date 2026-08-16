"""
models_ext.py  —  New tables required by the settings / backup / import-export /
referral / sales-return / multi-ISP features.

WIRING
------
Append the contents of this file to the BOTTOM of models.py, or keep it as a
separate module and add this line to models.py's imports section:

    from models_ext import *          # noqa: F401,F403

then run:  python migrate_v2.py
"""
from datetime import datetime, date
from decimal import Decimal

from cryptography.fernet import Fernet, InvalidToken
import os
import json

from models import db


# --------------------------------------------------------------------------- #
#  Secret storage
# --------------------------------------------------------------------------- #
def _fernet():
    """
    Key used to encrypt ISP API secrets at rest.

    Generate one with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    and put it in .env as CREDENTIAL_KEY=...
    """
    key = os.environ.get('CREDENTIAL_KEY')
    if not key:
        return None
    return Fernet(key.encode() if isinstance(key, str) else key)


class EncryptedSecretMixin:
    """Adds transparent encrypt/decrypt on a `_secret` column."""

    def set_secret(self, plaintext):
        if plaintext is None or plaintext == '':
            self._secret = None
            return
        f = _fernet()
        if f is None:
            # No key configured: refuse to store plaintext silently.
            raise RuntimeError(
                "CREDENTIAL_KEY is not set. Refusing to store an API secret in "
                "plaintext. Generate a key and set CREDENTIAL_KEY in your .env."
            )
        self._secret = f.encrypt(plaintext.encode()).decode()

    def get_secret(self):
        if not self._secret:
            return None
        f = _fernet()
        if f is None:
            return None
        try:
            return f.decrypt(self._secret.encode()).decode()
        except InvalidToken:
            return None

    @property
    def secret_is_set(self):
        return bool(self._secret)


# --------------------------------------------------------------------------- #
#  Application settings (Masters -> Company -> Settings screen)
# --------------------------------------------------------------------------- #
class Setting(db.Model):
    """
    Single-row key/value store backing the Settings screen.

    Access from anywhere with:
        Setting.get('invoice_prefix', 'IN')
        Setting.set('invoice_prefix', 'IN')
    """
    __tablename__ = 'settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False, index=True)
    value = db.Column(db.Text)
    value_type = db.Column(db.Enum('str', 'int', 'bool', 'decimal', 'json'),
                           default='str')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # ---- typed accessors -------------------------------------------------- #
    _CASTS = {
        'int': int,
        'bool': lambda v: str(v).lower() in ('1', 'true', 'yes', 'on'),
        'decimal': Decimal,
        'json': json.loads,
        'str': str,
    }

    @classmethod
    def get(cls, key, default=None):
        row = cls.query.filter_by(key=key).first()
        if row is None or row.value is None:
            return default
        try:
            return cls._CASTS.get(row.value_type or 'str', str)(row.value)
        except (ValueError, TypeError, json.JSONDecodeError):
            return default

    @classmethod
    def set(cls, key, value, value_type='str', user_id=None):
        row = cls.query.filter_by(key=key).first()
        if row is None:
            row = cls(key=key)
            db.session.add(row)
        row.value = (json.dumps(value) if value_type == 'json'
                     else ('' if value is None else str(value)))
        row.value_type = value_type
        row.updated_by_id = user_id
        return row

    @classmethod
    def as_dict(cls):
        return {r.key: cls.get(r.key) for r in cls.query.all()}


# Defaults seeded on first run — mirrors the fields on your Settings screen.
SETTING_DEFAULTS = [
    # (key, value, type)
    ('staff_prefix',            'S',      'str'),
    ('staff_next_no',           '47',     'int'),
    ('customer_prefix',         'C',      'str'),
    ('customer_next_no',        '2851',   'int'),
    ('invoice_prefix',          'IN',     'str'),
    ('invoice_next_no',         '4958',   'int'),
    ('receipt_prefix',          'R',      'str'),
    ('receipt_next_no',         '4252',   'int'),
    ('tax_type',                'Exclude', 'str'),   # Include | Exclude
    ('tax_on',                  'Total',  'str'),    # Base | Total
    ('invoice_package_price',   'Customer', 'str'),  # Customer | Master
    ('happy_code_enabled',      'False',  'bool'),
    ('coll_amount_change',      'True',   'bool'),
    ('coll_date_change',        'True',   'bool'),
    ('coll_renew_only',         'False',  'bool'),
    ('voucher_no',              '682',    'int'),
    ('discount_applicable',     'True',   'bool'),
    ('banner_link',             'referfriend.html', 'str'),
    ('banner_image',            '',       'str'),
    ('sms_template_renewal',
     'Dear {name}, your plan {plan} has been renewed until {expiry}. - YASH',
     'str'),
    ('sms_template_expiry',
     'Dear {name}, your plan expires on {expiry}. Please renew to avoid '
     'interruption. - YASH', 'str'),
    ('invoice_due_days',        '15',     'int'),
    ('grace_period_days',       '1',      'int'),

    # Outgoing mail. Off by default: with no SMTP host the mailer reports
    # 'dry-run' rather than pretending an invoice was delivered.
    ('mail_enabled',            'False',  'bool'),
    ('mail_from',               '',       'str'),
    ('mail_from_name',          '',       'str'),
    ('brevo_api_key',           '',       'str'),

    # Cloudinary cloud image storage. When enabled, logo, banner and customer
    # document uploads go to Cloudinary instead of the server's local disk,
    # which is wiped on every redeploy. Off keeps today's disk behaviour.
    ('cloudinary_enabled',      'False',  'bool'),
    ('cloudinary_cloud_name',   '',       'str'),
    ('cloudinary_api_key',      '',       'str'),
    ('cloudinary_api_secret',   '',       'str'),
    ('cloudinary_upload_preset','',       'str'),
    ('cloudinary_folder',       '',       'str'),
]


# --------------------------------------------------------------------------- #
#  Invoice line items  (device / addon billing against a vendor)
# --------------------------------------------------------------------------- #
class InvoiceItem(db.Model):
    """
    One row per billable line on an invoice.

    For a plan invoice there is normally a single item describing the plan.
    For an addon invoice there is one item per device taken from Inventory,
    which is how a router/ONU purchased from a Vendor gets billed to the
    customer and stays traceable back to that vendor's stock.
    """
    __tablename__ = 'invoice_items'

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'),
                           nullable=False, index=True)

    description = db.Column(db.String(255), nullable=False)
    item_type = db.Column(db.Enum('plan', 'device', 'service', 'other'),
                          default='other')

    # Link back to inventory / vendor when the line is a physical device
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'))
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendors.id'))
    serial_number = db.Column(db.String(100))

    quantity = db.Column(db.Integer, default=1, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    discount_amount = db.Column(db.Numeric(10, 2), default=0)
    tax_percent = db.Column(db.Numeric(5, 2), default=0)

    period_from = db.Column(db.Date)
    period_to = db.Column(db.Date)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    invoice = db.relationship('Invoice', backref=db.backref(
        'items', lazy=True, cascade='all, delete-orphan'))
    product = db.relationship('Product')
    vendor = db.relationship('Vendor')

    @property
    def base_amount(self):
        return Decimal(str(self.unit_price or 0)) * (self.quantity or 1)

    @property
    def taxable_amount(self):
        return self.base_amount - Decimal(str(self.discount_amount or 0))

    @property
    def tax_amount(self):
        return (self.taxable_amount * Decimal(str(self.tax_percent or 0))
                / Decimal('100')).quantize(Decimal('0.01'))

    @property
    def line_total(self):
        return self.taxable_amount + self.tax_amount


# --------------------------------------------------------------------------- #
#  Sales return / credit note
# --------------------------------------------------------------------------- #
class SalesReturn(db.Model):
    """
    A credit note raised against an existing invoice — used when a device is
    returned, a plan is cancelled mid-cycle, or an entry bill was raised in
    error and money has already been collected (so the invoice itself can no
    longer be deleted).
    """
    __tablename__ = 'sales_returns'

    id = db.Column(db.Integer, primary_key=True)
    return_no = db.Column(db.String(30), unique=True, nullable=False)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'),
                           nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'),
                            nullable=False, index=True)

    return_date = db.Column(db.Date, default=date.today, nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    reason = db.Column(db.Text)

    # Where the money goes back to
    refund_mode = db.Column(db.Enum('wallet', 'cash', 'bank', 'adjust_next'),
                            default='wallet')
    status = db.Column(db.Enum('pending', 'approved', 'rejected'),
                       default='pending')
    restock = db.Column(db.Boolean, default=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    invoice = db.relationship('Invoice', backref='sales_returns')
    customer = db.relationship('Customer', backref='sales_returns')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    approved_by = db.relationship('User', foreign_keys=[approved_by_id])


class SalesReturnItem(db.Model):
    __tablename__ = 'sales_return_items'

    id = db.Column(db.Integer, primary_key=True)
    sales_return_id = db.Column(db.Integer, db.ForeignKey('sales_returns.id'),
                                nullable=False)
    invoice_item_id = db.Column(db.Integer, db.ForeignKey('invoice_items.id'))
    description = db.Column(db.String(255))
    quantity = db.Column(db.Integer, default=1)
    amount = db.Column(db.Numeric(10, 2), default=0)

    sales_return = db.relationship('SalesReturn', backref=db.backref(
        'items', lazy=True, cascade='all, delete-orphan'))
    invoice_item = db.relationship('InvoiceItem')


# --------------------------------------------------------------------------- #
#  Referral campaign
# --------------------------------------------------------------------------- #
class ReferralCampaign(db.Model):
    __tablename__ = 'referral_campaigns'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(40), unique=True)
    description = db.Column(db.Text)

    reward_type = db.Column(db.Enum('fixed', 'percent', 'days'), default='fixed')
    referrer_reward = db.Column(db.Numeric(10, 2), default=0)
    referee_reward = db.Column(db.Numeric(10, 2), default=0)

    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_running(self):
        today = date.today()
        if not self.is_active:
            return False
        if self.start_date and today < self.start_date:
            return False
        if self.end_date and today > self.end_date:
            return False
        return True


class Referral(db.Model):
    __tablename__ = 'referrals'

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('referral_campaigns.id'))
    referrer_customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'),
                                     nullable=False)
    referee_customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'))
    referee_name = db.Column(db.String(120))
    referee_mobile = db.Column(db.String(20))

    status = db.Column(db.Enum('pending', 'converted', 'rewarded', 'rejected'),
                       default='pending')
    reward_credited = db.Column(db.Numeric(10, 2), default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    converted_at = db.Column(db.DateTime)

    campaign = db.relationship('ReferralCampaign', backref='referrals')
    referrer = db.relationship('Customer', foreign_keys=[referrer_customer_id])
    referee = db.relationship('Customer', foreign_keys=[referee_customer_id])


# --------------------------------------------------------------------------- #
#  ISP Credentials
# --------------------------------------------------------------------------- #
class ISPCredential(db.Model, EncryptedSecretMixin):
    """
    API credentials for one service provider.

    One ServiceProvider can have several credential rows (e.g. staging + live,
    or one per NAS/zone). The secret column is Fernet-encrypted at rest.
    """
    __tablename__ = 'isp_credentials'

    id = db.Column(db.Integer, primary_key=True)
    service_provider_id = db.Column(db.Integer,
                                    db.ForeignKey('service_providers.id'),
                                    nullable=False, index=True)

    # Which adapter in services/isp_providers.py handles this row
    driver = db.Column(db.String(40), nullable=False, default='log2space')
    label = db.Column(db.String(80))

    base_url = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(120))
    api_key = db.Column(db.String(255))
    _secret = db.Column('secret_enc', db.Text)

    # Free-form driver options, e.g. {"nas": "NAS1", "site": "AIROLI"}
    options_json = db.Column(db.Text)

    verify_ssl = db.Column(db.Boolean, default=True)
    timeout_seconds = db.Column(db.Integer, default=20)
    is_active = db.Column(db.Boolean, default=True)
    is_sandbox = db.Column(db.Boolean, default=False)

    last_ok_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    service_provider = db.relationship('ServiceProvider',
                                       backref='credentials')

    @property
    def options(self):
        try:
            return json.loads(self.options_json) if self.options_json else {}
        except json.JSONDecodeError:
            return {}

    @options.setter
    def options(self, value):
        self.options_json = json.dumps(value or {})

    @property
    def health(self):
        if self.last_error:
            return 'error'
        if self.last_ok_at:
            return 'ok'
        return 'untested'


class ISPSyncLog(db.Model):
    """Audit trail of every call made out to a provider."""
    __tablename__ = 'isp_sync_logs'

    id = db.Column(db.Integer, primary_key=True)
    credential_id = db.Column(db.Integer, db.ForeignKey('isp_credentials.id'))
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'))

    action = db.Column(db.String(60))          # enable / disable / renew / ...
    request_summary = db.Column(db.Text)
    response_summary = db.Column(db.Text)
    http_status = db.Column(db.Integer)
    success = db.Column(db.Boolean, default=False)
    duration_ms = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    credential = db.relationship('ISPCredential', backref='sync_logs')
    customer = db.relationship('Customer')


# --------------------------------------------------------------------------- #
#  Backup / import-export bookkeeping
# --------------------------------------------------------------------------- #
class BackupLog(db.Model):
    __tablename__ = 'backup_logs'

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255))
    size_bytes = db.Column(db.BigInteger)
    kind = db.Column(db.Enum('manual', 'scheduled'), default='manual')
    status = db.Column(db.Enum('running', 'success', 'failed'),
                       default='running')
    message = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    created_by = db.relationship('User')

    @property
    def size_human(self):
        n = float(self.size_bytes or 0)
        for unit in ('B', 'KB', 'MB', 'GB'):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"


class ImportJob(db.Model):
    __tablename__ = 'import_jobs'

    id = db.Column(db.Integer, primary_key=True)
    target = db.Column(db.String(40))          # customers / plans / payments ...
    filename = db.Column(db.String(255))
    total_rows = db.Column(db.Integer, default=0)
    ok_rows = db.Column(db.Integer, default=0)
    failed_rows = db.Column(db.Integer, default=0)
    error_report = db.Column(db.Text)          # CSV of failed rows + reason
    status = db.Column(db.Enum('pending', 'running', 'done', 'failed'),
                       default='pending')
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('User')

# --------------------------------------------------------------------------- #
#  Portal renewals
# --------------------------------------------------------------------------- #
class RenewalRequest(db.Model):
    """
    One renewal a customer started from the self-service portal.

    Covers both "renew what I already have" and "renew me onto a different
    plan" (an upgrade or downgrade), for a chosen number of billing cycles.
    The row is raised together with its invoice; the plan is only extended
    once an admin approves the linked payment, so a customer can never move
    their own expiry date.
    """
    __tablename__ = 'renewal_requests'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'),
                            nullable=False, index=True)
    customer_plan_id = db.Column(db.Integer, db.ForeignKey('customer_plans.id'))

    current_plan_id = db.Column(db.Integer, db.ForeignKey('plans.id'))
    requested_plan_id = db.Column(db.Integer, db.ForeignKey('plans.id'),
                                  nullable=False)

    #: How many billing cycles were bought in one go (1 / 3 / 6 / 12).
    months = db.Column(db.Integer, default=1, nullable=False)
    #: Total days the plan will be extended by once approved.
    days = db.Column(db.Integer, default=30, nullable=False)
    amount = db.Column(db.Numeric(10, 2), default=0, nullable=False)

    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'))
    payment_id = db.Column(db.Integer, db.ForeignKey('payments.id'))

    #: renew = same plan, change = upgrade/downgrade
    kind = db.Column(db.Enum('renew', 'change'), default='renew')
    #: pending -> approved | rejected | cancelled
    status = db.Column(db.Enum('pending', 'approved', 'rejected', 'cancelled'),
                       default='pending', index=True)

    note = db.Column(db.String(255))
    decision_note = db.Column(db.String(255))
    decided_at = db.Column(db.DateTime)
    decided_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    #: Filled in on approval so the history screen can show the new expiry.
    effective_from = db.Column(db.Date)
    effective_to = db.Column(db.Date)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    customer = db.relationship('Customer', backref=db.backref(
        'renewal_requests', lazy='dynamic'))
    customer_plan = db.relationship('CustomerPlan')
    current_plan = db.relationship('Plan', foreign_keys=[current_plan_id])
    requested_plan = db.relationship('Plan', foreign_keys=[requested_plan_id])
    invoice = db.relationship('Invoice')
    payment = db.relationship('Payment')
    decided_by = db.relationship('User')

    @property
    def is_upgrade(self):
        if not (self.current_plan and self.requested_plan):
            return False
        return (self.requested_plan.speed_mbps or 0) > (self.current_plan.speed_mbps or 0)

    @property
    def status_badge(self):
        return {'pending': 'warning', 'approved': 'success',
                'rejected': 'danger', 'cancelled': 'secondary'}.get(
                    self.status or '', 'secondary')

    @property
    def plan_label(self):
        if self.kind == 'change' and self.current_plan and self.requested_plan:
            return f"{self.current_plan.name} → {self.requested_plan.name}"
        return self.requested_plan.name if self.requested_plan else '-'

    @property
    def duration_label(self):
        m = self.months or 1
        return '1 month' if m == 1 else f'{m} months'
