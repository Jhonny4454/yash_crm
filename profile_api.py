"""
profile_api.py
==============

Where the time actually goes, per endpoint.

    python profile_api.py                 # against a seeded throwaway database
    python profile_api.py --live          # against DATABASE_URL, read-only

Prints, for every screen the admin panel and the portal load, how long the
request took and HOW MANY SQL QUERIES it issued.

Why the query count matters more than the milliseconds
------------------------------------------------------
On a laptop with MySQL on the same machine a query costs well under a
millisecond, so an endpoint issuing 300 of them still feels instant. Move that
database to Railway and every one of those 300 becomes a network round trip -
tens of milliseconds each - and the same screen takes ten seconds. The count
is the thing that predicts how the system behaves in production; the local
timing does not.

An endpoint whose query count grows with the number of rows on the page is the
signature of an N+1: one query for the list, then one more per row. Those are
flagged, because they get worse every month the business grows.
"""
import argparse
import os
import tempfile
from pathlib import Path
import statistics
import sys
import time
from datetime import date, timedelta

WARM_UP = 1
RUNS = 3

#: More than this many queries for one screen is worth a look; the dashboard
#: needs a handful, a list needs one plus a count.
BUSY = 25


def seed(db, models, customers=120):
    """A database with enough in it for an N+1 to show up."""
    Customer = models['Customer']
    Invoice = models['Invoice']
    Payment = models['Payment']
    Plan = models['Plan']
    CustomerPlan = models['CustomerPlan']
    Company = models['Company']
    User = models['User']

    db.drop_all()
    db.create_all()
    db.session.add(Company(name='YASH Internet Services'))

    admin = User(username='admin', full_name='Admin', role='admin', is_active=True)
    admin.set_password('x') if hasattr(admin, 'set_password') else setattr(
        admin, 'password_hash', 'x')
    db.session.add(admin)

    plans = [Plan(name=f'Fibre {speed}', speed_mbps=speed, validity_days=30,
                  price_monthly=300 + speed, is_active=True)
             for speed in (30, 50, 100, 200)]
    db.session.add_all(plans)
    db.session.flush()

    today = date.today()
    for n in range(1, customers + 1):
        cust = Customer(first_name=f'Customer{n}', last_name='Test',
                        mobile=f'98765{n:05d}', username=f'user{n}',
                        is_active=n % 9 != 0, zone=['North', 'South', 'East'][n % 3],
                        registration_date=today - timedelta(days=n))
        db.session.add(cust)
        db.session.flush()

        plan = plans[n % len(plans)]
        db.session.add(CustomerPlan(
            customer_id=cust.id, plan_id=plan.id,
            start_date=today - timedelta(days=20),
            end_date=today + timedelta(days=(n % 40) - 10),
            status='active', last_invoice_date=today - timedelta(days=20)))

        # Most customers have a few bills; some are unpaid, some part paid.
        for k in range(3):
            inv = Invoice(customer_id=cust.id, invoice_no=f'INV-{n:04d}-{k}',
                          issue_date=today - timedelta(days=30 * k + 1),
                          due_date=today - timedelta(days=30 * k - 14),
                          total_amount=plan.price_monthly, tax_amount=0,
                          status='paid' if k else 'sent', invoice_type='plan')
            db.session.add(inv)
            db.session.flush()
            if k:
                db.session.add(Payment(
                    invoice_id=inv.id, customer_id=cust.id,
                    amount=plan.price_monthly, payment_date=inv.issue_date,
                    payment_mode='Cash', status='approved', source='counter'))

    db.session.commit()
    return admin.id


