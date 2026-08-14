"""
UniCRM REST API - v1

Mount from app.py:

    from blueprints.api import register_api
    register_api(app, csrf=csrf)

Everything lives under /api/v1. The Jinja2 site keeps working untouched, so
you can migrate the admin panel to React screen by screen instead of going
dark during the rewrite.

CHANGED: `masters` and `staff` are now registered. Without them the React
masters screens (zones, tax, addon categories, expense categories, staff
types...) all called endpoints that did not exist and rendered an empty
table with a network error.
"""
from flask import Blueprint, jsonify

API_PREFIX = '/api/v1'

api_bp = Blueprint('api_v1', __name__, url_prefix=API_PREFIX)

#: Kept as an alias so `from blueprints.api import api` also works.
api = api_bp


@api_bp.get('/health')
def health():
    return jsonify(ok=True, service='unicrm-api', version='1.3')


@api_bp.app_errorhandler(404)
def _404(_e):
    from flask import request
    if request.path.startswith(API_PREFIX):
        return jsonify(ok=False, error='not_found'), 404
    return _e


# NOTE: there is deliberately no app_errorhandler(500) here.
#
# There used to be, returning a bare {ok: false, error: 'server_error'} with no
# reason in it. app.py registers a 500 handler that names the actual exception
# (and includes a traceback outside production), and whichever registers last
# wins - so which of the two answered depended on import order. One handler,
# in one place, beats two that quietly compete.


#: Whether the sub-blueprints have been attached to `api_bp` yet.
#:
#: `api_bp` is a module-level singleton, but the old guard here asked
#: `if 'api_v1' in app.blueprints` - a question about the APP. So the second
#: Flask app in a process (check_routes.py and doctor.py each build one, as do
#: the tests) passed the guard and tried to attach the sub-blueprints to an
#: api_bp that was already mounted, and Flask refused:
#:
#:     The setup method 'register_blueprint' can no longer be called on the
#:     blueprint 'api_v1'. It has already been registered at least once.
#:
#: The children only ever need attaching once per process; mounting on an app
#: is the part that happens per app.
_children_attached = False


def register_api(app, csrf=None):
    """
    Attach every API sub-blueprint to ``api_bp`` and mount it on ``app``.

    Safe to call for more than one app in the same process.
    """
    global _children_attached

    if 'api_v1' in app.blueprints:
        return api_bp

    if not _children_attached:
        from . import auth, authorisation, billing_run, company, customer_actions
        from . import customer_billing, dashboard, integrations, invoices, masters
        from . import portal, renewals, resources, staff

        for module in (auth, resources, masters, portal, dashboard,
                       company, staff, integrations, customer_actions,
                       customer_billing, invoices, authorisation, billing_run,
                       renewals):
            api_bp.register_blueprint(module.bp)
        _children_attached = True

    app.register_blueprint(api_bp)

    # The SPA and the mobile app authenticate with a Bearer token, so CSRF
    # protection (which is for cookie-authenticated forms) must be off here.
    if csrf is not None:
        csrf.exempt(api_bp)

    # CORS is NOT set here any more. There were two after_request handlers
    # writing the same four headers - this one and security.py's - and Flask
    # runs them in reverse registration order, so which one won depended on
    # import order rather than on anything anybody decided. This copy also had
    # no Access-Control-Max-Age, so it silently discarded the pre-flight cache
    # the other one was trying to set, and it compared the Origin header with
    # its trailing slash intact while the other stripped it. One place, one
    # rule: see `_install_cors` in security.py, which harden(app) applies.

    return api_bp
