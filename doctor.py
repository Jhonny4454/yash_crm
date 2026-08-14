"""
doctor.py
=========

Answers "why is it broken?" without needing anyone to read a traceback.

    python doctor.py

Every check prints OK, WARN or FAIL with the actual error and what to do
about it. It never changes anything - run ``upgrade_schema.py`` for that.

Why this exists
---------------
When the API started answering 500 to everything, the only record of the
reason was a traceback in the Flask console, and the browser showed an
unexplained failure. Working out that the fault was below the application
rather than in any one endpoint took a round trip and a guess. These are the
questions that had to be asked, in the order worth asking them.
"""
import os
import socket
import sys
import time
import traceback
from urllib.parse import urlsplit

PASS, WARN, FAIL = 'OK  ', 'WARN', 'FAIL'
_results = []


def report(level, title, detail='', fix=''):
    _results.append(level)
    print(f'[{level}] {title}')
    if detail:
        print(f'        {detail}')
    if fix and level != PASS:
        print(f'        -> {fix}')


def section(name):
    print(f'\n--- {name} ' + '-' * max(0, 58 - len(name)))


# --------------------------------------------------------------------------- #
def check_imports():
    section('Code')
    try:
        import app  # noqa: F401
        report(PASS, 'app.py imports cleanly')
        return app
    except Exception as exc:
        report(FAIL, 'app.py could not be imported', f'{type(exc).__name__}: {exc}',
               'Flask cannot start at all until this is fixed. Full trace below.')
        traceback.print_exc()
        return None


def check_config(app_module):
    section('Configuration')
    cfg = app_module.app.config
    url = cfg.get('SQLALCHEMY_DATABASE_URI') or ''
    split = urlsplit(url)
    safe = url.replace(f':{split.password}@', ':***@') if split.password else url
    report(PASS, 'Database URL', safe or '(not set)')

    if url.startswith('sqlite'):
        report(WARN, 'Running on SQLite',
               'Fine for a laptop; the live system is MySQL.',
               'Set DATABASE_URL if you meant to use MySQL.')

    for name in ('SECRET_KEY', 'JWT_SECRET_KEY'):
        value = cfg.get(name) or ''
        if value in ('dev-secret-key-change-me', 'dev', 'changeme', ''):
            report(WARN, f'{name} is still the development default',
                   fix='python -c "import secrets; print(secrets.token_hex(32))"')
        else:
            report(PASS, f'{name} is set')


