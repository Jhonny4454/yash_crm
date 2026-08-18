"""Set all customer portal passwords to 123456."""
import pymysql
from werkzeug.security import generate_password_hash

conn = pymysql.connect(host='sakura.proxy.rlwy.net', port=42443,
                       user='root', password='NHdgWnKBJMCenGfIgaZMQDtQZdbDoPaI',
                       database='railway')
cur = conn.cursor()

pwd_hash = generate_password_hash('123456')
cur.execute("UPDATE customers SET password_hash = %s WHERE password_hash IS NOT NULL", (pwd_hash,))
print(f'Updated {cur.rowcount} customer passwords to 123456')

conn.commit()
cur.close()
conn.close()
