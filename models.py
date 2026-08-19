from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date, timedelta
from decimal import Decimal
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    mobile = db.Column(db.String(20))
    role = db.Column(db.Enum('admin', 'support', 'field', 'accounts'), default='support')
    #: Comma-separated capability keys - see blueprints/api/permissions.py.
    #:
    #: NULL or empty means unrestricted, NOT "no access". Every user that
    #: existed before this column did is empty, and reading empty as "denied"
    #: would have locked the whole company out on the deploy that added it. A
    #: user becomes restricted the moment an administrator ticks their first
    #: box. Ignored entirely for role='admin', who always has everything.
    permissions = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    staff_type_id = db.Column(db.Integer, db.ForeignKey('staff_types.id'))
    monthly_salary = db.Column(db.Numeric(10, 2), default=0.00)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    staff_type = db.relationship('StaffType', backref='users')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'


class StaffType(db.Model):
    __tablename__ = 'staff_types'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)


class UsernameReservation(db.Model):
    """Every portal username that has ever been issued.

    The rule is that a username is spent the moment it is handed out and is
    never handed out again - not after the customer is deactivated, not after
    their row is deleted. A UNIQUE constraint on ``customers.username`` cannot
    express that on its own: it only knows about rows that still exist, so the
    day a customer record is removed their username silently becomes available
    and the next person to get it inherits their identity in every log, ticket
    and message history that still names them.

    So the reservation outlives the customer. ``customer_id`` is deliberately
    NOT a foreign key: the whole point is that this row survives its customer.

    ``username_key`` is the lowercased form and carries the UNIQUE index,
    because "Amar" and "amar" are the same login to a person even where the
    database collation says otherwise (SQLite compares case-sensitively;
    MySQL usually does not). Storing the comparison key explicitly means the
    rule does not change with the database engine.
    """
    __tablename__ = 'username_reservations'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False)
    username_key = db.Column(db.String(50), unique=True, nullable=False, index=True)
    #: 'customer' or 'staff' - separate login namespaces, reserved separately.
    scope = db.Column(db.String(16), nullable=False, default='customer')
    customer_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<UsernameReservation {self.username} ({self.scope})>'


class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Enum('Mr.', 'Mrs.', 'Ms.'), default='Mr.')
    customer_type = db.Column(db.Enum('Residential', 'Company', 'Commercial', 'Enterprise'), default='Residential')
    company_name = db.Column(db.String(100))
    first_name = db.Column(db.String(50), nullable=False)
    middle_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120))
    home_phone = db.Column(db.String(20))
    mobile = db.Column(db.String(20), nullable=False)
    username = db.Column(db.String(50), unique=True)
    password_hash = db.Column(db.String(200))
    gstin = db.Column(db.String(20))
    pan = db.Column(db.String(20))
    aadhar = db.Column(db.String(20))
    tax_type = db.Column(db.Enum('Taxable', 'Non-Taxable'), default='Taxable')
    connection_type = db.Column(db.Enum('Ethernet', 'FTTH', 'Lease Line'), default='FTTH')
    reference_id = db.Column(db.String(50), unique=True)
    zone = db.Column(db.String(50))
    registration_date = db.Column(db.Date, default=date.today)

    # ---- connection identity ------------------------------------------- #
    #: Static IP handed to this line, shown on the customer detail screen.
    ip_address = db.Column(db.String(45))
    #: The account id this customer has with the upstream provider (L2S etc.).
    ipacct_id = db.Column(db.String(50))
    service_provider_id = db.Column(db.Integer,
                                    db.ForeignKey('service_providers.id'))
    #: Prepaid bills before the period, Postpaid after it.
    billing_type = db.Column(db.Enum('Prepaid', 'Postpaid'), default='Prepaid')
    #: Day of month the recurring invoice is raised on.
    invoice_date = db.Column(db.Date)

    # ---- where the line physically is ---------------------------------- #
    latitude = db.Column(db.String(32))
    longitude = db.Column(db.String(32))

    #: Credit sitting on the account: overpayments and refunds land here and
    #: are drawn down by the next invoice. Never negative.
    wallet_balance = db.Column(db.Numeric(10, 2), default=0.00)

    flat_no = db.Column(db.String(50))
    locality = db.Column(db.String(100))
    area = db.Column(db.String(100))
    building = db.Column(db.String(100))
    billing_address = db.Column(db.Text)
    primary_address = db.Column(db.Text)
    reg_form_file = db.Column(db.String(255))
    photo_file = db.Column(db.String(255))
    address_proof_type = db.Column(db.String(50))
    address_proof_file = db.Column(db.String(255))
    id_proof_type = db.Column(db.String(50))
    id_proof_file = db.Column(db.String(255))
    notes = db.Column(db.Text)
    discount_percent = db.Column(db.Numeric(5, 2), default=0.00)
    discount_amount = db.Column(db.Numeric(10, 2), default=0.00)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plans = db.relationship('CustomerPlan', backref='customer', lazy=True)
    invoices = db.relationship('Invoice', backref='customer', lazy=True)
    service_provider = db.relationship('ServiceProvider',
                                       foreign_keys=[service_provider_id])

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        return f"{self.title} {self.first_name} {self.last_name}".strip()