def profile(app, client, headers, path, label=None):
    """(ms, queries) for one endpoint, best of RUNS."""
    from sqlalchemy import event
    from models import db

    counter = {'n': 0}

    def count(*_args, **_kwargs):
        counter['n'] += 1

    event.listen(db.engine, 'before_cursor_execute', count)
    try:
        for _ in range(WARM_UP):
            client.get(path, headers=headers)

        timings, counts = [], []
        for _ in range(RUNS):
            counter['n'] = 0
            start = time.perf_counter()
            response = client.get(path, headers=headers)
            timings.append((time.perf_counter() - start) * 1000)
            counts.append(counter['n'])
    finally:
        event.remove(db.engine, 'before_cursor_execute', count)

    return (statistics.median(timings), max(counts), response.status_code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true',
                        help='profile against DATABASE_URL instead of a '
                             'throwaway seeded database (read-only)')
    parser.add_argument('--customers', type=int, default=120)
    args = parser.parse_args()

    if not args.live:
        # ``/tmp`` is a POSIX convention.  The project is also developed on
        # Windows, where that literal points at a non-existent drive path and
        # made the profiler fail before it sent a single request.
        temp_db = Path(tempfile.gettempdir()) / 'profile_api.db'
        os.environ['DATABASE_URL'] = 'sqlite:///' + temp_db.as_posix()
    os.environ.setdefault('STRICT_SECRETS', '0')

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app import app
    from models import (Company, Customer, CustomerPlan, Invoice, Payment,
                        Plan, User, db)

    with app.app_context():
        if args.live:
            admin = User.query.filter_by(role='admin').first()
            if admin is None:
                print('No admin user in the live database.')
                return 1
            admin_id = admin.id
            customer = Customer.query.first()
        else:
            admin_id = seed(db, {'Customer': Customer, 'Invoice': Invoice,
                                 'Payment': Payment, 'Plan': Plan,
                                 'CustomerPlan': CustomerPlan,
                                 'Company': Company, 'User': User},
                            customers=args.customers)
            customer = Customer.query.first()

        customer_id = customer.id if customer else 1
        from blueprints.api.utils import make_token
        staff = make_token(admin_id, 'staff', 'admin')
        portal = make_token(customer_id, 'customer', 'customer')

    app.config['PROPAGATE_EXCEPTIONS'] = False
    client = app.test_client()

    screens = [
        ('staff', 'Dashboard',          '/api/v1/dashboard/summary'),
        ('staff', 'Customers list',     '/api/v1/customers?per_page=25'),
        ('staff', 'Customer profile',   f'/api/v1/customers/{customer_id}'),
        ('staff', 'Invoices',           '/api/v1/invoices?per_page=25'),
        ('staff', 'Payments',           '/api/v1/payments?per_page=25'),
        ('staff', 'Renewal queue',      '/api/v1/renewals?per_page=25'),
        ('staff', 'Plan expiry board',  '/api/v1/reports/plan-expiry'),
        ('staff', 'Authorisations',     '/api/v1/payments?status=pending&per_page=25'),
        ('staff', 'Message templates',  '/api/v1/masters/message-templates'),
        ('staff', 'Branding',           '/api/v1/branding'),
        ('customer', 'Portal home',     '/api/v1/portal/dashboard'),
        ('customer', 'Portal invoices', '/api/v1/portal/invoices'),
        ('customer', 'Portal payments', '/api/v1/portal/payments'),
        ('customer', 'Portal plans',    '/api/v1/portal/plans'),
    ]

    print(f"{'':<3}{'screen':<20}{'ms':>9}{'queries':>10}{'':>4}status")
    print('-' * 62)

    rows = []
    with app.app_context():
        for audience, label, path in screens:
            headers = {'Authorization':
                       f'Bearer {staff if audience == "staff" else portal}'}
            try:
                ms, queries, status = profile(app, client, headers, path, label)
            except Exception as exc:
                print(f'   {label:<20}{"-":>9}{"-":>10}    {type(exc).__name__}')
                continue
            flag = '!!' if queries > BUSY else '  '
            print(f'{flag} {label:<20}{ms:>9.0f}{queries:>10}    {status}')
            rows.append((label, ms, queries, status))

    print('-' * 62)
    busy = [r for r in rows if r[2] > BUSY]
    if busy:
        worst = max(busy, key=lambda r: r[2])
        print(f'{len(busy)} screen(s) issue more than {BUSY} queries. '
              f'Worst: {worst[0]} at {worst[2]}.')
        print('On a database in another datacentre each query is a round trip,')
        print(f'so {worst[2]} queries is roughly {worst[2] * 0.03:.1f}s of pure latency.')
    else:
        print(f'No screen issues more than {BUSY} queries.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
