#!/usr/bin/env python3
"""
Self-contained smoke test for the YASH Internet Services CRM.

Seeds a throwaway copy of instance/app.db, then:
  1. crawls every admin page as a logged-in admin
  2. crawls the customer portal as a logged-in customer
  3. crawls /api/v1 with staff + customer JWTs
  4. runs the four nightly scheduler jobs
  5. parses every template and compiles every module

Never touches instance/app.db. Run:  python qa_smoketest.py
"""
import os, re, sys, shutil, warnings, traceback, py_compile

warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

SRC_DB = os.path.join(HERE, 'instance', 'app.db')
QA_DB = os.path.join(HERE, 'instance', 'qa_smoketest.db')
if not os.path.exists(SRC_DB):
    open(SRC_DB, 'a').close()
if os.path.exists(QA_DB):
    os.remove(QA_DB)
shutil.copy(SRC_DB, QA_DB)

os.environ['DATABASE_URL'] = 'sqlite:///' + QA_DB
os.environ['FLASK_ENV'] = 'development'
os.environ['RUN_SCHEDULER'] = '0'
os.environ.setdefault('WA_ENABLED', '0')

import app as A                                     # noqa: E402
from models import *                                # noqa: E402,F401,F403
from models_ext import *                            # noqa: E402,F401,F403
from models_api import *                            # noqa: E402,F401,F403
from datetime import date, datetime, timedelta      # noqa: E402
from decimal import Decimal                          # noqa: E402

app = A.app
app.config['WTF_CSRF_ENABLED'] = False
PROBLEMS = []


def fail(section, msg):
    PROBLEMS.append(f'{section}: {msg}')
    print(f'   FAIL  {msg}')


# --------------------------------------------------------------------------- #
#  Seed
# --------------------------------------------------------------------------- #
def mk(Model, **kw):
    cols = {c.key for c in Model.__mapper__.column_attrs}
    o = Model(**{k: v for k, v in kw.items() if k in cols})
    db.session.add(o)
    return o


