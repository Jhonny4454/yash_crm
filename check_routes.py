"""
check_routes.py
===============

Register every API blueprint the way the app does and fail loudly on the
problems that otherwise fail *silently*.

Run it after adding or moving an endpoint::

    python check_routes.py

Why this exists: two blueprints both defined ``GET /customers/<cid>/ledger``.
Flask does not complain - it simply serves whichever blueprint registered
first. The newer, richer handler never ran, and nothing anywhere raised. The
screens just quietly rendered blank reference columns and dropped every wallet
movement from the statement, which looks like a UI bug and is not one.

A duplicate route is the kind of mistake that is invisible in review, invisible
in a unit test of either handler on its own, and obvious the moment you list
the whole URL map. So list it.

Exit code is 0 when clean, 1 when something needs attention - safe to wire
into CI or a pre-commit hook.
"""
import collections
import sys
import types


def build_app():
    """A bare Flask app with every API blueprint mounted, and no database."""
    from flask import Flask

    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        JWT_SECRET_KEY='route-check-only-not-a-real-secret-value',
    )

    import models  # noqa: F401  (registers the mappers)
    import models_ext  # noqa: F401
    try:
        import models_api  # noqa: F401
    except ImportError:
        pass

    from models import db
    db.init_app(app)

    # app.py imports the blueprints at start-up, so any module-level import of
    # app.py from inside one is a cycle. Stubbing it here means this script
    # also proves no blueprint has picked one up.
    if 'app' not in sys.modules:
        stub = types.ModuleType('app')
        for name in ('log_audit', 'send_template_message', 'send_email'):
            setattr(stub, name, lambda *a, **k: None)
        stub.generate_invoice_no = lambda: 'INV-CHECK'
        sys.modules['app'] = stub

    from blueprints.api import register_api
    register_api(app, csrf=None)
    return app


def main():
    try:
        app = build_app()
    except Exception as exc:
        print(f'FAIL  the blueprints do not import cleanly: '
              f'{type(exc).__name__}: {exc}')
        return 1

    rules = list(app.url_map.iter_rules())
    problems = 0

    # --- 1. Two handlers on one method+path ---------------------------------
    by_signature = collections.defaultdict(set)
    for rule in rules:
        for method in rule.methods - {'HEAD', 'OPTIONS'}:
            by_signature[(method, str(rule))].add(rule.endpoint)

    collisions = {k: sorted(v) for k, v in by_signature.items() if len(v) > 1}
    if collisions:
        problems += len(collisions)
        print(f'FAIL  {len(collisions)} route collision(s) - '
              f'the FIRST registered blueprint wins and the rest never run:')
        for (method, path), endpoints in sorted(collisions.items()):
            print(f'        {method:6} {path}')
            for endpoint in endpoints:
                print(f'          - {endpoint}')
    else:
        print(f'ok    no route collisions ({len(rules)} routes)')

    # --- 2. Endpoint names, which url_for() resolves on ---------------------
    names = collections.Counter(rule.endpoint for rule in rules)
    duplicate_names = {k: v for k, v in names.items() if v > 1}
    if duplicate_names:
        problems += len(duplicate_names)
        print(f'FAIL  {len(duplicate_names)} duplicate endpoint name(s):')
        for name, count in sorted(duplicate_names.items()):
            print(f'        {name} x{count}')
    else:
        print('ok    every endpoint name is unique')

    # --- 3. Rules with nothing behind them ----------------------------------
    unbound = [r.endpoint for r in rules if app.view_functions.get(r.endpoint) is None]
    if unbound:
        problems += len(unbound)
        print(f'FAIL  {len(unbound)} rule(s) with no view function: {unbound}')
    else:
        print('ok    every rule has a view function')

    # --- 4. Endpoints reachable without an auth decorator -------------------
    #  A missing @staff_required is a data leak, not a typo, so it is worth a
    #  standing check rather than a code review someone might skim.
    PUBLIC = {
        'api_v1.health', 'api_v1.static',
        'api_v1.api_auth.staff_login', 'api_v1.api_auth.customer_login',
        'api_v1.api_auth.refresh', 'api_v1.api_auth.customer_forgot_password',
        'api_v1.api_auth.customer_reset_password',
        # Same reason as the customer pair: someone who has forgotten their
        # password cannot present a token to ask for a reset.
        'api_v1.api_auth.staff_forgot_password',
        'api_v1.api_auth.staff_reset_password',
        'api_v1.api_portal.portal_pay_webhook',
        'static',

        # Deliberately open, each for a stated reason:
        #  logout   - the JWT is stateless, so this only tells the client to
        #             drop its token. Requiring a valid token to log out would
        #             leave someone holding an expired one unable to.
        #  branding - the login screen needs the company name and logo before
        #             any token exists.
        'api_v1.api_auth.logout',
        'api_v1.api_company.branding',
    }
    undecorated = []
    for rule in rules:
        if rule.endpoint in PUBLIC:
            continue
        view = app.view_functions.get(rule.endpoint)
        if view is None:
            continue
        # The decorators wrap the view, so the original name survives on
        # __wrapped__ only when something wrapped it.
        if not hasattr(view, '__wrapped__'):
            undecorated.append((rule.endpoint, str(rule)))

    if undecorated:
        print(f'WARN  {len(undecorated)} endpoint(s) look undecorated - '
              f'check each has @staff_required / @admin_required / '
              f'@customer_required:')
        for endpoint, path in sorted(undecorated):
            print(f'        {path}  ({endpoint})')
    else:
        print('ok    every non-public endpoint is wrapped by an auth decorator')

    print()
    if problems:
        print(f'{problems} problem(s) found.')
        return 1

    print('Route map is clean.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
