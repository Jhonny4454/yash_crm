"""Fix customer titles: identify female first names and set Mrs."""
import pymysql

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

conn = pymysql.connect(host='sakura.proxy.rlwy.net', port=42443,
                       user='root', password='NHdgWnKBJMCenGfIgaZMQDtQZdbDoPaI',
                       database='railway')
cur = conn.cursor()

cur.execute('SELECT id, first_name, title FROM customers')
rows = cur.fetchall()

updated = 0
matched = []
for cid, fname, title in rows:
    key = (fname or '').strip().lower()
    if key in FEMALE_NAMES and title != 'Mrs.':
        cur.execute('UPDATE customers SET title = %s WHERE id = %s', ('Mrs.', cid))
        updated += 1
        matched.append(fname)

conn.commit()
conn.close()

print(f'Updated {updated} customers to Mrs.')
if matched:
    print('Matched names:')
    for n in sorted(set(matched)):
        print(f'  {n}')
