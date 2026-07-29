<<<<<<< HEAD
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (
    StringField, PasswordField, SelectField, DateField, DecimalField,
    TextAreaField, BooleanField, IntegerField, HiddenField, SubmitField, RadioField,
    SelectMultipleField
)
from wtforms.validators import DataRequired, Email, Length, Optional, NumberRange, EqualTo
from datetime import date

PAYMENT_MODE_CHOICES = [
    ('', '-Select Mode-'),
    ('Cash', 'Cash'),
    ('Cheque', 'Cheque'),
    ('Online Transfer', 'Online Transfer'),
    ('Credit Card', 'Credit Card'),
    ('Paytm', 'Paytm'),
    ('GooglePay', 'GooglePay'),
    ('PhonePay', 'PhonePay'),
    ('Bank Transfer', 'Bank Transfer'),
]

# ---------- Authentication ----------
class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Keep me signed in')
    submit = SubmitField('Login')

class CustomerLoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Keep me signed in')
    submit = SubmitField('Login')

class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField('Change Password')

class CustomerChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField('Update Password')

# ---------- Zone Form ----------
class ZoneForm(FlaskForm):
    name = StringField('Zone Name', validators=[DataRequired()])
    code = StringField('Zone Code')
    phone = StringField('Phone No')
    email = StringField('Email', validators=[Optional(), Email()])
    address = TextAreaField('Address', render_kw={"rows": 2})
    city = StringField('City')
    state = StringField('State')
    country = StringField('Country')
    logo = FileField('Logo', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif'], 'Images only!')])
    l2s_site_name = StringField('L2s Site Name')
    nas = StringField('NAS')
    l2s_sync_id = StringField('L2sSync/Ijaz Id')
    sms_url = StringField('SMS Url')
    http_url = StringField('Http')
    whatsapp_url = StringField('WhatsApp URL')
    whatsapp_attachment_url = StringField('WhatsApp Attachment Url')
    company = StringField('Company')
    submit = SubmitField('Save Zone')

# ---------- Company Form ----------
class CompanyForm(FlaskForm):
    name = StringField('Company Name', validators=[DataRequired()])
    mobile = StringField('Mobile', render_kw={"placeholder": "Mobile"})
    phone = StringField('Phone', render_kw={"placeholder": "Phone"})
    email = StringField('Email', validators=[Optional(), Email()], render_kw={"placeholder": "Email"})
    address = TextAreaField('Address', render_kw={"rows": 2})
    bank_account_details = TextAreaField('Bank Account Detail', render_kw={"rows": 2})
    gstin = StringField('GSTIN')
    pan_no = StringField('Pan No.')
    sac_no = StringField('SAC No.')
    place_of_supply = StringField('Place Of Supply')
    state_code = StringField('State Code')
    b2b_invoice_series = StringField('B2B Invoice Series')
    b2c_invoice_series = StringField('B2C Invoice Series')
    website_url = StringField('Website Url')
    company_type = StringField('Company Type')
    company_logo = FileField('Company Logo', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif'], 'Images only!')])
    invoice_notes = TextAreaField('Invoice Notes', render_kw={"rows": 4})
    zones = SelectMultipleField('Zones', coerce=int, validators=[Optional()])
    submit = SubmitField('Submit')

# ---------- Tax Master Form ----------
class TaxForm(FlaskForm):
    name = StringField('Tax Name', validators=[DataRequired()])
    value = DecimalField('Value (%)', validators=[DataRequired(), NumberRange(min=0, max=100)])
    submit = SubmitField('Save Tax')

# ---------- Customer Form ----------
class CustomerForm(FlaskForm):
    title = SelectField('Title', choices=[('Mr.', 'Mr.'), ('Mrs.', 'Mrs.'), ('Ms.', 'Ms.')])
    customer_type = SelectField('Customer Type', choices=[
        ('Residential', 'Residential'),
        ('Company', 'Company'),
        ('Commercial', 'Commercial'),
        ('Enterprise', 'Enterprise')
    ])
    company_name = StringField('Company Name')
    first_name = StringField('First Name', validators=[DataRequired()])
    middle_name = StringField('Middle Name')
    last_name = StringField('Last Name', validators=[DataRequired()])
    
    email = StringField('Email', validators=[Optional(), Email()])
    home_phone = StringField('Home Phone')
    mobile = StringField('Mobile', validators=[DataRequired()])
    username = StringField('Username')
    
    password = PasswordField('Password', validators=[Optional(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[Optional(), EqualTo('password')])
    
    gstin = StringField('GSTIN')
    pan = StringField('PAN')
    aadhar = StringField('Aadhar')
    tax_type = SelectField('Tax Type', choices=[('Taxable', 'Taxable'), ('Non-Taxable', 'Non-Taxable')])
    
    connection_type = RadioField('Connection Type', choices=[
        ('Ethernet', 'Ethernet'),
        ('FTTH', 'FTTH'),
        ('Lease Line', 'Lease Line')
    ], default='FTTH')
    
    reference_id = StringField('Reference ID')
    zone = SelectField('Zone', coerce=str, validators=[Optional()])
    registration_date = DateField('Registration Date', validators=[Optional()], default=date.today)
    
    flat_no = StringField('Flat No', validators=[DataRequired()])
    locality = SelectField('Locality', coerce=str, validators=[DataRequired()])
    area = SelectField('Area', coerce=str, validators=[DataRequired()])
    building = SelectField('Building', coerce=str, validators=[DataRequired()])
    
    primary_address = TextAreaField('Primary Address', render_kw={"rows": 2})
    same_as_billing = BooleanField('Same as Billing Address', default=True)
    
    reg_form = FileField('Reg Form', validators=[Optional(), FileAllowed(['jpg', 'png', 'pdf', 'jpeg'])])
    photo = FileField('Photo', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg'])])
    address_proof_type = SelectField('Address Proof', validators=[Optional()], choices=[
        ('', 'Select Address Proof'),
        ('Aadhaar', 'Aadhaar'),
        ('Ration Card', 'Ration Card'),
        ('Passport', 'Passport'),
        ('Driving License', 'Driving License'),
        ('Voter ID', 'Voter ID')
    ])
    address_proof = FileField('Address Proof File', validators=[Optional(), FileAllowed(['jpg', 'png', 'pdf', 'jpeg'])])
    id_proof_type = SelectField('ID Proof', validators=[Optional()], choices=[
        ('', 'Select Identity Proof'),
        ('Aadhaar', 'Aadhaar'),
        ('PAN', 'PAN'),
        ('Passport', 'Passport'),
        ('Driving License', 'Driving License'),
        ('Voter ID', 'Voter ID')
    ])
    id_proof = FileField('ID Proof File', validators=[Optional(), FileAllowed(['jpg', 'png', 'pdf', 'jpeg'])])
    submit = SubmitField('Save Customer')

# ---------- Service Provider Form ----------
class ServiceProviderForm(FlaskForm):
    name = StringField('Provider Name', validators=[DataRequired()])
    is_active = BooleanField('Active (Enable)', default=True)
    api_url = StringField('API URL')
    api_username = StringField('API Username')
    api_password = PasswordField('API Password', validators=[Optional(), Length(min=6)])
    confirm_password = PasswordField('Confirm API Password', validators=[Optional(), EqualTo('api_password')])
    submit = SubmitField('Save Provider')

# ---------- Plan ----------
class PlanForm(FlaskForm):
    plan_code = StringField('Plan Code')
    service_provider_id = SelectField('Service Provider', coerce=int, validators=[Optional()])
    plan_type = StringField('Plan Type')
    name = StringField('Plan Name', validators=[DataRequired()])
    speed_mbps = IntegerField('Speed (Mbps)', validators=[DataRequired(), NumberRange(min=1)])
    price_monthly = DecimalField('Monthly Price (₹)', validators=[DataRequired(), NumberRange(min=0)])
    isp_amount = DecimalField('ISP Amount (₹)', validators=[Optional(), NumberRange(min=0)])
    validity_days = IntegerField('Validity (days)', default=30)
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Plan')

# ---------- Invoice ----------
class InvoiceForm(FlaskForm):
    customer_id = HiddenField('Customer ID', validators=[DataRequired()])
    customer_plan_id = SelectField('Plan (Caption)', coerce=int, validators=[Optional()])
    invoice_no = StringField('Invoice No', validators=[DataRequired()])
    issue_date = DateField('Issue Date', default=date.today)
    due_date = DateField('Due Date', default=date.today)
    total_amount = DecimalField('Total Amount (₹)', validators=[DataRequired(), NumberRange(min=0)])
    tax_amount = DecimalField('Tax Amount (₹)', default=0)
    
    discount_percent = DecimalField('Discount (%)', default=0.00)
    discount_amount = DecimalField('Discount Amount (₹)', default=0.00)
    receipt_number = StringField('Receipt No')
    caption = StringField('Caption')
    invoice_type = SelectField('Invoice Type', choices=[
        ('plan', 'Plan'), ('addon', 'Addon'), ('discount', 'Discount'), ('other', 'Other')
    ], default='plan')
    remarks = TextAreaField('Remarks', render_kw={"rows": 2})

    status = SelectField('Status', choices=[
        ('draft', 'Draft'), ('sent', 'Sent'), ('paid', 'Paid'),
        ('overdue', 'Overdue'), ('cancelled', 'Cancelled')
    ])
    submit = SubmitField('Save Invoice')

# ---------- Payment ----------
class PaymentForm(FlaskForm):
    invoice_id = HiddenField('Invoice ID')
    customer_id = HiddenField('Customer ID')
    amount = DecimalField('Amount (₹)', validators=[DataRequired(), NumberRange(min=0.01)])
    payment_date = DateField('Payment Date', default=date.today)
    payment_mode = SelectField('Payment Mode', choices=PAYMENT_MODE_CHOICES, validators=[DataRequired()])
    mode_detail = StringField('Mode Detail')
    status = SelectField('Status', choices=[
        ('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')
    ], default='approved')
    
    book_receipt_no = StringField('Book Receipt No')
    remarks = TextAreaField('Remarks', render_kw={"rows": 2})
    
    submit = SubmitField('Record Payment')

# ---------- Staff ----------
class StaffForm(FlaskForm):
    full_name = StringField('Full Name', validators=[DataRequired()])
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[Optional(), Length(min=6)])
    email = StringField('Email', validators=[Optional(), Email()])
    mobile = StringField('Mobile')
    role = SelectField('Role', choices=[
        ('admin', 'Admin'), ('support', 'Support'),
        ('field', 'Field Engineer'), ('accounts', 'Accounts')
    ])
    staff_type_id = SelectField('Staff Type', coerce=int, validators=[DataRequired()])
    monthly_salary = DecimalField('Monthly Salary (₹)', validators=[Optional(), NumberRange(min=0)])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Staff')

# ---------- HR & Payroll ----------
class AttendanceForm(FlaskForm):
    user_id = SelectField('Staff', coerce=int, validators=[DataRequired()])
    date = DateField('Date', default=date.today)
    status = SelectField('Status', choices=[
        ('present', 'Present'), ('absent', 'Absent'), ('half-day', 'Half Day')
    ])
    submit = SubmitField('Record Attendance')

class LeaveForm(FlaskForm):
    user_id = SelectField('Staff', coerce=int, validators=[DataRequired()])
    start_date = DateField('Start Date', validators=[DataRequired()])
    end_date = DateField('End Date', validators=[DataRequired()])
    reason = TextAreaField('Reason')
    status = SelectField('Status', choices=[
        ('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')
    ], default='pending')
    submit = SubmitField('Submit Leave')

class PayrollForm(FlaskForm):
    user_id = SelectField('Staff', coerce=int, validators=[DataRequired()])
    month_year = DateField('Month/Year', default=date.today)
    salary = DecimalField('Salary (₹)', validators=[DataRequired(), NumberRange(min=0)])
    paid = BooleanField('Paid')
    submit = SubmitField('Save Payroll')

# ---------- Expenses ----------
class ExpenseForm(FlaskForm):
    category_id = SelectField('Category', coerce=int, validators=[DataRequired()])
    account_id = SelectField('Account', coerce=int, validators=[DataRequired()])
    payee_id = SelectField('Payee', coerce=int, validators=[DataRequired()])
    amount = DecimalField('Amount (₹)', validators=[DataRequired(), NumberRange(min=0)])
    expense_date = DateField('Expense Date', default=date.today)
    description = TextAreaField('Description')
    prepared_by_id = SelectField('Prepared By', coerce=int, validators=[Optional()])
    passed_by_id = SelectField('Passed By', coerce=int, validators=[Optional()])
    status = SelectField('Status', choices=[
        ('draft', 'Draft'),
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected')
    ], default='pending')
    submit = SubmitField('Save Expense')

class ExpenseCategoryForm(FlaskForm):
    name = StringField('Category Name', validators=[DataRequired()])
    submit = SubmitField('Add Category')

class ExpenseAccountForm(FlaskForm):
    name = StringField('Account Name', validators=[DataRequired()])
    submit = SubmitField('Add Account')

class ExpensePayeeForm(FlaskForm):
    name = StringField('Payee Name', validators=[DataRequired()])
    mobile = StringField('Mobile', validators=[Optional()])
    email = StringField('Email', validators=[Optional(), Email()])
    address = TextAreaField('Address', validators=[Optional()], render_kw={"rows": 2})
    submit = SubmitField('Add Payee')

# ---------- Inventory ----------
class VendorForm(FlaskForm):
    name = StringField('Vendor Name', validators=[DataRequired(), Length(max=100)])
    contact_person = StringField('Contact Person', validators=[Optional(), Length(max=100)])
    mobile = StringField('Mobile', validators=[Optional(), Length(max=20)])
    email = StringField('Email', validators=[Optional(), Email(), Length(max=120)])
    gstin = StringField('GSTIN', validators=[Optional(), Length(max=20)])
    address = TextAreaField('Address', validators=[Optional()])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Vendor')

class ProductForm(FlaskForm):
    name = StringField('Product Name', validators=[DataRequired(), Length(max=100)])
    vendor_id = SelectField('Vendor', coerce=int, validators=[Optional()])
    sku = StringField('SKU / Model', validators=[Optional(), Length(max=50)])
    hsn_code = StringField('HSN Code', validators=[Optional(), Length(max=20)])
    description = TextAreaField('Description')
    cost_price = DecimalField('Purchase Price from Vendor (\u20b9)',
                              default=0, validators=[Optional(), NumberRange(min=0)])
    unit_price = DecimalField('Selling Price to Customer (\u20b9)',
                              default=0, validators=[Optional(), NumberRange(min=0)])
    tax_percent = DecimalField('Tax %', default=0,
                               validators=[Optional(), NumberRange(min=0, max=100)])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Product')

class VendorBillForm(FlaskForm):
    vendor_id = SelectField('Vendor', coerce=int, validators=[DataRequired()])
    bill_date = DateField('Bill Date', default=date.today, validators=[DataRequired()])
    due_date = DateField('Due Date', validators=[Optional()])
    reference = StringField('Vendor Invoice / Reference No.',
                            validators=[Optional(), Length(max=100)])
    notes = TextAreaField('Notes', validators=[Optional()])
    submit = SubmitField('Save Bill')

class VendorBillPaymentForm(FlaskForm):
    amount = DecimalField('Amount Paid (\u20b9)',
                          validators=[DataRequired(), NumberRange(min=0.01)])
    submit = SubmitField('Record Payment')

class StockForm(FlaskForm):
    product_id = SelectField('Product', coerce=int, validators=[DataRequired()])
    quantity = IntegerField('Quantity', validators=[DataRequired(), NumberRange(min=0)])
    submit = SubmitField('Update Stock')

# ---------- Masters ----------
class AddressForm(FlaskForm):
    line1 = StringField('Address Line 1', validators=[DataRequired()])
    line2 = StringField('Address Line 2')
    city = StringField('City', validators=[DataRequired()])
    state = StringField('State', validators=[DataRequired()])
    pincode = StringField('Pincode', validators=[DataRequired()])
    submit = SubmitField('Save Address')

class CustomerPlanForm(FlaskForm):
    customer_id = SelectField('Customer', coerce=int, validators=[DataRequired()])
    plan_id = SelectField('Plan', coerce=int, validators=[DataRequired()])
    start_date = DateField('Start Date', default=date.today)
    end_date = DateField('End Date', validators=[DataRequired()])
    status = SelectField('Status', choices=[
        ('active', 'Active'), ('inactive', 'Inactive'), ('terminated', 'Terminated')
    ], default='active')
    auto_renew = BooleanField('Auto Renew', default=True)
    grace_period_days = IntegerField('Grace Period (days)', default=1)
    submit = SubmitField('Assign Plan')

class AddonInvoiceForm(FlaskForm):
    summary_invoice = StringField('Summary Invoice')
    detailed_invoice = StringField('Detailed Invoice')
    caption = StringField('Caption')
    invoice_date = DateField('Invoice Date', default=date.today)
    payment_mode = SelectField('Mode', choices=PAYMENT_MODE_CHOICES, validators=[Optional()])
    invoice_amount = DecimalField('Invoice Amount', default=0, validators=[Optional(), NumberRange(min=0)])
    discount_amount = DecimalField('Discount', default=0, validators=[Optional(), NumberRange(min=0)])
    book_receipt_no = StringField('Book Receipt No.')
    remark = StringField('Remark')
    submit = SubmitField('Submit')

class PlanDatesForm(FlaskForm):
    start_date = DateField('Renew Date', validators=[DataRequired()])
    end_date = DateField('Expiry Date', validators=[DataRequired()])
    status = SelectField('Status', choices=[
        ('active', 'Active'), ('expired', 'Expired'),
        ('cancelled', 'Cancelled'), ('terminated', 'Terminated')
    ], default='active')
    submit = SubmitField('Update Dates')

# =========================================================== #
#  NEW FORMS FOR CUSTOMER PORTAL & MESSAGE TEMPLATES          #
# =========================================================== #

class MessageTemplateForm(FlaskForm):
    """Form for creating/editing WhatsApp/SMS templates."""
    name = StringField('Template Name', validators=[DataRequired(), Length(max=100)])
    template_type = SelectField('Template Type', choices=[
        ('renewal', 'Plan Renewed'),
        ('payment_received', 'Payment Received'),
        ('expiry_3d', 'Expiry Reminder (3 Days)'),
        ('expiry_2d', 'Expiry Reminder (2 Days)'),
        ('expired', 'Plan Expired')
    ], validators=[DataRequired()])
    body = TextAreaField('Message Body', validators=[DataRequired()], 
                         render_kw={"rows": 6, "placeholder": "Use {{customer_name}}, {{username}}, {{amount}}, {{days}}, {{balance}}, {{paid_amount}} as placeholders."})
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Template')

class BulkMessageForm(FlaskForm):
    """Form for sending bulk expiry messages to customers within a date range."""
    template_id = SelectField('Select Template', coerce=int, validators=[DataRequired()])
    start_date = DateField('Start Date', validators=[DataRequired()])
    end_date = DateField('End Date', validators=[DataRequired()])
=======
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (
    StringField, PasswordField, SelectField, DateField, DecimalField,
    TextAreaField, BooleanField, IntegerField, HiddenField, SubmitField, RadioField,
    SelectMultipleField
)
from wtforms.validators import DataRequired, Email, Length, Optional, NumberRange, EqualTo
from datetime import date

PAYMENT_MODE_CHOICES = [
    ('', '-Select Mode-'),
    ('Cash', 'Cash'),
    ('Cheque', 'Cheque'),
    ('Online Transfer', 'Online Transfer'),
    ('Credit Card', 'Credit Card'),
    ('Paytm', 'Paytm'),
    ('GooglePay', 'GooglePay'),
    ('PhonePay', 'PhonePay'),
    ('Bank Transfer', 'Bank Transfer'),
]

# ---------- Authentication ----------
class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Keep me signed in')
    submit = SubmitField('Login')

class CustomerLoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Keep me signed in')
    submit = SubmitField('Login')

class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField('Change Password')

class CustomerChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField('Update Password')

# ---------- Zone Form ----------
class ZoneForm(FlaskForm):
    name = StringField('Zone Name', validators=[DataRequired()])
    code = StringField('Zone Code')
    phone = StringField('Phone No')
    email = StringField('Email', validators=[Optional(), Email()])
    address = TextAreaField('Address', render_kw={"rows": 2})
    city = StringField('City')
    state = StringField('State')
    country = StringField('Country')
    logo = FileField('Logo', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif'], 'Images only!')])
    l2s_site_name = StringField('L2s Site Name')
    nas = StringField('NAS')
    l2s_sync_id = StringField('L2sSync/Ijaz Id')
    sms_url = StringField('SMS Url')
    http_url = StringField('Http')
    whatsapp_url = StringField('WhatsApp URL')
    whatsapp_attachment_url = StringField('WhatsApp Attachment Url')
    company = StringField('Company')
    submit = SubmitField('Save Zone')

# ---------- Company Form ----------
class CompanyForm(FlaskForm):
    name = StringField('Company Name', validators=[DataRequired()])
    mobile = StringField('Mobile', render_kw={"placeholder": "Mobile"})
    phone = StringField('Phone', render_kw={"placeholder": "Phone"})
    email = StringField('Email', validators=[Optional(), Email()], render_kw={"placeholder": "Email"})
    address = TextAreaField('Address', render_kw={"rows": 2})
    bank_account_details = TextAreaField('Bank Account Detail', render_kw={"rows": 2})
    gstin = StringField('GSTIN')
    pan_no = StringField('Pan No.')
    sac_no = StringField('SAC No.')
    place_of_supply = StringField('Place Of Supply')
    state_code = StringField('State Code')
    b2b_invoice_series = StringField('B2B Invoice Series')
    b2c_invoice_series = StringField('B2C Invoice Series')
    website_url = StringField('Website Url')
    company_type = StringField('Company Type')
    company_logo = FileField('Company Logo', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif'], 'Images only!')])
    invoice_notes = TextAreaField('Invoice Notes', render_kw={"rows": 4})
    zones = SelectMultipleField('Zones', coerce=int, validators=[Optional()])
    submit = SubmitField('Submit')

# ---------- Tax Master Form ----------
class TaxForm(FlaskForm):
    name = StringField('Tax Name', validators=[DataRequired()])
    value = DecimalField('Value (%)', validators=[DataRequired(), NumberRange(min=0, max=100)])
    submit = SubmitField('Save Tax')

# ---------- Customer Form ----------
class CustomerForm(FlaskForm):
    title = SelectField('Title', choices=[('Mr.', 'Mr.'), ('Mrs.', 'Mrs.'), ('Ms.', 'Ms.')])
    customer_type = SelectField('Customer Type', choices=[
        ('Residential', 'Residential'),
        ('Company', 'Company'),
        ('Commercial', 'Commercial'),
        ('Enterprise', 'Enterprise')
    ])
    company_name = StringField('Company Name')
    first_name = StringField('First Name', validators=[DataRequired()])
    middle_name = StringField('Middle Name')
    last_name = StringField('Last Name', validators=[DataRequired()])
    
    email = StringField('Email', validators=[Optional(), Email()])
    home_phone = StringField('Home Phone')
    mobile = StringField('Mobile', validators=[DataRequired()])
    username = StringField('Username')
    
    password = PasswordField('Password', validators=[Optional(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[Optional(), EqualTo('password')])
    
    gstin = StringField('GSTIN')
    pan = StringField('PAN')
    aadhar = StringField('Aadhar')
    tax_type = SelectField('Tax Type', choices=[('Taxable', 'Taxable'), ('Non-Taxable', 'Non-Taxable')])
    
    connection_type = RadioField('Connection Type', choices=[
        ('Ethernet', 'Ethernet'),
        ('FTTH', 'FTTH'),
        ('Lease Line', 'Lease Line')
    ], default='FTTH')
    
    reference_id = StringField('Reference ID')
    zone = SelectField('Zone', coerce=str, validators=[Optional()])
    registration_date = DateField('Registration Date', validators=[Optional()], default=date.today)
    
    flat_no = StringField('Flat No', validators=[DataRequired()])
    locality = SelectField('Locality', coerce=str, validators=[DataRequired()])
    area = SelectField('Area', coerce=str, validators=[DataRequired()])
    building = SelectField('Building', coerce=str, validators=[DataRequired()])
    
    primary_address = TextAreaField('Primary Address', render_kw={"rows": 2})
    same_as_billing = BooleanField('Same as Billing Address', default=True)
    
    reg_form = FileField('Reg Form', validators=[Optional(), FileAllowed(['jpg', 'png', 'pdf', 'jpeg'])])
    photo = FileField('Photo', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg'])])
    address_proof_type = SelectField('Address Proof', validators=[Optional()], choices=[
        ('', 'Select Address Proof'),
        ('Aadhaar', 'Aadhaar'),
        ('Ration Card', 'Ration Card'),
        ('Passport', 'Passport'),
        ('Driving License', 'Driving License'),
        ('Voter ID', 'Voter ID')
    ])
    address_proof = FileField('Address Proof File', validators=[Optional(), FileAllowed(['jpg', 'png', 'pdf', 'jpeg'])])
    id_proof_type = SelectField('ID Proof', validators=[Optional()], choices=[
        ('', 'Select Identity Proof'),
        ('Aadhaar', 'Aadhaar'),
        ('PAN', 'PAN'),
        ('Passport', 'Passport'),
        ('Driving License', 'Driving License'),
        ('Voter ID', 'Voter ID')
    ])
    id_proof = FileField('ID Proof File', validators=[Optional(), FileAllowed(['jpg', 'png', 'pdf', 'jpeg'])])
    submit = SubmitField('Save Customer')

# ---------- Service Provider Form ----------
class ServiceProviderForm(FlaskForm):
    name = StringField('Provider Name', validators=[DataRequired()])
    is_active = BooleanField('Active (Enable)', default=True)
    api_url = StringField('API URL')
    api_username = StringField('API Username')
    api_password = PasswordField('API Password', validators=[Optional(), Length(min=6)])
    confirm_password = PasswordField('Confirm API Password', validators=[Optional(), EqualTo('api_password')])
    submit = SubmitField('Save Provider')

# ---------- Plan ----------
class PlanForm(FlaskForm):
    plan_code = StringField('Plan Code')
    service_provider_id = SelectField('Service Provider', coerce=int, validators=[Optional()])
    plan_type = StringField('Plan Type')
    name = StringField('Plan Name', validators=[DataRequired()])
    speed_mbps = IntegerField('Speed (Mbps)', validators=[DataRequired(), NumberRange(min=1)])
    price_monthly = DecimalField('Monthly Price (₹)', validators=[DataRequired(), NumberRange(min=0)])
    isp_amount = DecimalField('ISP Amount (₹)', validators=[Optional(), NumberRange(min=0)])
    validity_days = IntegerField('Validity (days)', default=30)
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Plan')

# ---------- Invoice ----------
class InvoiceForm(FlaskForm):
    customer_id = HiddenField('Customer ID', validators=[DataRequired()])
    customer_plan_id = SelectField('Plan (Caption)', coerce=int, validators=[Optional()])
    invoice_no = StringField('Invoice No', validators=[DataRequired()])
    issue_date = DateField('Issue Date', default=date.today)
    due_date = DateField('Due Date', default=date.today)
    total_amount = DecimalField('Total Amount (₹)', validators=[DataRequired(), NumberRange(min=0)])
    tax_amount = DecimalField('Tax Amount (₹)', default=0)
    
    discount_percent = DecimalField('Discount (%)', default=0.00)
    discount_amount = DecimalField('Discount Amount (₹)', default=0.00)
    receipt_number = StringField('Receipt No')
    caption = StringField('Caption')
    invoice_type = SelectField('Invoice Type', choices=[
        ('plan', 'Plan'), ('addon', 'Addon'), ('discount', 'Discount'), ('other', 'Other')
    ], default='plan')
    remarks = TextAreaField('Remarks', render_kw={"rows": 2})

    status = SelectField('Status', choices=[
        ('draft', 'Draft'), ('sent', 'Sent'), ('paid', 'Paid'),
        ('overdue', 'Overdue'), ('cancelled', 'Cancelled')
    ])
    submit = SubmitField('Save Invoice')

# ---------- Payment ----------
class PaymentForm(FlaskForm):
    invoice_id = HiddenField('Invoice ID')
    customer_id = HiddenField('Customer ID')
    amount = DecimalField('Amount (₹)', validators=[DataRequired(), NumberRange(min=0.01)])
    payment_date = DateField('Payment Date', default=date.today)
    payment_mode = SelectField('Payment Mode', choices=PAYMENT_MODE_CHOICES, validators=[DataRequired()])
    mode_detail = StringField('Mode Detail')
    status = SelectField('Status', choices=[
        ('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')
    ], default='approved')
    
    book_receipt_no = StringField('Book Receipt No')
    remarks = TextAreaField('Remarks', render_kw={"rows": 2})
    
    submit = SubmitField('Record Payment')

# ---------- Staff ----------
class StaffForm(FlaskForm):
    full_name = StringField('Full Name', validators=[DataRequired()])
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[Optional(), Length(min=6)])
    email = StringField('Email', validators=[Optional(), Email()])
    mobile = StringField('Mobile')
    role = SelectField('Role', choices=[
        ('admin', 'Admin'), ('support', 'Support'),
        ('field', 'Field Engineer'), ('accounts', 'Accounts')
    ])
    staff_type_id = SelectField('Staff Type', coerce=int, validators=[DataRequired()])
    monthly_salary = DecimalField('Monthly Salary (₹)', validators=[Optional(), NumberRange(min=0)])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Staff')

# ---------- HR & Payroll ----------
class AttendanceForm(FlaskForm):
    user_id = SelectField('Staff', coerce=int, validators=[DataRequired()])
    date = DateField('Date', default=date.today)
    status = SelectField('Status', choices=[
        ('present', 'Present'), ('absent', 'Absent'), ('half-day', 'Half Day')
    ])
    submit = SubmitField('Record Attendance')

class LeaveForm(FlaskForm):
    user_id = SelectField('Staff', coerce=int, validators=[DataRequired()])
    start_date = DateField('Start Date', validators=[DataRequired()])
    end_date = DateField('End Date', validators=[DataRequired()])
    reason = TextAreaField('Reason')
    status = SelectField('Status', choices=[
        ('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')
    ], default='pending')
    submit = SubmitField('Submit Leave')

class PayrollForm(FlaskForm):
    user_id = SelectField('Staff', coerce=int, validators=[DataRequired()])
    month_year = DateField('Month/Year', default=date.today)
    salary = DecimalField('Salary (₹)', validators=[DataRequired(), NumberRange(min=0)])
    paid = BooleanField('Paid')
    submit = SubmitField('Save Payroll')

# ---------- Expenses ----------
class ExpenseForm(FlaskForm):
    category_id = SelectField('Category', coerce=int, validators=[DataRequired()])
    account_id = SelectField('Account', coerce=int, validators=[DataRequired()])
    payee_id = SelectField('Payee', coerce=int, validators=[DataRequired()])
    amount = DecimalField('Amount (₹)', validators=[DataRequired(), NumberRange(min=0)])
    expense_date = DateField('Expense Date', default=date.today)
    description = TextAreaField('Description')
    prepared_by_id = SelectField('Prepared By', coerce=int, validators=[Optional()])
    passed_by_id = SelectField('Passed By', coerce=int, validators=[Optional()])
    status = SelectField('Status', choices=[
        ('draft', 'Draft'),
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected')
    ], default='pending')
    submit = SubmitField('Save Expense')

class ExpenseCategoryForm(FlaskForm):
    name = StringField('Category Name', validators=[DataRequired()])
    submit = SubmitField('Add Category')

class ExpenseAccountForm(FlaskForm):
    name = StringField('Account Name', validators=[DataRequired()])
    submit = SubmitField('Add Account')

class ExpensePayeeForm(FlaskForm):
    name = StringField('Payee Name', validators=[DataRequired()])
    mobile = StringField('Mobile', validators=[Optional()])
    email = StringField('Email', validators=[Optional(), Email()])
    address = TextAreaField('Address', validators=[Optional()], render_kw={"rows": 2})
    submit = SubmitField('Add Payee')

# ---------- Inventory ----------
class VendorForm(FlaskForm):
    name = StringField('Vendor Name', validators=[DataRequired(), Length(max=100)])
    contact_person = StringField('Contact Person', validators=[Optional(), Length(max=100)])
    mobile = StringField('Mobile', validators=[Optional(), Length(max=20)])
    email = StringField('Email', validators=[Optional(), Email(), Length(max=120)])
    gstin = StringField('GSTIN', validators=[Optional(), Length(max=20)])
    address = TextAreaField('Address', validators=[Optional()])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Vendor')

class ProductForm(FlaskForm):
    name = StringField('Product Name', validators=[DataRequired(), Length(max=100)])
    vendor_id = SelectField('Vendor', coerce=int, validators=[Optional()])
    sku = StringField('SKU / Model', validators=[Optional(), Length(max=50)])
    hsn_code = StringField('HSN Code', validators=[Optional(), Length(max=20)])
    description = TextAreaField('Description')
    cost_price = DecimalField('Purchase Price from Vendor (\u20b9)',
                              default=0, validators=[Optional(), NumberRange(min=0)])
    unit_price = DecimalField('Selling Price to Customer (\u20b9)',
                              default=0, validators=[Optional(), NumberRange(min=0)])
    tax_percent = DecimalField('Tax %', default=0,
                               validators=[Optional(), NumberRange(min=0, max=100)])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Product')

class VendorBillForm(FlaskForm):
    vendor_id = SelectField('Vendor', coerce=int, validators=[DataRequired()])
    bill_date = DateField('Bill Date', default=date.today, validators=[DataRequired()])
    due_date = DateField('Due Date', validators=[Optional()])
    reference = StringField('Vendor Invoice / Reference No.',
                            validators=[Optional(), Length(max=100)])
    notes = TextAreaField('Notes', validators=[Optional()])
    submit = SubmitField('Save Bill')

class VendorBillPaymentForm(FlaskForm):
    amount = DecimalField('Amount Paid (\u20b9)',
                          validators=[DataRequired(), NumberRange(min=0.01)])
    submit = SubmitField('Record Payment')

class StockForm(FlaskForm):
    product_id = SelectField('Product', coerce=int, validators=[DataRequired()])
    quantity = IntegerField('Quantity', validators=[DataRequired(), NumberRange(min=0)])
    submit = SubmitField('Update Stock')

# ---------- Masters ----------
class AddressForm(FlaskForm):
    line1 = StringField('Address Line 1', validators=[DataRequired()])
    line2 = StringField('Address Line 2')
    city = StringField('City', validators=[DataRequired()])
    state = StringField('State', validators=[DataRequired()])
    pincode = StringField('Pincode', validators=[DataRequired()])
    submit = SubmitField('Save Address')

class CustomerPlanForm(FlaskForm):
    customer_id = SelectField('Customer', coerce=int, validators=[DataRequired()])
    plan_id = SelectField('Plan', coerce=int, validators=[DataRequired()])
    start_date = DateField('Start Date', default=date.today)
    end_date = DateField('End Date', validators=[DataRequired()])
    status = SelectField('Status', choices=[
        ('active', 'Active'), ('inactive', 'Inactive'), ('terminated', 'Terminated')
    ], default='active')
    auto_renew = BooleanField('Auto Renew', default=True)
    grace_period_days = IntegerField('Grace Period (days)', default=1)
    submit = SubmitField('Assign Plan')

class AddonInvoiceForm(FlaskForm):
    summary_invoice = StringField('Summary Invoice')
    detailed_invoice = StringField('Detailed Invoice')
    caption = StringField('Caption')
    invoice_date = DateField('Invoice Date', default=date.today)
    payment_mode = SelectField('Mode', choices=PAYMENT_MODE_CHOICES, validators=[Optional()])
    invoice_amount = DecimalField('Invoice Amount', default=0, validators=[Optional(), NumberRange(min=0)])
    discount_amount = DecimalField('Discount', default=0, validators=[Optional(), NumberRange(min=0)])
    book_receipt_no = StringField('Book Receipt No.')
    remark = StringField('Remark')
    submit = SubmitField('Submit')

class PlanDatesForm(FlaskForm):
    start_date = DateField('Renew Date', validators=[DataRequired()])
    end_date = DateField('Expiry Date', validators=[DataRequired()])
    status = SelectField('Status', choices=[
        ('active', 'Active'), ('expired', 'Expired'),
        ('cancelled', 'Cancelled'), ('terminated', 'Terminated')
    ], default='active')
    submit = SubmitField('Update Dates')

<<<<<<< HEAD
# =========================================================== #
#  NEW FORMS FOR CUSTOMER PORTAL & MESSAGE TEMPLATES          #
# =========================================================== #

class MessageTemplateForm(FlaskForm):
    """Form for creating/editing WhatsApp/SMS templates."""
=======
# ---------- Message Template Forms ----------
class MessageTemplateForm(FlaskForm):
>>>>>>> dc70a1ede676b9cb650b3df45c549cd06fe7535e
    name = StringField('Template Name', validators=[DataRequired(), Length(max=100)])
    template_type = SelectField('Template Type', choices=[
        ('renewal', 'Plan Renewed'),
        ('payment_received', 'Payment Received'),
        ('expiry_3d', 'Expiry Reminder (3 Days)'),
        ('expiry_2d', 'Expiry Reminder (2 Days)'),
        ('expired', 'Plan Expired')
    ], validators=[DataRequired()])
    body = TextAreaField('Message Body', validators=[DataRequired()], 
                         render_kw={"rows": 6, "placeholder": "Use {{customer_name}}, {{username}}, {{amount}}, {{days}}, {{balance}}, {{paid_amount}} as placeholders."})
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Template')

class BulkMessageForm(FlaskForm):
<<<<<<< HEAD
    """Form for sending bulk expiry messages to customers within a date range."""
=======
>>>>>>> dc70a1ede676b9cb650b3df45c549cd06fe7535e
    template_id = SelectField('Select Template', coerce=int, validators=[DataRequired()])
    start_date = DateField('Start Date', validators=[DataRequired()])
    end_date = DateField('End Date', validators=[DataRequired()])
>>>>>>> eaddab7a9b6609413ac527248b3d5a68cc7057f5
    submit = SubmitField('Send Bulk Message')