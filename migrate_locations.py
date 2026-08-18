"""Import localities, areas, and buildings from CSV files."""
import csv, os, pymysql

DOWNLOADS = os.path.expanduser(r'~\Downloads')
conn = pymysql.connect(host='sakura.proxy.rlwy.net', port=42443,
                       user='root', password='NHdgWnKBJMCenGfIgaZMQDtQZdbDoPaI',
                       database='railway')
cur = conn.cursor()

cur.execute('SELECT name FROM localities')
existing_loc = {r[0] for r in cur.fetchall()}
cur.execute('SELECT name FROM areas')
existing_area = {r[0] for r in cur.fetchall()}
cur.execute('SELECT name FROM buildings')
existing_bld = {r[0] for r in cur.fetchall()}

def clean(s):
    s = (s or '').strip().strip('"')
    s = s.lstrip('\u200e\u200f\ufeff')
    return s

# 1. Localities
with open(os.path.join(DOWNLOADS, 'building (2).csv'), 'r', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
loc_added = 0
for r in rows:
    name = clean(r.get('Locality', ''))
    if name and name not in existing_loc:
        cur.execute('INSERT INTO localities (name) VALUES (%s)', (name,))
        existing_loc.add(name)
        loc_added += 1
print(f'Localities: {loc_added} added, {len(existing_loc)} total')

# 2. Areas
with open(os.path.join(DOWNLOADS, 'building (1).csv'), 'r', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
area_added = 0
for r in rows:
    name = clean(r.get('Area', ''))
    if name and name not in existing_area:
        cur.execute('INSERT INTO areas (name) VALUES (%s)', (name,))
        existing_area.add(name)
        area_added += 1
print(f'Areas: {area_added} added, {len(existing_area)} total')

# 3. Buildings
with open(os.path.join(DOWNLOADS, 'building.csv'), 'r', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
bld_added = 0
for r in rows:
    name = clean(r.get('Building', ''))
    if name and name not in existing_bld and name not in ('-', '.', ''):
        cur.execute('INSERT INTO buildings (name) VALUES (%s)', (name,))
        existing_bld.add(name)
        bld_added += 1
print(f'Buildings: {bld_added} added, {len(existing_bld)} total')

conn.commit()
conn.close()
print('Done')