print('\n[1/7] seeding throwaway database')
with app.app_context():
    db.create_all()
    from services.schema_sync import sync_schema
    _changes = sync_schema(db)
    if _changes['added_columns']:
        print('        schema sync added ' + str(len(_changes['added_columns'])) + ' column(s)')
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(username='admin', full_name='Administrator',
                     email='admin@yash.com', role='admin', is_active=True)
        db.session.add(admin)
    admin.role = 'admin'
    admin.set_password('qa-admin-pw')
    db.session.commit()

    st = mk(StaffType, name='QA Field Engineer'); db.session.flush()
    staff = User(username='qa_staff', full_name='QA Staff', email='qa@yash.com',
                 mobile='9000000001', role='support', is_active=True,
                 staff_type_id=st.id, monthly_salary=25000)
    staff.set_password('qa-staff-pw'); db.session.add(staff)

    mk(Company, name='YASH Internet Services', mobile='9029508777',
       email='info@yash.com', address='Andheri West, Mumbai')
    mk(Zone, name='QA Zone', code='QAZ', city='Mumbai', state='Maharashtra')
    mk(Locality, name='QA Locality'); mk(Area, name='QA Area')
    mk(Building, name='QA Building')
    mk(TaxMaster, name='QA GST 18%', value=Decimal('18.00'))
    sp = mk(ServiceProvider, name='QA Provider', is_active=True)
    mk(Address, name='QA Office', address_line='MG Road', city='Mumbai',
       state='MH', pincode='400058')
    mk(AddonCategory, name='QA Installation', default_price=Decimal('500'))
    db.session.flush()

    plan = mk(Plan, name='QA Fiber 100', plan_code='QAF100', speed_mbps=100,
              price_monthly=Decimal('600'), validity_days=30,
              service_provider_id=sp.id, isp_amount=Decimal('300'),
              plan_type='Prepaid', is_active=True)
    db.session.flush()

    today = date.today()
    custs = []
    for i in range(1, 4):
        c = Customer(first_name=f'QACust{i}', last_name='Test',
                     mobile=f'9000090{i:03d}', email=f'qacust{i}@example.com',
                     username=f'qacust{i}@yn', reference_id=f'QA{i:04d}',
                     zone='QA Zone', locality='QA Locality', area='QA Area',
                     building='QA Building', flat_no=f'{i}01',
                     billing_address='QA address', primary_address='QA address',
                     registration_date=today - timedelta(days=120), is_active=True)
        c.set_password('qa-cust-pw'); db.session.add(c); custs.append(c)
    db.session.flush()

    for i, c in enumerate(custs):
        offset = [20, 3, -5][i]
        cp = mk(CustomerPlan, customer_id=c.id, plan_id=plan.id,
                start_date=today - timedelta(days=25),
                end_date=today + timedelta(days=offset),
                status='active' if offset >= 0 else 'expired',
                auto_renew=True, grace_period_days=1)
        db.session.flush()
        for k in range(2):
            inv = mk(Invoice, customer_id=c.id, customer_plan_id=cp.id,
                     invoice_no=f'QAINV{i+1}{k+1}',
                     issue_date=today - timedelta(days=30 * (k + 1)),
                     due_date=today - timedelta(days=30 * (k + 1) - 7),
                     total_amount=Decimal('708'), tax_amount=Decimal('108'),
                     status='paid' if k == 0 else 'sent',
                     invoice_type='plan', caption='Monthly Plan')
            db.session.flush()
            mk(InvoiceItem, invoice_id=inv.id, description='QA Fiber 100',
               item_type='plan', quantity=1, unit_price=Decimal('600'),
               tax_percent=Decimal('18'))
            mk(Payment, invoice_id=inv.id, customer_id=c.id,
               amount=Decimal('708' if k == 0 else '300'),
               payment_date=today - timedelta(days=5),
               payment_mode='Cash' if k == 0 else 'UPI',
               status='approved' if k == 0 else 'pending',
               source='admin' if k == 0 else 'portal',
               received_by_user_id=admin.id,
               authorized_at=datetime.utcnow() if k == 0 else None)
        mk(ServiceRequest, customer_id=c.id, ticket_id=f'QATKT{i+1}',
           subject='QA ticket', description='QA description', status='open')

    ec = mk(ExpenseCategory, name='QA Utilities')
    ea = mk(ExpenseAccount, name='QA Bank')
    ep = mk(ExpensePayee, name='QA Payee'); db.session.flush()
    mk(Expense, category_id=ec.id, account_id=ea.id, payee_id=ep.id,
       amount=Decimal('1500'), expense_date=today, description='QA expense',
       status='approved', prepared_by_id=admin.id)
    ven = mk(Vendor, name='QA Vendor', is_active=True); db.session.flush()
    prod = mk(Product, name='QA Router', sku='QA-ONU', unit_price=Decimal('1200'),
              vendor_id=ven.id, is_active=True); db.session.flush()
    mk(Stock, product_id=prod.id, quantity=25)
    vb = mk(VendorBill, vendor_id=ven.id, bill_no='QA-VB-1', bill_date=today,
            total_amount=Decimal('12000'), status='pending'); db.session.flush()
    mk(VendorBillItem, bill_id=vb.id, product_id=prod.id, description='QA Router',
       quantity=10, unit_cost=Decimal('1200'))
    mk(InventoryAssignment, product_id=prod.id, customer_id=custs[0].id,
       serial_number='QA-SN-1', assigned_date=today, status='Active')
    mk(Attendance, user_id=staff.id, date=today, status='present')
    mk(Leave, user_id=staff.id, start_date=today + timedelta(days=2),
       end_date=today + timedelta(days=3), reason='QA', status='pending')
    mk(Payroll, user_id=staff.id, month_year=today.replace(day=1),
       salary=Decimal('25000'), paid=True)
    camp = mk(ReferralCampaign, name='QA Campaign', code='QAREF',
              reward_type='fixed', referrer_reward=Decimal('100'), is_active=True)
    db.session.flush()
    mk(Referral, campaign_id=camp.id, referrer_customer_id=custs[0].id,
       referee_name='QA Referee', referee_mobile='9000097777', status='pending')
    cred = mk(ISPCredential, service_provider_id=sp.id, driver='log2space',
              label='QA', username='qa', base_url='https://example.invalid',
              is_active=True)
    db.session.flush()
    mk(ISPSyncLog, credential_id=cred.id, customer_id=custs[0].id,
       action='enable', http_status=200, success=True, duration_ms=100)
    mk(BackupLog, filename='qa.sql', size_bytes=1024, status='success',
       created_by_id=admin.id)
    mk(ImportJob, target='customers', filename='qa.csv', status='done',
       created_by_id=admin.id)
    mk(MessageLog, customer_id=custs[0].id, channel='whatsapp',
       phone='9000090001', body='QA message', status='sent')
    mk(Notification, customer_id=custs[0].id, title='QA', body='QA',
       channel='push', status='sent')
    mk(DeviceToken, customer_id=custs[0].id, token='qa-token', platform='android')
    db.session.flush()
    # A pending portal renewal + payment entry, so the admin queue and the
    # portal history screens render with real rows.
    qa_inv = Invoice.query.filter_by(customer_id=custs[0].id).first()
    rr = mk(RenewalRequest, customer_id=custs[0].id,
            customer_plan_id=CustomerPlan.query.filter_by(
                customer_id=custs[0].id).first().id,
            current_plan_id=plan.id, requested_plan_id=plan.id,
            months=3, days=90, amount=Decimal('1800'),
            invoice_id=qa_inv.id if qa_inv else None,
            kind='renew', status='pending')
    mk(Payment, invoice_id=qa_inv.id if qa_inv else None,
       customer_id=custs[0].id, amount=Decimal('1800'),
       payment_date=today, payment_mode='UPI', status='pending',
       source='portal', utr='QA000111222333', mode_detail='Customer entry - UPI')
    db.session.commit()

    IDS = {'customer_id': custs[0].id, 'plan_id': plan.id,
           'invoice_id': Invoice.query.first().id,
           'payment_id': Payment.query.first().id,
           'user_id': staff.id, 'staff_id': staff.id,
           'expense_id': Expense.query.first().id,
           'category_id': ec.id, 'account_id': ea.id, 'payee_id': ep.id,
           'vendor_id': ven.id, 'product_id': prod.id,
           'stock_id': Stock.query.first().id, 'bill_id': vb.id,
           'zone_id': Zone.query.first().id, 'tax_id': TaxMaster.query.first().id,
           'locality_id': Locality.query.first().id,
           'area_id': Area.query.first().id,
           'building_id': Building.query.first().id,
           'address_id': Address.query.first().id,
           'company_id': Company.query.first().id,
           'provider_id': sp.id, 'sp_id': sp.id, 'type_id': st.id,
           'cp_id': CustomerPlan.query.first().id,
           'customer_plan_id': CustomerPlan.query.first().id,
           'attendance_id': Attendance.query.first().id,
           'leave_id': Leave.query.first().id,
           'payroll_id': Payroll.query.first().id,
           'template_id': MessageTemplate.query.first().id,
           'cred_id': cred.id, 'credential_id': cred.id,
           'campaign_id': camp.id, 'referral_id': Referral.query.first().id,
           'ticket_id': ServiceRequest.query.first().id,
           'request_id': ServiceRequest.query.first().id,
           'sr_id': ServiceRequest.query.first().id,
           'assignment_id': InventoryAssignment.query.first().id,
           'item_id': InvoiceItem.query.first().id,
           'notification_id': Notification.query.first().id,
           'addon_id': AddonCategory.query.first().id,
           'log_id': MessageLog.query.first().id,
           'job_id': ImportJob.query.first().id,
           'backup_id': BackupLog.query.first().id,
           'return_id': 1, 'order_id': 'QA-ORDER', 'id': 1,
           'renewal_id': RenewalRequest.query.first().id}
    CUST_USER = custs[0].username
