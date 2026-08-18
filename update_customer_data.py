"""Update customer data from CSV: addresses, plan dates, emails, titles.

Usage:
    python update_customer_data.py

Updates:
  1. Customer addresses (flat_no, building, area, locality) from CSV
  2. Customer plan start/end dates from CSV
  3. Sets email = username@gmail.com for all customers
  4. Fixes Mr/Mrs titles for females
"""
import csv
import os
import sys
from datetime import datetime

import pymysql

# ── DB connection ────────────────────────────────────────────────────────────
conn = pymysql.connect(
    host='sakura.proxy.rlwy.net', port=42443,
    user='root', password='NHdgWnKBJMCenGfIgaZMQDtQZdbDoPaI',
    database='railway',
    connect_timeout=10, read_timeout=30, write_timeout=30
)
cur = conn.cursor()

DRY_RUN = '--dry-run' in sys.argv

def parse_date(s):
    if not s or not s.strip():
        return None
    s = s.strip()
    for fmt in ('%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def clean(s):
    s = (s or '').strip().strip('\u200e\u200f\ufeff')
    return s or None

print(f'Dry run: {DRY_RUN}')
print()

# ── Load CSV ─────────────────────────────────────────────────────────────────
DOWNLOADS = os.path.expanduser(r'~\Downloads')
CSV_PATH = os.path.join(DOWNLOADS, 'customers (2).csv')
with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
print(f'CSV rows: {len(rows)}')

# ── Build lookup: CSV mobile → row data ─────────────────────────────────────
csv_by_mobile = {}
for r in rows:
    mob = clean(r.get('Mobile No.'))
    if mob:
        csv_by_mobile[mob] = r

# ── Build lookup: CSV reference_id → row data ───────────────────────────────
csv_by_ref = {}
for r in rows:
    ref = clean(r.get('Reference Id'))
    if ref:
        csv_by_ref[ref] = r

# ── Phase 1: Update customer addresses ──────────────────────────────────────
print('=' * 60)
print('PHASE 1: Update customer addresses from CSV')
print('=' * 60)

cur.execute('SELECT id, mobile, reference_id FROM customers')
db_customers = cur.fetchall()
print(f'DB customers: {len(db_customers)}')

addr_updated = 0
addr_no_match = 0
addr_errors = 0

for cid, mobile, ref_id in db_customers:
    csv_row = csv_by_mobile.get(mobile) or csv_by_ref.get(ref_id)
    if not csv_row:
        addr_no_match += 1
        continue

    flat = clean(csv_row.get('Customer Flat No.'))
    building = clean(csv_row.get('Customer Building Name'))
    area = clean(csv_row.get('Customer Area Name'))
    locality = clean(csv_row.get('Customer Locality Name'))

    if flat or building or area or locality:
        try:
            if not DRY_RUN:
                cur.execute(
                    "UPDATE customers SET flat_no = %s, building = %s, "
                    "area = %s, locality = %s, updated_at = NOW() WHERE id = %s",
                    (flat, building, area, locality, cid)
                )
            addr_updated += 1
        except Exception as e:
            addr_errors += 1
            if addr_errors <= 5:
                print(f'  ERROR customer {cid}: {e}')

print(f'  Updated: {addr_updated}, No match: {addr_no_match}, Errors: {addr_errors}')
print()

# ── Phase 2: Update customer plan start/end dates ───────────────────────────
print('=' * 60)
print('PHASE 2: Update customer plan start/end dates from CSV')
print('=' * 60)

cur.execute("""
    SELECT cp.id, cp.customer_id, cp.plan_id, cp.start_date, cp.end_date,
           c.mobile, c.reference_id, p.name AS plan_name
    FROM customer_plans cp
    JOIN customers c ON c.id = cp.customer_id
    JOIN plans p ON p.id = cp.plan_id
""")
db_plans = cur.fetchall()
print(f'DB customer_plans: {len(db_plans)}')

plan_updated = 0
plan_no_change = 0
plan_no_match = 0
plan_errors = 0

for cp_id, cust_id, plan_id, cur_start, cur_end, mobile, ref_id, plan_name in db_plans:
    csv_row = csv_by_mobile.get(mobile) or csv_by_ref.get(ref_id)
    if not csv_row:
        plan_no_match += 1
        continue

    csv_plan = clean(csv_row.get('Plan Name'))
    csv_start = parse_date(csv_row.get('Start Date'))
    csv_end = parse_date(csv_row.get('End Date'))

    if not csv_start or not csv_end:
        plan_no_match += 1
        continue

    if cur_start == csv_start and cur_end == csv_end:
        plan_no_change += 1
        continue

    if csv_plan and csv_plan != plan_name:
        plan_no_match += 1
        continue

    try:
        if not DRY_RUN:
            cur.execute(
                "UPDATE customer_plans SET start_date = %s, end_date = %s WHERE id = %s",
                (csv_start, csv_end, cp_id)
            )
        plan_updated += 1
    except Exception as e:
        plan_errors += 1
        if plan_errors <= 5:
            print(f'  ERROR customer_plan {cp_id}: {e}')

print(f'  Updated: {plan_updated}, No change needed: {plan_no_change}, '
      f'No match: {plan_no_match}, Errors: {plan_errors}')
print()

# ── Phase 3: Set email = username@gmail.com for all customers ────────────────
print('=' * 60)
print('PHASE 3: Set email = username@gmail.com for all customers')
print('=' * 60)

cur.execute("SELECT id, username, email FROM customers WHERE email IS NULL OR email = ''")
customers_no_email = cur.fetchall()
print(f'Customers without email: {len(customers_no_email)}')

email_updated = 0
email_errors = 0

for cid, username, email in customers_no_email:
    if not username:
        continue
    new_email = f'{username}@gmail.com'
    try:
        if not DRY_RUN:
            cur.execute(
                "UPDATE customers SET email = %s, updated_at = NOW() WHERE id = %s",
                (new_email, cid)
            )
        email_updated += 1
    except Exception as e:
        email_errors += 1
        if email_errors <= 5:
            print(f'  ERROR customer {cid}: {e}')

print(f'  Updated: {email_updated}, Errors: {email_errors}')
print()

# ── Phase 4: Fix Mr/Mrs titles for females ──────────────────────────────────
print('=' * 60)
print('PHASE 4: Fix Mr/Mrs titles for females')
print('=' * 60)

FEMALE_NAMES = {
    'aditi', 'ahsna', 'aishwariya', 'aishwarya', 'akanksha', 'amrita',
    'amruta', 'anita', 'anitha', 'anjali', 'ankita', 'anuja', 'arati',
    'archana', 'asha', 'ashwini', 'ashwinii', 'bhagyashri', 'bhavana',
    'chanamma', 'deepali', 'deepika', 'essakiammal', 'gayatri', 'gopa',
    'harshada', 'harshda', 'hema', 'hemangi', 'jasmine', 'jayshree',
    'jyoti', 'kajal', 'kavita', 'kirti', 'komal', 'kritika', 'krystal',
    'lavina', 'laxmi', 'mamta', 'manisha', 'mansi', 'mariammal',
    'mayuri', 'meenu', 'megha', 'mohini', 'monika', 'mony', 'mugdha',
    'muktai', 'neha', 'nidhi', 'nilam', 'nisha', 'nishigandha', 'nutan',
    'pallavi', 'payal', 'pooja', 'prajakta', 'pranali', 'pratiksha',
    'preeti', 'priyambal', 'priyanka', 'ragini', 'rani', 'rashmi',
    'reena', 'reeta', 'rhutika', 'rita', 'ruby', 'ruchika', 'rutuja',
    'sadhana', 'samidha', 'sangeeta', 'sangita', 'sanjana', 'sarita',
    'saroj', 'sayali', 'sejal', 'shashikala', 'sheetal', 'shefali',
    'shital', 'shraddha', 'shruti', 'shrutika', 'shubhangi', 'smita',
    'snehalata', 'sohaja', 'sonali', 'steffi', 'suchorita', 'sukmita',
    'sunita', 'supriya', 'suvarana', 'sweta', 'tarul', 'trupti',
    'ujjwala', 'vaishali', 'vaishnavi', 'vandana', 'vanita', 'vidya',
    'yashwanthi', 'yogini', 'yogita', 'surachana',
}

cur.execute("SELECT id, first_name, title FROM customers")
all_customers = cur.fetchall()

title_updated = 0
for cid, fname, title in all_customers:
    key = (fname or '').strip().lower()
    if key in FEMALE_NAMES and title != 'Mrs.':
        try:
            if not DRY_RUN:
                cur.execute(
                    "UPDATE customers SET title = %s, updated_at = NOW() WHERE id = %s",
                    ('Mrs.', cid)
                )
            title_updated += 1
        except Exception as e:
            pass

print(f'  Updated to Mrs.: {title_updated}')
print()

# ── Final commit ─────────────────────────────────────────────────────────────
if not DRY_RUN:
    conn.commit()
    print('Changes committed.')
else:
    print('DRY RUN complete -- no changes written.')

cur.close()
conn.close()

print()
print('=' * 60)
print('SUMMARY')
print('=' * 60)
print(f'  Addresses updated:  {addr_updated}')
print(f'  Plan dates updated: {plan_updated}')
print(f'  Emails set:         {email_updated}')
print(f'  Titles fixed:       {title_updated}')
print('=' * 60)