class ServiceProvider(db.Model):
    __tablename__ = 'service_providers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    api_url = db.Column(db.String(255))
    api_username = db.Column(db.String(100))
    api_password = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    plans = db.relationship('Plan', backref='service_provider', lazy=True)


class Plan(db.Model):
    __tablename__ = 'plans'
    id = db.Column(db.Integer, primary_key=True)
    plan_code = db.Column(db.String(50))
    isp_amount = db.Column(db.Numeric(10, 2), default=0.00)
    service_provider_id = db.Column(db.Integer, db.ForeignKey('service_providers.id'), nullable=True)
    plan_type = db.Column(db.String(50))
    name = db.Column(db.String(50), nullable=False)
    speed_mbps = db.Column(db.Integer, nullable=False)
    price_monthly = db.Column(db.Numeric(10, 2), nullable=False)
    validity_days = db.Column(db.Integer, default=30)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CustomerPlan(db.Model):
    __tablename__ = 'customer_plans'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('plans.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.Enum('active', 'expired', 'cancelled', 'terminated'), default='active')
    auto_renew = db.Column(db.Boolean, default=True)
    #: Whether the CUSTOMER may renew this plan themselves from the portal.
    #:
    #: Separate from ``auto_renew`` on purpose, though the Plan tab used to
    #: print that one under an "Online Renewal" heading. ``auto_renew`` is the
    #: billing run's switch - it decides whether the office raises the next
    #: invoice automatically - and turning it off to stop a customer renewing
    #: online would also stop their bills being raised at all.
    online_renewal = db.Column(db.Boolean, default=True)
    grace_period_days = db.Column(db.Integer, default=1)
    last_invoice_date = db.Column(db.Date)
    # What THIS customer pays for the plan, when it differs from the master
    # price - the editable Total Amount on Assign Plan. NULL means "use the
    # master price", so an untouched row keeps following the plan.
    price = db.Column(db.Numeric(10, 2), nullable=True)
    suspension_review_status = db.Column(db.Enum('none', 'pending_review', 'terminated', 're_enabled'), default='none')
    suspended_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    plan = db.relationship('Plan', backref='customer_plans')
    invoices = db.relationship('Invoice', backref='customer_plan', lazy=True)

    @property
    def renewable_online(self):
        """Whether the portal should let this customer renew.

        NULL reads as yes: every row written before this column existed has
        no value, and a missing setting must not silently lock customers out
        of a screen that worked yesterday.
        """
        return self.online_renewal is not False

    @property
    def effective_price(self):
        """The agreed price for this customer's current plan.

        ``price`` is deliberately nullable: NULL means follow the shared plan
        price, while zero is a valid negotiated price and must stay zero.  A
        number of screens used ``price or plan.price_monthly`` before this
        property existed, which made an override appear to disappear after a
        refresh and made a free plan bill at its master price.
        """
        if self.price is not None:
            return self.price
        if self.plan is not None and self.plan.price_monthly is not None:
            return self.plan.price_monthly
        return Decimal('0.00')

    @property
    def _payment_mode(self):
        """Payment mode from the most recent approved payment on any invoice."""
        for inv in sorted(self.invoices or [], key=lambda i: i.id, reverse=True):
            for p in sorted(inv.payments or [], key=lambda x: x.id, reverse=True):
                if p.status == 'approved' and p.payment_mode:
                    return p.payment_mode
        return ''


class Invoice(db.Model):
    __tablename__ = 'invoices'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    customer_plan_id = db.Column(db.Integer, db.ForeignKey('customer_plans.id'), nullable=True)
    invoice_no = db.Column(db.String(20), unique=True, nullable=False)
    issue_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date, nullable=False)
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    tax_amount = db.Column(db.Numeric(10, 2), default=0.00)
    discount_percent = db.Column(db.Numeric(5, 2), default=0.00)
    discount_amount = db.Column(db.Numeric(10, 2), default=0.00)
    receipt_number = db.Column(db.String(50))
    remarks = db.Column(db.Text)
    #: Why the discount was given, copied from Discount Master at the time the
    #: bill was raised. Stored as text, not a FK, so renaming or retiring a
    #: reason later cannot rewrite history on invoices already issued.
    discount_reason = db.Column(db.String(100))
    status = db.Column(db.Enum('draft', 'sent', 'paid', 'overdue', 'cancelled'), default='draft')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    caption = db.Column(db.String(120))
    invoice_type = db.Column(db.Enum('plan', 'addon', 'discount', 'other'), default='plan')
    # The service window this bill covers. Distinct from issue_date on purpose:
    # a run done three days late still bills the month it was for, and matching
    # duplicates on issue_date would let that late run bill the month twice.
    period_start = db.Column(db.Date, nullable=True)
    period_end = db.Column(db.Date, nullable=True)
    vendor = db.Column(db.String(100), nullable=True)

    payments = db.relationship('Payment', backref='invoice', lazy=True)

    @property
    def paid_amount(self):
        return float(sum(float(p.amount or 0) for p in self.payments if p.status == 'approved'))

    @property
    def balance(self):
        return float(self.total_amount) - self.paid_amount - float(self.discount_amount or 0)

    @property
    def net_amount(self):
        return float(self.total_amount) - float(self.discount_amount or 0)

    @property
    def paid_modes(self):
        seen, out = set(), []
        for p in sorted(self.payments, key=lambda x: (x.payment_date, x.id)):
            if p.status == 'approved' and p.payment_mode and p.payment_mode not in seen:
                seen.add(p.payment_mode)
                out.append(p.payment_mode)
        return out

    @property
    def display_caption(self):
        if self.caption:
            return self.caption
        modes = self.paid_modes
        if modes:
            return ' / '.join(modes)
        if self.customer_plan and self.customer_plan.plan:
            return self.customer_plan.plan.name
        return '-'