def check_database(app_module):
    section('Database')
    from models import db
    from sqlalchemy import inspect, text

    with app_module.app.app_context():
        # 1. Can we reach it at all? This is the question that matters first:
        #    when it fails, EVERY endpoint answers 500, including public ones.
        try:
            db.session.execute(text('SELECT 1'))
            report(PASS, 'Database connection')
        except Exception as exc:
            message = str(getattr(exc, 'orig', exc))
            hint = 'Check the server is running and DATABASE_URL is correct.'
            if '2003' in message or 'Connect' in message:
                hint = 'The MySQL server is not accepting connections. Is it running?'
            elif '1045' in message:
                hint = 'The username or password in DATABASE_URL is wrong.'
            elif '1049' in message:
                hint = 'That database name does not exist on the server.'
            elif '1044' in message:
                # MySQL reports "access denied to database X" for a database
                # that does not exist as well as for one you may not touch,
                # so the hint has to cover both.
                hint = ('Either that database does not exist, or this user has '
                        'no rights to it. Check the name at the end of '
                        'DATABASE_URL.')
            elif '1040' in message or 'many connections' in message.lower():
                hint = ('MySQL has run out of connections. Restart MySQL, and '
                        'close any other clients holding sessions open.')
            report(FAIL, 'Database connection', message[:200], hint)
            return

        # 1b. How FAR away is it?
        #
        # This is the question that explains "the whole site is slow" when
        # every screen is individually well written. A screen issues 5-15
        # queries. Against a database on this machine that is under a
        # millisecond of travel and nobody notices. Against one in another
        # datacentre - Railway, a managed MySQL - each query is a round trip,
        # and 15 of them at 200ms is three seconds before any work is done.
        # No amount of tuning the application fixes that; only moving the
        # database next to the app does.
        try:
            samples = []
            for _ in range(10):
                started = time.perf_counter()
                db.session.execute(text('SELECT 1'))
                samples.append((time.perf_counter() - started) * 1000)
            samples.sort()
            typical = samples[len(samples) // 2]

            budget = typical * 15      # a normal screen's worth of queries
            detail = (f'{typical:.1f} ms per query '
                      f'(~{budget:.0f} ms for a typical 15-query screen)')

            if typical < 3:
                report(PASS, 'Database round trip', detail)
            elif typical < 25:
                report(WARN, 'The database is not local', detail,
                       'Every screen pays this 5-15 times. Tolerable, but it '
                       'is the reason pages feel heavy.')
            else:
                report(FAIL, 'The database is a long way away', detail,
                       'This alone makes every page slow, and no application '
                       'change will fix it. Move the database to the same '
                       'host/region as the app, or run both locally.')
        except Exception as exc:
            report(WARN, 'Could not measure database latency',
                   f'{type(exc).__name__}: {exc}'[:160])

        # 2. Does the live schema match the models? A missing column makes
        #    every query on that table fail, which looks identical to a bug.
        inspector = inspect(db.engine)
        live_tables = set(inspector.get_table_names())
        expected = set(db.metadata.tables)

        missing_tables = sorted(expected - live_tables)
        if missing_tables:
            report(FAIL, f'{len(missing_tables)} table(s) missing',
                   ', '.join(missing_tables), 'python upgrade_schema.py')
        else:
            report(PASS, f'All {len(expected)} tables present')

        missing_columns = []
        for name, table in db.metadata.tables.items():
            if name not in live_tables:
                continue
            live = {c['name'] for c in inspector.get_columns(name)}
            for column in table.columns:
                if column.name not in live:
                    missing_columns.append(f'{name}.{column.name}')

        if missing_columns:
            report(FAIL, f'{len(missing_columns)} column(s) missing',
                   ', '.join(missing_columns[:12])
                   + ('...' if len(missing_columns) > 12 else ''),
                   'python upgrade_schema.py')
        else:
            report(PASS, 'Every model column exists in the database')

        # 3. Mappers. A bad relationship breaks the first query of ANY model.
        try:
            from sqlalchemy.orm import configure_mappers
            configure_mappers()
            report(PASS, 'Model relationships configure cleanly')
        except Exception as exc:
            report(FAIL, 'Model relationships are misconfigured',
                   f'{type(exc).__name__}: {exc}'[:200],
                   'Every database query fails while this is true.')

        # 4. A real read through the ORM, end to end.
        try:
            from models import Company, Customer, User
            counts = {'users': User.query.count(),
                      'customers': Customer.query.count(),
                      'companies': Company.query.count()}
            report(PASS, 'ORM reads work',
                   ', '.join(f'{k}: {v}' for k, v in counts.items()))
            if not counts['companies']:
                report(WARN, 'No company record',
                       'Invoices and the portal header need one.',
                       'Add one under Settings > Company Details.')
        except Exception as exc:
            report(FAIL, 'ORM read failed',
                   f'{type(exc).__name__}: {getattr(exc, "orig", exc)}'[:200])


def check_endpoints(app_module):
    section('API')
    from models import User, db
    from blueprints.api.utils import make_token

    with app_module.app.app_context():
        admin = User.query.filter_by(role='admin').first()
        if not admin:
            report(WARN, 'No admin user to test with')
            return
        token = make_token(admin.id, 'staff', 'admin')

    app_module.app.config['PROPAGATE_EXCEPTIONS'] = False
    client = app_module.app.test_client()
    headers = {'Authorization': f'Bearer {token}'}

    # Login, without needing anyone's password.
    #
    # A deliberately wrong password must come back 401. If it comes back 500,
    # the endpoint itself is broken and NOBODY can sign in - which looks
    # identical, from the login screen, to having forgotten the password.
    try:
        resp = client.post('/api/v1/auth/staff/login',
                           json={'username': '__doctor__', 'password': '__wrong__'})
        if resp.status_code == 401:
            report(PASS, 'Staff login endpoint', 'rejects a bad password correctly')
        elif resp.status_code >= 500:
            payload = resp.get_json(silent=True) or {}
            report(FAIL, 'Staff login endpoint is broken',
                   f"HTTP {resp.status_code} {payload.get('detail') or ''}"[:220],
                   'Nobody can sign in while this is true.')
        else:
            report(WARN, 'Staff login endpoint',
                   f'answered HTTP {resp.status_code} to a bad password')
    except Exception as exc:
        report(FAIL, 'Staff login endpoint raised',
               f'{type(exc).__name__}: {exc}'[:200])

    for path in ('/api/v1/health', '/api/v1/branding', '/api/v1/auth/staff/me',
                 '/api/v1/dashboard/summary', '/api/v1/customers?per_page=1',
                 '/api/v1/invoices?per_page=1', '/api/v1/payments?per_page=1'):
        try:
            resp = client.get(path, headers=headers)
        except Exception as exc:
            report(FAIL, path, f'{type(exc).__name__}: {exc}'[:180])
            continue
        if resp.status_code < 400:
            report(PASS, path, f'HTTP {resp.status_code}')
        else:
            payload = resp.get_json(silent=True) or {}
            report(FAIL, path, f"HTTP {resp.status_code} "
                               f"{payload.get('detail') or payload.get('error') or ''}"[:200])


def check_messaging_schema(app_module):
    section('Messaging')
    with app_module.app.app_context():
        try:
            from models import MessageTemplate
            rows = MessageTemplate.query.all()
        except Exception as exc:
            report(FAIL, 'Message templates cannot be read',
                   str(getattr(exc, 'orig', exc))[:200],
                   'python upgrade_schema.py - then restart Flask.')
            return

        usable = [r for r in rows if r.is_active and (r.body or '').strip()]
        if not rows:
            report(FAIL, 'No message templates at all', fix='python upgrade_schema.py')
        elif not any(r.template_type == 'bill' for r in usable):
            report(FAIL, 'No usable "bill" template',
                   'Sending a bill will fail for every customer.',
                   'Settings > WhatsApp gateway > Restore defaults.')
        else:
            report(PASS, f'{len(usable)} usable template(s)')


def check_whatsapp(app_module):
    section('WhatsApp gateway')
    with app_module.app.app_context():
        try:
            from services.messaging import (SEND_TIMEOUT, is_configured,
                                            provider_endpoint, _setting)
        except Exception as exc:
            report(FAIL, 'messaging module', f'{type(exc).__name__}: {exc}'[:180])
            return

        enabled = _setting('wa_enabled') in ('1', 'true', 'True', 'yes', 'on')
        endpoint = provider_endpoint()
        has_key = bool((_setting('wa_api_token') or '').strip())

        if not enabled:
            report(WARN, 'Sending is switched off',
                   'Messages are logged, not sent.',
                   'Set Wa Enabled to 1 in Settings when you are ready.')
            return

        report(PASS, 'Sending is on', f'provider: {_setting("wa_provider", "generic")}')
        if not endpoint:
            report(FAIL, 'No endpoint', fix='Set Wa Api Url in Settings.')
        if not has_key:
            report(FAIL, 'No API key', fix='Set Wa Api Token in Settings.')

        if endpoint:
            host = urlsplit(endpoint).hostname
            port = urlsplit(endpoint).port or (443 if endpoint.startswith('https') else 80)
            try:
                with socket.create_connection((host, port), timeout=SEND_TIMEOUT[0]):
                    report(PASS, f'{host} is reachable')
            except Exception as exc:
                report(FAIL, f'{host} is NOT reachable',
                       f'{type(exc).__name__}: {exc}'[:160],
                       'Sends will hang and time out. Check the URL and that '
                       'this machine has outbound internet access.')

        # --- can this gateway send an APPROVED TEMPLATE? ------------------
        #
        # The single most expensive thing to get wrong. Free text only reaches
        # somebody who messaged the business in the last 24 hours, and nobody
        # messages their ISP before a bill arrives - so a gateway that cannot
        # send templates delivers no bills at all while reporting success for
        # every one of them.
        try:
            from services.messaging import provider_sends_templates
            if provider_sends_templates():
                report(PASS, 'Approved templates can be sent')
            else:
                report(FAIL, 'This gateway cannot send approved templates',
                       'Bills, reminders and expiry notices will be accepted '
                       'by the gateway and delivered to nobody.',
                       'Set the gateway to WabAssist or Meta Cloud API in '
                       'Settings > WhatsApp gateway.')
        except Exception as exc:                             # noqa: BLE001
            report(WARN, 'Could not check template support',
                   f'{type(exc).__name__}: {exc}'[:160])

        # --- can Meta FETCH the PDF we attach? ----------------------------
        #
        # The bill and receipt templates carry a DOCUMENT header, and Meta
        # collects that file itself from a public URL. A link pointing at
        # localhost resolves on this machine and nowhere else, so the message
        # goes out with no attachment and nothing reports a problem.
        base = (app_module.app.config.get('PUBLIC_BASE_URL') or '').strip()
        if not base:
            report(FAIL, 'PUBLIC_BASE_URL is not set',
                   'Bill and receipt PDFs are attached by giving Meta a link '
                   'to fetch. Without this, the link is built from whatever '
                   'address the request arrived on - localhost, behind a '
                   'proxy - and the PDF never attaches.',
                   'Set PUBLIC_BASE_URL in .env to the https address this API '
                   'answers on from the public internet.')
        elif not base.startswith('https://'):
            report(FAIL, 'PUBLIC_BASE_URL is not https', base,
                   'Meta refuses to fetch a document over http.')
        elif any(local in base for local in
                 ('localhost', '127.0.0.1', '0.0.0.0', '192.168.', '10.0.')):
            report(FAIL, 'PUBLIC_BASE_URL points at a private address', base,
                   "Meta's servers cannot reach it, so bills will send with "
                   'no attachment. Use the public address of this API.')
        else:
            report(PASS, 'PUBLIC_BASE_URL looks reachable', base,
                   'Confirm by opening a bill link from the message log on a '
                   'phone with mobile data, not office wifi.')

        if is_configured() and has_key:
            report(PASS, 'Ready to send',
                   'Use Settings > WhatsApp gateway > Send test to confirm.')


def main():
    print('UniCRM doctor')
    print('=' * 64)
    app_module = check_imports()
    if app_module is None:
        return 1

    check_config(app_module)
    check_database(app_module)
    if FAIL not in _results:
        check_endpoints(app_module)
    else:
        section('API')
        print('        skipped - fix the failures above first.')
    check_messaging_schema(app_module)
    check_whatsapp(app_module)

    print('\n' + '=' * 64)
    fails = _results.count(FAIL)
    warns = _results.count(WARN)
    if fails:
        print(f'{fails} failure(s), {warns} warning(s). '
              f'Start with the first FAIL above - the rest often follow from it.')
    elif warns:
        print(f'No failures, {warns} warning(s). The app should be working.')
    else:
        print('Everything checks out.')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
