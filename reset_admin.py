"""
reset_admin.py
==============

Get back into the admin panel when the password no longer works.

    python reset_admin.py                       # admin / a password you are shown
    python reset_admin.py admin NewPass123      # set one yourself
    python reset_admin.py --list                # who can sign in at all

Why this exists
---------------
``init_database()`` creates the ``admin`` user with ADMIN_PASSWORD (or
``admin123``) ONLY when that row does not already exist. Once it does - and on
any deployment that has run once, it does - changing the environment variable
has no effect and the seed never runs again, so `admin` / `admin123` stops
being the answer the moment anybody changes the password. There was no way
back other than editing the database by hand.

This resets the hash with the same function the login endpoint checks against,
so what you set here is what works. It also re-enables a disabled account,
which is the other way to be locked out (the API answers `account_disabled`
with a 403 rather than "wrong password", so read the error before assuming the
password is the problem).

Run it wherever the database is reachable - a Render shell, or locally with
DATABASE_URL pointing at production.

Nothing is printed to a log: the password is written to the terminal only.
"""
import secrets
import string
import sys


def _generated():
    """A password worth having, since most people keep the one they are given."""
    alphabet = string.ascii_letters + string.digits
    return 'Yis-' + ''.join(secrets.choice(alphabet) for _ in range(10))


def main(argv):
    from app import app
    from models import User, db

    if '--list' in argv:
        with app.app_context():
            rows = User.query.order_by(User.id).all()
            if not rows:
                print('No users at all. Start the app once to seed `admin`.')
                return 0
            print(f'{"id":>4}  {"username":<20} {"role":<10} active')
            for user in rows:
                print(f'{user.id:>4}  {user.username:<20} {user.role or "":<10} '
                      f'{"yes" if user.is_active else "NO"}')
        return 0

    username = argv[0] if argv else 'admin'
    password = argv[1] if len(argv) > 1 else _generated()

    if len(password) < 8:
        print('Refusing: use at least 8 characters.')
        return 2

    with app.app_context():
        user = User.query.filter_by(username=username).first()
        created = user is None
        if created:
            user = User(username=username, full_name='Administrator',
                        role='admin', email='admin@yash.com')
            db.session.add(user)

        user.set_password(password)
        # Being disabled is the other way to be locked out, and it answers
        # with a different error than a wrong password does.
        user.is_active = True
        db.session.commit()

    print()
    print(f'  {"Created" if created else "Reset"}: {username}')
    print(f'  Password: {password}')
    print()
    print('  Sign in, then change it from Profile. Anyone who can read your')
    print('  shell history can read it here.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
