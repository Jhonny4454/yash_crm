"""Migrate customers and packages from CSV files into the CRM database.

Usage:
    python migrate_csv_import.py

Reads:
    customers (2).csv  →  Customer, User, CustomerPlan, UsernameReservation
    packages (1).csv   →  Plan

Connects to the live Railway MySQL database via DATABASE_URL from .env.
Run with --dry-run to preview without writing.
"""
import csv
import io
import os
import re
import secrets
import sys
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from werkzeug.security import generate_password_hash

# ── Load DATABASE_URL from .env ──────────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(_env_path):
    for line in open(_env_path):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

db_url = os.environ.get('DATABASE_URL', '')
if not db_url:
    sys.exit('Set DATABASE_URL in .env first.')

if db_url.startswith('mysql://'):
    db_url = db_url.replace('mysql://', 'mysql+pymysql://', 1)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import pymysql
engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=300,
                       connect_args={'connect_timeout': 10, 'read_timeout': 30, 'write_timeout': 30})
Session = sessionmaker(bind=engine)

# ── File paths ───────────────────────────────────────────────────────────────
BASE = os.path.dirname(__file__)
DOWNLOADS = os.path.expanduser(r'~\Downloads')
PACKAGES_CSV = os.path.join(DOWNLOADS, 'packages (1).csv')
CUSTOMERS_CSV = os.path.join(DOWNLOADS, 'customers (2).csv')

DRY_RUN = '--dry-run' in sys.argv

import sys as _sys
print(f'Database: {db_url.split("@")[-1] if "@" in db_url else db_url}', flush=True)
print(f'Dry run:  {DRY_RUN}', flush=True)
print(flush=True)
print('Testing DB connection...', flush=True)
try:
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1"))
        print(f'  DB OK: {r.fetchone()[0]}', flush=True)
except Exception as e:
    _sys.exit(f'  DB connection failed: {e}')
print(flush=True)