print('        seeded.')

SKIP = re.compile(r'(logout|delete|remove|destroy|/send|send_|sync|backup/run|'
                  r'restore|purge|reset|webhook|callback|shutdown|/test)', re.I)


def fill(rule):
    p = rule.rule
    for a in rule.arguments:
        p = re.sub(r'<[^<>:]*:?' + re.escape(a) + r'>', str(IDS.get(a, 1)), p)
    return None if '<' in p else p


def crawl(client, predicate, label, login_path):
    ok = 0
    for rule in sorted(app.url_map.iter_rules(), key=lambda x: x.rule):
        if 'GET' not in rule.methods or rule.endpoint == 'static':
            continue
        if not predicate(rule) or SKIP.search(rule.rule):
            continue
        p = fill(rule)
        if not p:
            continue
        try:
            r = client.get(p, follow_redirects=True)
            body = r.get_data(as_text=True)
        except Exception:
            fail(label, f'{p} raised\n{traceback.format_exc()[-600:]}')
            continue
        if r.status_code >= 500 or 'Internal Server Error' in body:
            fail(label, f'{p} -> HTTP {r.status_code}')
        elif login_path and login_path in r.request.path and login_path not in rule.rule:
            fail(label, f'{p} bounced to login')
        else:
            ok += 1
    print(f'        {ok} pages OK')