class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    payment_mode = db.Column(db.Enum(
        'Cash', 'Cheque', 'Online Transfer', 'Credit Card', 'Paytm', 'GooglePay',
        'PhonePay', 'Bank Transfer', 'NEFT', 'RTGS', 'IMPS', 'UPI', 'Card', 'Online'
    ), default='Cash')
    mode_detail = db.Column(db.String(200))
    status = db.Column(db.Enum('pending', 'approved', 'rejected'), default='approved')
    gateway_transaction_id = db.Column(db.String(100))
    source = db.Column(db.String(20), default='admin', index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    book_receipt_no = db.Column(db.String(50))
    remarks = db.Column(db.Text)
    #: The payer. There was no relationship here at all, which is why the
    #: Payments screen printed a bare customer id where a name belongs -
    #: payment_dict had nothing to read a name from.
    customer = db.relationship('Customer', foreign_keys=[customer_id])

    received_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    received_by_user = db.relationship('User', foreign_keys=[received_by_user_id])
    authorized_at = db.Column(db.DateTime)
    authorized_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    authorized_by_user = db.relationship('User', foreign_keys=[authorized_by_user_id])
    discount_amount = db.Column(db.Numeric(10, 2), default=0.00)

    # ---- customer-submitted payment entries (portal) --------------------- #
    #: Bank / UPI reference the customer typed in. Searchable from the admin
    #: UTR-verification screen.
    utr = db.Column(db.String(60), index=True)
    #: Filename under static/uploads/payment_proofs/ of the screenshot the
    #: customer attached as proof.
    proof_file = db.Column(db.String(255))
    #: Why an admin rejected the entry, shown back to the customer.
    rejection_reason = db.Column(db.String(255))
    rejected_at = db.Column(db.DateTime)
    rejected_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    rejected_by_user = db.relationship('User', foreign_keys=[rejected_by_user_id])

    #: Same snapshot-not-FK reasoning as Invoice.discount_reason.
    discount_reason = db.Column(db.String(100))

    @property
    def receipt_no(self):
        """What this payment is called on a receipt.

        The manual book number when the counter wrote one, otherwise ``R``
        and the row id - so a payment taken online, which never goes near a
        receipt book, still has a reference the customer can quote.

        The expression was copied into the receipt PDF, the WhatsApp receipt
        message, the ledger and two download filenames, while the customer
        portal's Payments screen asked the API for a ``receipt_no`` no
        serializer ever sent - so that column was blank for a payment that
        had a number on every other document. One property now, so the
        receipt a customer is shown and the receipt they are sent cannot
        disagree.
        """
        return self.book_receipt_no or f'R{self.id}'

    @property
    def is_authorized(self):
        """True once an admin has signed this entry off."""
        return self.status == 'approved' and self.authorized_at is not None

    @property
    def needs_authorization(self):
        """
        Money is credited to the customer the moment it is recorded - the
        authorisation step is a *review*, not a gate. A payment is waiting for
        review while it counts toward the balance but no admin has signed it.
        """
        return self.status in ('approved', 'pending') and self.authorized_at is None

    @property
    def counts_toward_balance(self):
        return self.status == 'approved'

    @property
    def source_label(self):
        src = (self.source or '')
        if src == 'gateway':
            return 'Online Payment'
        if src == 'portal':
            return 'Customer Entry'
        return 'Counter Entry'

    @property
    def status_label(self):
        """Customer-facing wording for the portal."""
        return {'pending': 'Pending verification',
                'approved': 'Approved',
                'rejected': 'Rejected'}.get(self.status or '', self.status or '')

    @property
    def status_badge(self):
        """Bootstrap contextual class matching `status`."""
        return {'pending': 'warning',
                'approved': 'success',
                'rejected': 'danger'}.get(self.status or '', 'secondary')

    @property
    def reference(self):
        """The bank reference, wherever it was recorded."""
        return self.utr or self.gateway_transaction_id or self.mode_detail or ''

    @property
    def mode_group(self):
        m = (self.payment_mode or '').lower()
        if m == 'cash':
            return 'cash'
        if m == 'cheque':
            return 'cheque'
        if m in ('online transfer', 'neft', 'rtgs', 'imps', 'upi', 'paytm',
                 'googlepay', 'phonepay', 'bank transfer', 'online'):
            return 'online'
        return 'other'


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(100))
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    #: Set when the action was about one customer, so the Customer Log tab is
    #: an indexed lookup rather than a LIKE over every audit row ever written.
    #: Rows written before this column existed have it NULL; the log endpoint
    #: falls back to a name match for those.
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'),
                            nullable=True, index=True)
    user = db.relationship('User', backref='audit_logs')