# ── Helpers ──────────────────────────────────────────────────────────────────
def parse_date(s):
    """Parse DD-MM-YYYY, YYYY-MM-DD, DD/MM/YYYY etc."""
    if not s or not s.strip():
        return None
    s = s.strip()
    for fmt in ('%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_decimal(s):
    if not s or not s.strip():
        return Decimal('0.00')
    s = s.strip().replace(',', '')
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal('0.00')


def parse_int(s):
    if not s or not s.strip():
        return 0
    try:
        return int(float(s.strip()))
    except ValueError:
        return 0


def extract_speed(name):
    """Extract speed in Mbps from package name like FR_100Mbps_30Days."""
    m = re.search(r'(\d+)\s*[Mm]bps', name)
    if m:
        return int(m.group(1))
    return 0


def parse_validity(locking_str):
    """Parse '30 Day', '365 Day', '545 Year' etc. into integer days."""
    if not locking_str:
        return 30
    locking_str = locking_str.strip()
    m = re.match(r'(\d+)\s*(Day|Month|Year|Days|Months|Years)', locking_str, re.I)
    if not m:
        return 30
    num = int(m.group(1))
    unit = m.group(2).lower()
    if 'year' in unit:
        return num * 365
    if 'month' in unit:
        return num * 30
    return num


def gen_password():
    return secrets.token_urlsafe(12)


# ── Phase 1: Import Packages → Plans ─────────────────────────────────────────
print('=' * 60)
print('PHASE 1: Import packages to Plans')
print('=' * 60)

session = Session()
try:
    # Ensure L2S service provider exists
    sp = session.execute(
        text("SELECT id FROM service_providers WHERE name = :n LIMIT 1"),
        {'n': 'L2S'}
    ).fetchone()
    if not sp:
        if DRY_RUN:
            print('[DRY RUN] Would create L2S service provider')
            l2s_id = 999
        else:
            session.execute(
                text("INSERT INTO service_providers (name, is_active, created_at) "
                     "VALUES (:n, 1, NOW())"),
                {'n': 'L2S'}
            )
            session.flush()
            l2s_id = session.execute(text("SELECT LAST_INSERT_ID()")).fetchone()[0]
            print(f'  Created L2S service provider (id={l2s_id})')
    else:
        l2s_id = sp[0]
        print(f'  L2S service provider exists (id={l2s_id})')

    # Read existing plans
    existing_plans = {}
    for row in session.execute(text("SELECT id, name, plan_code FROM plans")):
        existing_plans[row[1]] = row[0]
        if row[2]:
            existing_plans[row[2]] = row[0]
    print(f'  {len(existing_plans)} existing plan(s) found')

    # Read packages CSV
    with open(PACKAGES_CSV, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        packages = list(reader)

    print(f'  {len(packages)} package(s) in CSV')

    plans_created = plans_updated = 0
    plan_id_map = {}  # package_name → plan_id

    for pkg in packages:
        name = (pkg.get('Package Name') or '').strip()
        if not name:
            continue

        speed = extract_speed(name)
        price = parse_decimal(pkg.get('Package Price'))
        isp_price = parse_decimal(pkg.get('Lco Price'))
        plan_type = (pkg.get('Package Type') or '').strip()
        validity = parse_validity(pkg.get('Locking Period'))
        is_active = (pkg.get('Status') or '').strip().lower() == 'activated'

        # Look up by name or plan_code
        existing_id = existing_plans.get(name)

        if existing_id:
            plan_id_map[name] = existing_id
            if not DRY_RUN:
                session.execute(
                    text("UPDATE plans SET plan_type = :pt, price_monthly = :pm, "
                         "isp_amount = :ia, validity_days = :vd, speed_mbps = :sp, "
                         "is_active = :ia2, plan_code = :pc, service_provider_id = :sid "
                         "WHERE id = :id"),
                    {'pt': plan_type, 'pm': price, 'ia': isp_price,
                     'vd': validity, 'sp': speed, 'ia2': is_active,
                     'pc': name, 'sid': l2s_id, 'id': existing_id}
                )
            plans_updated += 1
        else:
            if not DRY_RUN:
                session.execute(
                    text("INSERT INTO plans (name, plan_code, plan_type, speed_mbps, "
                         "price_monthly, isp_amount, validity_days, is_active, "
                         "service_provider_id, created_at) "
                         "VALUES (:n, :pc, :pt, :sp, :pm, :ia, :vd, :ia2, :sid, NOW())"),
                    {'n': name, 'pc': name, 'pt': plan_type, 'sp': speed,
                     'pm': price, 'ia': isp_price, 'vd': validity,
                     'ia2': is_active, 'sid': l2s_id}
                )
                session.flush()
                new_id = session.execute(text("SELECT LAST_INSERT_ID()")).fetchone()[0]
                plan_id_map[name] = new_id
            else:
                plan_id_map[name] = f'NEW_{name}'
            plans_created += 1

    if not DRY_RUN:
        session.commit()

    print(f'  Plans created: {plans_created}, updated: {plans_updated}')
    print()

except Exception as e:
    session.rollback()
    print(f'  ERROR in Phase 1: {e}')
    raise
finally:
    session.close()


# ── Phase 2: Import Customers ───────────────────────────────────────────────
print('=' * 60)
print('PHASE 2: Import customers')
print('=' * 60)

session = Session()
try:
    # Read existing customers by mobile and reference_id
    existing_by_mobile = {}
    existing_by_ref = {}
    existing_by_username = {}
    for row in session.execute(
        text("SELECT id, mobile, reference_id, username FROM customers")
    ):
        if row[1]:
            existing_by_mobile[row[1]] = row[0]
        if row[2]:
            existing_by_ref[row[2]] = row[0]
        if row[3]:
            existing_by_username[row[3]] = row[0]

    print(f'  {len(existing_by_mobile)} existing customer(s) by mobile')
    print(f'  {len(existing_by_ref)} existing customer(s) by reference_id')

    # Read customer CSV
    with open(CUSTOMERS_CSV, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        customers = list(reader)

    print(f'  {len(customers)} customer(s) in CSV')

    # Cache existing usernames (case-insensitive)
    taken_usernames = set(k.lower() for k in existing_by_username)

    def unique_username(base):
        base = (base or '').strip()
        if not base:
            base = 'user_' + secrets.token_hex(4)
        candidate = base
        i = 2
        while candidate.lower() in taken_usernames:
            candidate = f'{base}{i}'
            i += 1
            if i > 200:
                candidate = f'{base}_{secrets.token_hex(3)}'
                break
        taken_usernames.add(candidate.lower())
        return candidate

    customers_created = customers_updated = 0
    users_created = 0
    plans_assigned = 0
    skipped = 0
    row_errors = 0
    for n, row in enumerate(customers, start=2):
        try:
            first = (row.get('First Name') or '').strip()
            last = (row.get('Last Name') or '').strip()
            mobile = (row.get('Mobile No.') or '').strip()
            ref_id = (row.get('Reference Id') or '').strip()
            username_raw = (row.get('Username') or '').strip()

            if not first or not last:
                skipped += 1
                continue

            # Determine customer status
            status = (row.get('Customer Status') or '').strip().lower()
            is_active = status == 'activated'

            # Address fields
            flat = (row.get('Flat No.') or '').strip()
            building = (row.get('Building') or '').strip()
            area = (row.get('Area') or '').strip()
            locality = (row.get('Locality') or '').strip()
            zone = (row.get('Zone Name') or '').strip()
            phone = (row.get('Phone No.') or '').strip()
            balance = parse_decimal(row.get('Balance'))
            tax_type = (row.get('Tax-Type') or 'Non-Taxable').strip()
            if tax_type == 'Non-Tax':
                tax_type = 'Non-Taxable'
            elif tax_type == 'Tax':
                tax_type = 'Taxable'

            conn_type = (row.get('Connection Type') or 'FTTH').strip()

            # Connection type must be valid enum
            valid_conn = {'Ethernet', 'FTTH', 'Lease Line'}
            if conn_type not in valid_conn:
                conn_type = 'FTTH'

            # Determine plan
            plan_name = (row.get('Plan Name') or '').strip()
            amount = parse_decimal(row.get('Amount'))
            start_date = parse_date(row.get('Start Date'))
            end_date = parse_date(row.get('End Date'))
            plan_status_raw = (row.get('Plan Status') or '').strip().lower()
            plan_status = 'active' if plan_status_raw == 'activated' else 'expired'

            # Check if customer exists (by reference_id, mobile, or account_id)
            account_id = (row.get('Account Id') or '').strip()
            cust_id = existing_by_ref.get(ref_id) or existing_by_mobile.get(mobile)

            if cust_id:
                # UPDATE existing customer
                if not DRY_RUN:
                    session.execute(
                        text("UPDATE customers SET first_name = :fn, last_name = :ln, "
                             "mobile = :mob, home_phone = :hp, reference_id = :rid, "
                             "flat_no = :flat, building = :bld, area = :area, "
                             "locality = :loc, zone = :zone, wallet_balance = :bal, "
                             "is_active = :ia, tax_type = :tt, connection_type = :ct, "
                             "updated_at = NOW() WHERE id = :id"),
                        {'fn': first, 'ln': last, 'mob': mobile or None,
                         'hp': phone or None, 'rid': ref_id or None,
                         'flat': flat or None, 'bld': building or None,
                         'area': area or None, 'loc': locality or None,
                         'zone': zone or None, 'bal': balance,
                         'ia': is_active, 'tt': tax_type, 'ct': conn_type,
                         'id': cust_id}
                    )
                customers_updated += 1
            else:
                # INSERT new customer
                username = unique_username(username_raw)
                pwd_hash = generate_password_hash(gen_password())

                if not DRY_RUN:
                    session.execute(
                        text("INSERT INTO customers (first_name, last_name, mobile, "
                             "home_phone, reference_id, username, password_hash, "
                             "flat_no, building, area, locality, zone, "
                             "wallet_balance, is_active, tax_type, connection_type, "
                             "billing_type, registration_date, created_at, updated_at) "
                             "VALUES (:fn, :ln, :mob, :hp, :rid, :uname, :pwd, "
                             ":flat, :bld, :area, :loc, :zone, "
                             ":bal, :ia, :tt, :ct, 'Prepaid', CURDATE(), NOW(), NOW())"),
                        {'fn': first, 'ln': last, 'mob': mobile or '',
                         'hp': phone or '', 'rid': ref_id or None,
                         'uname': username, 'pwd': pwd_hash,
                         'flat': flat or None, 'bld': building or None,
                         'area': area or None, 'loc': locality or None,
                         'zone': zone or None, 'bal': balance,
                         'ia': is_active, 'tt': tax_type, 'ct': conn_type}
                    )
                    cust_id = session.execute(text("SELECT LAST_INSERT_ID()")).fetchone()[0]

                    # Create User account for portal login
                    user_username = unique_username(username_raw)
                    session.execute(
                        text("INSERT INTO users (username, password_hash, role, "
                             "is_active, created_at) "
                             "VALUES (:uname, :pwd, 'support', 1, NOW())"),
                        {'uname': user_username, 'pwd': pwd_hash}
                    )
                    users_created += 1

                    # Reserve username
                    session.execute(
                        text("INSERT INTO username_reservations "
                             "(username, username_key, scope, customer_id, created_at) "
                             "VALUES (:u, :uk, 'customer', :cid, NOW())"),
                        {'u': username, 'uk': username.lower(), 'cid': cust_id}
                    )
                customers_created += 1
                cust_id = cust_id if not DRY_RUN else f'NEW_{n}'

            # Assign plan if we have a matching plan and dates
            if plan_name and start_date and end_date and cust_id and not DRY_RUN:
                pid = plan_id_map.get(plan_name)
                if pid and isinstance(pid, int):
                    existing_cp = session.execute(
                        text("SELECT id FROM customer_plans "
                             "WHERE customer_id = :cid AND plan_id = :pid "
                             "AND start_date = :sd AND end_date = :ed LIMIT 1"),
                        {'cid': cust_id, 'pid': pid, 'sd': start_date, 'ed': end_date}
                    ).fetchone()

                    if not existing_cp:
                        session.execute(
                            text("INSERT INTO customer_plans "
                                 "(customer_id, plan_id, start_date, end_date, "
                                 "status, auto_renew, online_renewal, price, "
                                 "created_at) "
                                 "VALUES (:cid, :pid, :sd, :ed, :st, 1, 1, :pr, NOW())"),
                            {'cid': cust_id, 'pid': pid, 'sd': start_date,
                             'ed': end_date, 'st': plan_status, 'pr': amount}
                        )
                        plans_assigned += 1
            elif plan_name and start_date and end_date and DRY_RUN:
                plans_assigned += 1

        except Exception as row_err:
            if not DRY_RUN:
                session.rollback()
            row_errors += 1
            if row_errors <= 20:
                print(f'  ROW {n} ERROR: {row_err}', flush=True)
            continue

        if n % 100 == 0:
            print(f'  ... processed {n}/{len(customers)} rows', flush=True)

    if not DRY_RUN:
        session.commit()

    print(f'  Customers created: {customers_created}, updated: {customers_updated}')
    print(f'  Users created: {users_created}')
    print(f'  Plans assigned: {plans_assigned}')
    print(f'  Skipped (missing name): {skipped}')
    print(f'  Row errors: {row_errors}')
    print()

except Exception as e:
    session.rollback()
    print(f'  ERROR in Phase 2: {e}')
    import traceback; traceback.print_exc()
    raise
finally:
    session.close()


# ── Summary ──────────────────────────────────────────────────────────────────
print('=' * 60)
if DRY_RUN:
    print('DRY RUN COMPLETE -- no data was written')
else:
    print('MIGRATION COMPLETE')
print('=' * 60)