print('\n[2/7] crawling admin pages')
c = app.test_client()
c.post('/login', data={'username': 'admin', 'password': 'qa-admin-pw'},
       follow_redirects=True)
crawl(c, lambda r: not r.rule.startswith(('/customer', '/api')), 'admin', '/login')

print('\n[3/7] crawling customer portal')
cc = app.test_client()
cc.post('/customer/login', data={'username': CUST_USER, 'password': 'qa-cust-pw'},
        follow_redirects=True)
crawl(cc, lambda r: r.rule.startswith('/customer'), 'portal', '/customer/login')

print('\n[4/7] crawling REST API')
api = app.test_client()


def _tok(resp):
    try:
        return resp.get_json()['data']['access_token']
    except Exception:
        return None


staff_tok = _tok(api.post('/api/v1/auth/staff/login',
                          json={'username': 'admin', 'password': 'qa-admin-pw'}))
cust_tok = _tok(api.post('/api/v1/auth/customer/login',
                         json={'username': CUST_USER, 'password': 'qa-cust-pw'}))
if not staff_tok:
    fail('api', 'staff JWT login failed')
if not cust_tok:
    fail('api', 'customer JWT login failed')
n_api = 0
for rule in sorted(app.url_map.iter_rules(), key=lambda x: x.rule):
    if 'GET' not in rule.methods or not rule.rule.startswith('/api/v1'):
        continue
    if SKIP.search(rule.rule):
        continue
    p = fill(rule)
    tok = cust_tok if ('/portal' in p or '/auth/customer' in p) else staff_tok
    if not p or not tok:
        continue
    r = api.get(p, headers={'Authorization': 'Bearer ' + tok})
    if r.status_code >= 500:
        fail('api', f'{p} -> HTTP {r.status_code}')
    else:
        n_api += 1
print(f'        {n_api} endpoints OK')

# Exercise newly exposed SPA write flows without depending on the legacy HTML
# forms: provider CRUD and customer OTP reset request both need to stay live.
_provider = api.post('/api/v1/service-providers', json={
    'name': 'QA React Provider', 'is_active': True,
}, headers={'Authorization': 'Bearer ' + staff_tok})
if _provider.status_code != 201:
    fail('api-write', f'service provider create -> HTTP {_provider.status_code}')
else:
    _provider_id = _provider.get_json()['data']['id']
    _updated = api.put(f'/api/v1/service-providers/{_provider_id}', json={
        'name': 'QA React Provider Updated', 'is_active': False,
    }, headers={'Authorization': 'Bearer ' + staff_tok})
    _deleted = api.delete(f'/api/v1/service-providers/{_provider_id}',
                          headers={'Authorization': 'Bearer ' + staff_tok})
    if _updated.status_code != 200 or _deleted.status_code != 200:
        fail('api-write', 'service provider update/delete failed')