class Vendor(db.Model):
    __tablename__ = 'vendors'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact_person = db.Column(db.String(100))
    mobile = db.Column(db.String(20))
    email = db.Column(db.String(120))
    gstin = db.Column(db.String(20))
    address = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    products = db.relationship('Product', back_populates='vendor', lazy=True)

    @property
    def product_count(self):
        return len([p for p in self.products if p.is_active])


class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    unit_price = db.Column(db.Numeric(10, 2), default=0.00)
    cost_price = db.Column(db.Numeric(10, 2), default=0.00)
    sku = db.Column(db.String(50))
    hsn_code = db.Column(db.String(20))
    tax_percent = db.Column(db.Numeric(5, 2), default=0.00)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendors.id'), nullable=True, index=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    vendor = db.relationship('Vendor', back_populates='products')

    @property
    def on_hand(self):
        row = Stock.query.filter_by(product_id=self.id).first()
        return row.quantity if row else 0

    @property
    def display_name(self):
        return f"{self.name} ({self.sku})" if self.sku else self.name


class Stock(db.Model):
    __tablename__ = 'stock'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'))
    quantity = db.Column(db.Integer, default=0)


class InventoryAssignment(db.Model):
    __tablename__ = 'inventory_assignments'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    serial_number = db.Column(db.String(100))
    assigned_date = db.Column(db.Date, default=date.today)
    status = db.Column(db.String(20), default='Active')

    customer = db.relationship('Customer', backref='inventory_assignments')
    product = db.relationship('Product')


