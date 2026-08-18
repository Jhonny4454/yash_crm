"""Add locality_id and area_id to buildings, populate from CSV, and bulk-update customers.

Adds:
  1. ALTER TABLE buildings ADD COLUMN locality_id INT, area_id INT
  2. Populate from building CSVs (building -> area -> locality mapping)
  3. UPDATE all customers: zone='Yashnet', tax_type='Non-Taxable',
     service_provider_id=L2S, billing_address auto-generated
"""
import csv
import os
import sys
import pymysql

conn = pymysql.connect(
    host='sakura.proxy.rlwy.net', port=42443,
    user='root', password='NHdgWnKBJMCenGfIgaZMQDtQZdbDoPaI',
    database='railway',
    connect_timeout=10, read_timeout=60, write_timeout=60
)
cur = conn.cursor()

DRY_RUN = '--dry-run' in sys.argv
print(f'Dry run: {DRY_RUN}')
print()

# ── Phase 1: Add locality_id and area_id columns to buildings ──────────────
print('=' * 60)
print('PHASE 1: Add locality_id and area_id to buildings')
print('=' * 60)

try:
    cur.execute("ALTER TABLE buildings ADD COLUMN locality_id INT NULL")
    print('  Added locality_id')
except Exception as e:
    if 'Duplicate column' in str(e):
        print('  locality_id already exists')
    else:
        raise

try:
    cur.execute("ALTER TABLE buildings ADD COLUMN area_id INT NULL")
    print('  Added area_id')
except Exception as e:
    if 'Duplicate column' in str(e):
        print('  area_id already exists')
    else:
        raise

conn.commit()
print()

# ── Phase 2: Populate building locality_id and area_id from CSV ────────────
print('=' * 60)
print('PHASE 2: Populate building locality/area from CSV')
print('=' * 60)

DOWNLOADS = os.path.expanduser(r'~\Downloads')

# Load locality name -> id
cur.execute("SELECT id, name FROM localities")
locality_map = {name: lid for lid, name in cur.fetchall()}
print(f'  Localities: {len(locality_map)}')

# Load area name -> id
cur.execute("SELECT id, name FROM areas")
area_map = {name: aid for aid, name in cur.fetchall()}
print(f'  Areas: {len(area_map)}')

# Load building name -> id
cur.execute("SELECT id, name FROM buildings")
building_map = {name: bid for bid, name in cur.fetchall()}
print(f'  Buildings: {len(building_map)}')

# Read building CSV: Building, Area, Locality
with open(os.path.join(DOWNLOADS, 'building.csv'), 'r', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
print(f'  CSV rows: {len(rows)}')

updated = 0
skipped = 0
for r in rows:
    bld_name = (r.get('Building') or '').strip().strip('"').lstrip('\u200e\u200f\ufeff')
    area_name = (r.get('Area') or '').strip().strip('"').lstrip('\u200e\u200f\ufeff')
    loc_name = (r.get('Locality') or '').strip().strip('"').lstrip('\u200e\u200f\ufeff')

    if not bld_name or bld_name in ('-', ''):
        skipped += 1
        continue

    bld_id = building_map.get(bld_name)
    loc_id = locality_map.get(loc_name)
    area_id = area_map.get(area_name)

    if not bld_id:
        skipped += 1
        continue

    if loc_id or area_id:
        if not DRY_RUN:
            cur.execute(
                "UPDATE buildings SET locality_id = %s, area_id = %s WHERE id = %s",
                (loc_id, area_id, bld_id)
            )
        updated += 1

print(f'  Buildings updated: {updated}, Skipped: {skipped}')
conn.commit()
print()

# ── Phase 3: Bulk update customers ────────────────────────────────────────
print('=' * 60)
print('PHASE 3: Bulk update customers (zone, tax_type, provider, billing_address)')
print('=' * 60)

# Find L2S provider id
cur.execute("SELECT id FROM service_providers WHERE name = 'L2S' LIMIT 1")
row = cur.fetchone()
l2s_id = row[0] if row else None
print(f'  L2S provider id: {l2s_id}')

# Find Yashnet zone - create if not exists
cur.execute("SELECT id FROM zones WHERE name = 'Yashnet' LIMIT 1")
row = cur.fetchone()
if not row:
    if not DRY_RUN:
        cur.execute("INSERT INTO zones (name) VALUES ('Yashnet')")
        conn.commit()
        cur.execute("SELECT id FROM zones WHERE name = 'Yashnet'")
        row = cur.fetchone()
        print('  Created Yashnet zone')
    else:
        print('  [DRY RUN] Would create Yashnet zone')
yashnet_name = 'Yashnet'

# Update all customers
cur.execute("SELECT id, flat_no, building, area, locality, billing_address FROM customers")
customers = cur.fetchall()
print(f'  Customers to update: {len(customers)}')

zone_updated = 0
tax_updated = 0
provider_updated = 0
address_updated = 0

for cid, flat_no, building, area, locality, billing_address in customers:
    changes = []

    # Zone = Yashnet
    changes.append(("zone = %s", (yashnet_name,)))

    # Tax type = Non-Taxable
    changes.append(("tax_type = %s", ('Non-Taxable',)))

    # Service provider = L2S
    if l2s_id:
        changes.append(("service_provider_id = %s", (l2s_id,)))

    # Auto-generate billing_address if empty
    if not billing_address:
        parts = [flat_no, building, area, locality]
        parts = [p for p in parts if p and p != '-']
        if parts:
            addr = ' -> '.join(parts) + ', Navi Mumbai, Maharashtra'
            changes.append(("billing_address = %s", (addr,)))
            changes.append(("primary_address = %s", (addr,)))

    if changes and not DRY_RUN:
        set_clause = ', '.join(c[0] for c in changes)
        params = {}
        all_params = []
        for c in changes:
            for p in c[1]:
                all_params.append(p)
        # Build parameterized query
        set_parts = []
        idx = 1
        for c in changes:
            placeholders = []
            for _ in c[1]:
                placeholders.append(f'%s')
                idx += 1
            set_parts.append(c[0].replace('%s', ', '.join(placeholders)) if len(c[1]) > 1 else c[0])
        # Simpler approach: build query with %s
        set_str = ', '.join(c[0] for c in changes)
        flat_params = []
        for c in changes:
            flat_params.extend(c[1])
        cur.execute(f"UPDATE customers SET {set_str}, updated_at = NOW() WHERE id = %s",
                   flat_params + [cid])

    zone_updated += 1

if not DRY_RUN:
    conn.commit()

print(f'  Updated: {zone_updated} customers')
print()

# ── Summary ────────────────────────────────────────────────────────────────
if not DRY_RUN:
    conn.commit()
    print('All changes committed.')
else:
    print('DRY RUN complete -- no changes written.')

cur.close()
conn.close()

print()
print('=' * 60)
print('SUMMARY')
print('=' * 60)
print(f'  Building locality/area populated: {updated}')
print(f'  Customers updated (zone/tax/provider/address): {zone_updated}')
print('=' * 60)