_otp = api.post('/api/v1/auth/customer/forgot-password',
                json={'identifier': CUST_USER})
if _otp.status_code != 200:
    fail('api-write', f'customer password-reset request -> HTTP {_otp.status_code}')
else:
    print('        SPA write and password-recovery endpoints OK')

# Tokens must honour the account's current state, not only the role/status
# embedded at sign-in.  This makes a staff demotion or customer deactivation
# effective immediately instead of waiting for the JWT to expire.
with app.app_context():
    _api_admin = User.query.filter_by(username='admin').first()
    _api_customer = db.session.get(Customer, IDS['customer_id'])
    _admin_role = _api_admin.role
    _customer_active = _api_customer.is_active
    _api_admin.role = 'support'
    _api_customer.is_active = False
    db.session.commit()
try:
    _r = api.post('/api/v1/plans', json={},
                  headers={'Authorization': 'Bearer ' + staff_tok})
    if _r.status_code != 403:
        fail('api-auth', 'demoted admin token retained admin access')
    _r = api.get('/api/v1/auth/customer/me',
                 headers={'Authorization': 'Bearer ' + cust_tok})
    if _r.status_code != 403:
        fail('api-auth', 'disabled customer token retained API access')
    if _r.status_code == 403:
        print('        live account/role checks OK')
finally:
    with app.app_context():
        _api_admin = User.query.filter_by(username='admin').first()
        _api_customer = db.session.get(Customer, IDS['customer_id'])
        _api_admin.role = _admin_role
        _api_customer.is_active = _customer_active
        db.session.commit()

print('\n[5/7] running nightly scheduler jobs')
for name in ('generate_auto_invoices', 'auto_suspend_overdue',
             'send_grace_period_reminders', 'send_expiry_reminders'):
    fn = getattr(A, name, None)
    if fn is None:
        fail('jobs', f'{name} is missing')
        continue
    try:
        fn()
        print(f'        {name} OK')
    except Exception:
        fail('jobs', f'{name} raised\n{traceback.format_exc()[-900:]}')

print('\n[6/7] portal renewal + payment-entry flow')
_before = None
with app.app_context():
    # Step 5 ran auto_suspend_overdue, which correctly disables customers with
    # unpaid bills. Re-enable ours so the portal login works for this check.
    for _c in Customer.query.all():
        _c.is_active = True
    db.session.commit()
    _cp = CustomerPlan.query.filter_by(customer_id=IDS['customer_id']).first()
    _before = _cp.end_date
    _plan_id = Plan.query.first().id

_flow = app.test_client()
_flow.post('/customer/login', data={'username': CUST_USER, 'password': 'qa-cust-pw'},
           follow_redirects=True)

# The seeded pending renewal blocks a new one, so clear it first.
with app.app_context():
    for _r in RenewalRequest.query.filter_by(customer_id=IDS['customer_id'],
                                             status='pending').all():
        _r.status = 'cancelled'
    db.session.commit()

_r = _flow.post('/customer/renew/confirm',
                data={'plan_id': str(_plan_id), 'months': '3'},
                follow_redirects=True)
if _r.status_code >= 400:
    fail('renew', f'renew/confirm -> HTTP {_r.status_code}')

with app.app_context():
    _req = (RenewalRequest.query
            .filter_by(customer_id=IDS['customer_id'], status='pending')
            .order_by(RenewalRequest.id.desc()).first())
    _reqid = _req.id if _req else None
    _invid = _req.invoice_id if _req else None
    _end_after_request = CustomerPlan.query.get(_cp.id).end_date

if _req is None:
    fail('renew', 'renewal request was not created')
elif _end_after_request != _before:
    fail('renew', 'the plan moved before anyone approved the renewal')
else:
    print('        renewal request raised, plan correctly untouched')