class VendorBill(db.Model):
    __tablename__ = 'vendor_bills'
    id = db.Column(db.Integer, primary_key=True)
    bill_no = db.Column(db.String(30), unique=True, nullable=False)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendors.id'), nullable=False, index=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=True, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=True)
    bill_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date)
    total_amount = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    tax_amount = db.Column(db.Numeric(10, 2), default=0.00)
    paid_amount = db.Column(db.Numeric(10, 2), default=0.00)
    status = db.Column(db.Enum('draft', 'pending', 'partial', 'paid', 'cancelled'), default='pending')
    reference = db.Column(db.String(100))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    vendor = db.relationship('Vendor', backref=db.backref('bills', lazy=True))
    invoice = db.relationship('Invoice', backref=db.backref('vendor_bills', lazy=True))
    customer = db.relationship('Customer')
    items = db.relationship('VendorBillItem', backref='bill', lazy=True, cascade='all, delete-orphan')

    @property
    def balance(self):
        return float(self.total_amount or 0) - float(self.paid_amount or 0)

    def recalculate(self):
        self.total_amount = sum(float(i.line_total) for i in self.items)
        self.tax_amount = sum(float(i.tax_amount) for i in self.items)
        if self.balance <= 0 and float(self.total_amount or 0) > 0:
            self.status = 'paid'
        elif float(self.paid_amount or 0) > 0:
            self.status = 'partial'
        return self.total_amount


class VendorBillItem(db.Model):
    __tablename__ = 'vendor_bill_items'
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey('vendor_bills.id'), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'))
    description = db.Column(db.String(255), nullable=False)
    serial_number = db.Column(db.String(100))
    quantity = db.Column(db.Integer, default=1, nullable=False)
    unit_cost = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    tax_percent = db.Column(db.Numeric(5, 2), default=0.00)

    product = db.relationship('Product')

    @property
    def base_amount(self):
        return float(self.unit_cost or 0) * int(self.quantity or 1)

    @property
    def tax_amount(self):
        return self.base_amount * float(self.tax_percent or 0) / 100.0

    @property
    def line_total(self):
        return self.base_amount + self.tax_amount


class ExpenseCategory(db.Model):
    __tablename__ = 'expense_categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)


class ExpenseAccount(db.Model):
    __tablename__ = 'expense_accounts'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)


class ExpensePayee(db.Model):
    __tablename__ = 'expense_payees'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    mobile = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Expense(db.Model):
    __tablename__ = 'expenses'
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('expense_categories.id'))
    account_id = db.Column(db.Integer, db.ForeignKey('expense_accounts.id'))
    payee_id = db.Column(db.Integer, db.ForeignKey('expense_payees.id'))
    amount = db.Column(db.Numeric(10, 2))
    expense_date = db.Column(db.Date)
    description = db.Column(db.Text)
    prepared_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    passed_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    status = db.Column(db.Enum('draft', 'pending', 'approved', 'rejected'), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = db.relationship('ExpenseCategory')
    account = db.relationship('ExpenseAccount')
    payee = db.relationship('ExpensePayee')
    prepared_by = db.relationship('User', foreign_keys=[prepared_by_id])
    passed_by = db.relationship('User', foreign_keys=[passed_by_id])


class Attendance(db.Model):
    __tablename__ = 'attendance'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    date = db.Column(db.Date)
    # 'leave' and 'holiday' are days the office marks but nobody worked, and
    # the attendance screen has always offered them in its dropdown. The column
    # did not accept them: choosing either wrote a value the ORM then refused
    # to read back, so the row saved and every later load of the Attendance
    # page died on it. Widened here; upgrade_schema.py widens the live MySQL
    # column to match.
    status = db.Column(
        db.Enum('present', 'absent', 'half-day', 'leave', 'holiday'),
        default='present')


class Leave(db.Model):
    __tablename__ = 'leaves'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    reason = db.Column(db.Text)
    status = db.Column(db.Enum('pending', 'approved', 'rejected'), default='pending')


class Payroll(db.Model):
    __tablename__ = 'payroll'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    month_year = db.Column(db.Date)
    salary = db.Column(db.Numeric(10, 2))
    paid = db.Column(db.Boolean, default=False)


class Company(db.Model):
    __tablename__ = 'company'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    mobile = db.Column(db.String(20))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    bank_account_details = db.Column(db.Text)
    gstin = db.Column(db.String(20))
    pan_no = db.Column(db.String(20))
    sac_no = db.Column(db.String(20))
    place_of_supply = db.Column(db.String(100))
    state_code = db.Column(db.String(10))
    b2b_invoice_series = db.Column(db.String(50))
    b2c_invoice_series = db.Column(db.String(50))
    website_url = db.Column(db.String(255))
    company_type = db.Column(db.String(50))
    company_logo = db.Column(db.String(255))
    invoice_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


company_zones = db.Table('company_zones',
    db.Column('company_id', db.Integer, db.ForeignKey('company.id')),
    db.Column('zone_id', db.Integer, db.ForeignKey('zones.id'))
)


class Zone(db.Model):
    __tablename__ = 'zones'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(50))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    country = db.Column(db.String(100))
    logo = db.Column(db.String(255))
    l2s_site_name = db.Column(db.String(100))
    nas = db.Column(db.String(50))
    l2s_sync_id = db.Column(db.String(50))
    sms_url = db.Column(db.String(255))
    http_url = db.Column(db.String(255))
    whatsapp_url = db.Column(db.String(255))
    whatsapp_attachment_url = db.Column(db.String(255))
    company = db.Column(db.String(100))

    companies = db.relationship('Company', secondary=company_zones, backref='zones')


