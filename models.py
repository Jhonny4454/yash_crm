from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date, timedelta
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
    role = db.Column(db.Enum('admin','support','field','accounts'), default='support')
    is_active = db.Column(db.Boolean, default=True)
    staff_type_id = db.Column(db.Integer, db.ForeignKey('staff_types.id'))
    monthly_salary = db.Column(db.Numeric(10,2), default=0.00)
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

class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Enum('Mr.','Mrs.','Ms.'), default='Mr.')
    customer_type = db.Column(db.Enum('Residential','Company','Commercial','Enterprise'), default='Residential')
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
    tax_type = db.Column(db.Enum('Taxable','Non-Taxable'), default='Taxable')
    connection_type = db.Column(db.Enum('Ethernet','FTTH','Lease Line'), default='FTTH')
    reference_id = db.Column(db.String(50), unique=True)
    zone = db.Column(db.String(50))
    registration_date = db.Column(db.Date, default=date.today)
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
    discount_percent = db.Column(db.Numeric(5,2), default=0.00)
    discount_amount = db.Column(db.Numeric(10,2), default=0.00)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plans = db.relationship('CustomerPlan', backref='customer', lazy=True)
    invoices = db.relationship('Invoice', backref='customer', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        return f"{self.title} {self.first_name} {self.last_name}".strip()

# ===== Service Provider Master =====
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
    isp_amount = db.Column(db.Numeric(10,2), default=0.00)
    service_provider_id = db.Column(db.Integer, db.ForeignKey('service_providers.id'), nullable=True)
    plan_type = db.Column(db.String(50))
    name = db.Column(db.String(50), nullable=False)
    speed_mbps = db.Column(db.Integer, nullable=False)
    price_monthly = db.Column(db.Numeric(10,2), nullable=False)
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
    status = db.Column(db.Enum('active','expired','cancelled','terminated'), default='active')
    auto_renew = db.Column(db.Boolean, default=True)
    grace_period_days = db.Column(db.Integer, default=1)
    last_invoice_date = db.Column(db.Date)
    suspension_review_status = db.Column(db.Enum('none','pending_review','terminated','re_enabled'), default='none')
    suspended_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    plan = db.relationship('Plan', backref='customer_plans')
    invoices = db.relationship('Invoice', backref='customer_plan', lazy=True)

class Invoice(db.Model):
    __tablename__ = 'invoices'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    customer_plan_id = db.Column(db.Integer, db.ForeignKey('customer_plans.id'), nullable=True)
    invoice_no = db.Column(db.String(20), unique=True, nullable=False)
    issue_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date, nullable=False)
    total_amount = db.Column(db.Numeric(10,2), nullable=False)
    tax_amount = db.Column(db.Numeric(10,2), default=0.00)
    discount_percent = db.Column(db.Numeric(5,2), default=0.00)
    discount_amount = db.Column(db.Numeric(10,2), default=0.00)
    receipt_number = db.Column(db.String(50))
    remarks = db.Column(db.Text)
    status = db.Column(db.Enum('draft','sent','paid','overdue','cancelled'), default='draft')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    caption = db.Column(db.String(120))
    invoice_type = db.Column(db.Enum('plan','addon','discount','other'), default='plan')
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
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    amount = db.Column(db.Numeric(10,2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    payment_mode = db.Column(db.Enum(
        'Cash','Cheque','Online Transfer','Credit Card','Paytm','GooglePay',
        'PhonePay','Bank Transfer','NEFT','RTGS','IMPS','UPI','Card','Online'
    ), default='Cash')
    mode_detail = db.Column(db.String(200))
    status = db.Column(db.Enum('pending','approved','rejected'), default='approved')
    gateway_transaction_id = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    book_receipt_no = db.Column(db.String(50))
    remarks = db.Column(db.Text)
    received_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    received_by_user = db.relationship('User', foreign_keys=[received_by_user_id])
    authorized_at = db.Column(db.DateTime)
    authorized_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    authorized_by_user = db.relationship('User', foreign_keys=[authorized_by_user_id])
    discount_amount = db.Column(db.Numeric(10, 2), default=0.00)

    @property
    def is_authorized(self):
        return self.status == 'approved'

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
    unit_price = db.Column(db.Numeric(10,2), default=0.00)
    cost_price = db.Column(db.Numeric(10,2), default=0.00)
    sku = db.Column(db.String(50))
    hsn_code = db.Column(db.String(20))
    tax_percent = db.Column(db.Numeric(5,2), default=0.00)
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
    amount = db.Column(db.Numeric(10,2))
    expense_date = db.Column(db.Date)
    description = db.Column(db.Text)
    prepared_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    passed_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    status = db.Column(db.Enum('draft','pending','approved','rejected'), default='pending')
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
    status = db.Column(db.Enum('present','absent','half-day'), default='present')

class Leave(db.Model):
    __tablename__ = 'leaves'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    reason = db.Column(db.Text)
    status = db.Column(db.Enum('pending','approved','rejected'), default='pending')

class Payroll(db.Model):
    __tablename__ = 'payroll'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    month_year = db.Column(db.Date)
    salary = db.Column(db.Numeric(10,2))
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

class TaxMaster(db.Model):
    __tablename__ = 'tax_master'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    value = db.Column(db.Numeric(5,2), nullable=False)
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

class MessageTemplate(db.Model):
    __tablename__ = 'message_templates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    template_type = db.Column(db.String(50), nullable=False, unique=True)
    body = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)