_r = _flow.post('/customer/payments/new',
                data={'invoice_id': str(_invid), 'amount': '10',
                      'payment_mode': 'UPI', 'utr': 'QA-SMOKE-9911',
                      'payment_date': date.today().isoformat()},
                follow_redirects=True)
with app.app_context():
    _pay = Payment.query.filter_by(utr='QA-SMOKE-9911').first()
    _payid = _pay.id if _pay else None
    _paystatus = _pay.status if _pay else None
if _pay is None:
    fail('payment-entry', 'customer payment entry was not created')
elif _paystatus != 'pending':
    fail('payment-entry', f'entry should be pending, got {_paystatus}')
else:
    print('        payment entry recorded as pending (nothing credited)')

_admin = app.test_client()
_admin.post('/login', data={'username': 'admin', 'password': 'qa-admin-pw'},
            follow_redirects=True)
_r = _admin.get('/admin/portal-activity')
if _r.status_code != 200 or 'QA-SMOKE-9911' not in _r.get_data(as_text=True):
    fail('admin-queue', 'the payment entry does not show in the portal queue')
else:
    print('        entry visible in the admin queue')

_r = _admin.get('/admin/utr-search?q=QA-SMOKE')
if _r.status_code != 200 or 'QA-SMOKE-9911' not in _r.get_data(as_text=True):
    fail('utr-search', 'UTR search did not find the entry')
else:
    print('        UTR search finds it')

if _reqid:
    _r = _admin.post(f'/admin/portal-activity/renewals/{_reqid}/approve',
                     follow_redirects=True)
    with app.app_context():
        _after = CustomerPlan.query.get(_cp.id).end_date
        _st = RenewalRequest.query.get(_reqid).status
    if _st != 'approved':
        fail('renew', f'renewal did not approve (status {_st})')
    elif _after <= _before:
        fail('renew', f'plan expiry did not move ({_before} -> {_after})')
    else:
        print(f'        admin approval extended the plan {_before} -> {_after}')

print('\n[7/7] static checks')
for root, dirs, files in os.walk(HERE):
    dirs[:] = [d for d in dirs if d not in ('__pycache__', 'node_modules',
                                            '.git', 'frontend', 'backups')]
    for f in files:
        if f.endswith('.py'):
            src = os.path.join(root, f)
            try:
                with open(src, encoding='utf-8') as fh:
                    compile(fh.read(), src, 'exec')
            except SyntaxError as e:
                fail('syntax', f'{f}: {e}')
n_tpl = 0
for root, _, files in os.walk(os.path.join(HERE, 'templates')):
    for f in files:
        if not f.endswith('.html'):
            continue
        rel = os.path.relpath(os.path.join(root, f),
                              os.path.join(HERE, 'templates')).replace(os.sep, '/')
        try:
            app.jinja_env.get_template(rel)
            n_tpl += 1
        except Exception as e:
            fail('jinja', f'{rel}: {e}')
endpoints = {r.endpoint for r in app.url_map.iter_rules()}
missing = {}
for root, _, files in os.walk(os.path.join(HERE, 'templates')):
    for f in files:
        if not f.endswith('.html'):
            continue
        p = os.path.join(root, f)
        txt = open(p, encoding='utf-8', errors='replace').read()
        for ep in set(re.findall(r"""url_for\(\s*['"]([A-Za-z0-9_.]+)['"]""", txt)):
            if ep != 'static' and ep not in endpoints:
                missing.setdefault(ep, set()).add(os.path.basename(p))
for ep, where in missing.items():
    fail('url_for', f"{ep} (referenced by {', '.join(sorted(where))})")
print(f'        {n_tpl} templates parsed, {len(missing)} broken url_for targets')

try:
    os.remove(QA_DB)
except OSError:
    pass

print('\n' + '=' * 60)
if PROBLEMS:
    print(f'{len(PROBLEMS)} PROBLEM(S):')
    for p in PROBLEMS:
        print('  -', p)
    sys.exit(1)
print('ALL CHECKS PASSED')
