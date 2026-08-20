"""Remove all staff except admin, dinesh, nitesh, ram."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from app import app
from models import db, User

KEEP = {'admin', 'dinesh', 'nitesh', 'ram'}

with app.app_context():
    users = User.query.filter(User.is_active.is_(True)).all()
    before = len(users)
    for u in users:
        if u.username.lower() in KEEP:
            continue
        db.session.delete(u)
    db.session.commit()

    after = User.query.filter(User.is_active.is_(True)).count()
    print(f"Deleted {before - after} staff. Remaining: {after}")
    for u in User.query.filter(User.is_active.is_(True)).order_by(User.username):
        print(f"  {u.username} ({u.role})")