class Locality(db.Model):
    __tablename__ = 'localities'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)


class Area(db.Model):
    __tablename__ = 'areas'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)


class Building(db.Model):
    __tablename__ = 'buildings'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    locality_id = db.Column(db.Integer, db.ForeignKey('localities.id'), nullable=True)
    area_id = db.Column(db.Integer, db.ForeignKey('areas.id'), nullable=True)

    locality = db.relationship('Locality', backref='buildings', lazy=True)
    area = db.relationship('Area', backref='buildings', lazy=True)


class TaxMaster(db.Model):
    __tablename__ = 'tax_master'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    value = db.Column(db.Numeric(5, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Address(db.Model):
    __tablename__ = 'addresses'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    address_line = db.Column(db.Text)
    city = db.Column(db.String(50))
    state = db.Column(db.String(50))
    pincode = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ServiceRequest(db.Model):
    __tablename__ = 'service_requests'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    ticket_id = db.Column(db.String(20), unique=True, nullable=False)
    subject = db.Column(db.String(255))
    description = db.Column(db.Text)
    status = db.Column(db.Enum('open', 'in_progress', 'resolved', 'closed'), default='open')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)

    customer = db.relationship('Customer', backref='service_requests')


# =============================================================================
#  Messaging: editable customer templates + a delivery log
# =============================================================================

class AddonCategory(db.Model):
    """Preset categories for addon invoices (installation, shifting, ONT, ONU, etc.)."""
    __tablename__ = 'addon_categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    default_price = db.Column(db.Numeric(10, 2), default=0.00)
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class WalletEntry(db.Model):
    """
    One movement of the customer's account credit.

    `Customer.wallet_balance` is the running total; this table is the history
    behind it. Storing only the balance would make the Wallet tab a number
    nobody could explain, so every credit and debit lands here with the
    invoice or payment that caused it and a `balance_after` snapshot, which
    also makes a drifted balance obvious instead of silent.

    `amount` is signed: positive credits the customer, negative draws down.
    """
    __tablename__ = 'wallet_entries'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'),
                            nullable=False, index=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    balance_after = db.Column(db.Numeric(10, 2), default=0.00)
    #: credit | debit - derived from the sign, stored so reports can group.
    kind = db.Column(db.String(10), default='credit')
    reason = db.Column(db.String(200))
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'))
    payment_id = db.Column(db.Integer, db.ForeignKey('payments.id'))
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    customer = db.relationship('Customer', backref=db.backref(
        'wallet_entries', lazy='dynamic'))
    invoice = db.relationship('Invoice')
    payment = db.relationship('Payment')
    created_by = db.relationship('User')


class DiscountReason(db.Model):
    """
    Discount Master: the reasons an operator is allowed to knock money off an
    addon invoice ("Power Supply", "wire supply", ...).

    This is a controlled list on purpose. Before it existed the discount was a
    free-text amount with no explanation attached, so a month later nobody
    could say why a bill was short. Picking from a master means every discount
    on the ledger carries its reason.
    """
    __tablename__ = 'discount_reasons'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    #: Pre-filled when the reason is chosen; the operator can still override it.
    default_amount = db.Column(db.Numeric(10, 2), default=0.00)
    #: Percent discounts are stored here instead when the reason is a rate.
    default_percent = db.Column(db.Numeric(5, 2), default=0.00)
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class MessageTemplate(db.Model):
    """
    An editable WhatsApp / SMS message. `template_type` is the stable key the
    application code uses to look a template up; `name` is the human label
    shown in Masters -> Customer Templates.

    Bodies may contain {{placeholders}} - see services/messaging.py for the
    full list (customer_name, username, amount, balance, expiry_date, ...).
    """
    __tablename__ = 'message_templates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    template_type = db.Column(db.String(50), nullable=False, unique=True)
    body = db.Column(db.Text, nullable=False)
    #: whatsapp | sms  - which transport this template is written for
    channel = db.Column(db.String(20), default='whatsapp')
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True)

    # ---- Meta template mapping ------------------------------------------- #
    #
    # WhatsApp will not carry free text to somebody who has not messaged you in
    # the last 24 hours - and a bill, by definition, goes to somebody who has
    # not. Outside that window the message must be one Meta has approved in
    # advance, sent by NAME with its variables supplied separately.
    #
    # So each of these rows can carry two forms of the same message: the body
    # above, used inside the 24-hour window and by SMS, and the approved Meta
    # template named here, used everywhere else.

    #: The template's name as approved in Meta's WhatsApp Manager.
    meta_template_name = db.Column(db.String(100))

    #: Language code of the approved template, e.g. 'en' or 'en_US'. Meta
    #: matches on name AND language, and rejects a mismatch.
    meta_language = db.Column(db.String(10), default='en')

    #: Comma-separated context keys filling {{1}}, {{2}}, ... IN ORDER.
    #: e.g. "customer_name,amount,due_date". Order is the whole contract -
    #: Meta sends positionally and has no idea what each value means.
    meta_variables = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    #: Types the scheduler and billing code send automatically. These cannot be
    #: deleted from the UI (you can edit the wording or switch them off).
    SYSTEM_TYPES = (
        'renewal', 'expiry_3d', 'expiry_2d', 'expired',
        'payment_received', 'due_reminder', 'bill',
        # Mapped to Meta-approved templates, so deleting one silently breaks
        # sending outside the 24-hour window.
        'summary_bill', 'detailed_bill', 'payment_approved', 'welcome',
    )

    @property
    def is_system(self):
        return self.template_type in self.SYSTEM_TYPES


class MessageLog(db.Model):
    """One row per outbound message attempt - powers the customer SMS log."""
    __tablename__ = 'message_logs'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=True, index=True)
    phone = db.Column(db.String(25))
    channel = db.Column(db.String(20), default='whatsapp')   # whatsapp | sms
    template_type = db.Column(db.String(50))
    body = db.Column(db.Text)
    #: sent | failed | skipped | dry-run
    status = db.Column(db.String(20), default='sent', index=True)
    error = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    customer = db.relationship('Customer', backref=db.backref('message_logs', lazy='dynamic'))


class OnlinePaymentOrder(db.Model):
    """
    A Cashfree order raised from the customer self-service portal.

    The row is created *before* the customer is sent to checkout, then updated
    from the return URL and again (authoritatively) from the signed webhook.
    """
    __tablename__ = 'online_payment_orders'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False, index=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payments.id'), nullable=True)
    gateway = db.Column(db.String(20), default='cashfree')
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    #: created | paid | failed | expired
    status = db.Column(db.String(20), default='created', index=True)
    payment_session_id = db.Column(db.String(255))
    cf_order_id = db.Column(db.String(64))
    #: Bank / UPI reference shown to the operator as the "transaction id"
    transaction_id = db.Column(db.String(100))
    payment_method = db.Column(db.String(50))
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = db.relationship('Customer', backref='online_orders')
    invoice = db.relationship('Invoice')
    payment = db.relationship('Payment')
    
