"""
blueprints/api/utils.py
=======================

Shared helpers for the UniCRM REST API.

JWT auth (stateless), role decorators, pagination and error envelopes.
No Flask-Login / no CSRF here - the React SPA and the React Native app both
authenticate with a Bearer token.
"""
import json
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

import jwt
from flask import current_app, jsonify, request

ALGORITHM = 'HS256'
ACCESS_MINUTES_KEY = 'JWT_ACCESS_MINUTES'


def escape_like(value):
    """Escape SQL LIKE metacharacters (``%``, ``_``) for use with ``ilike``."""
    return (str(value or '')
            .replace('\\', '\\\\')
            .replace('%', '\\%')
            .replace('_', '\\_'))
ACCESS_HOURS_KEY = 'JWT_ACCESS_HOURS'
REFRESH_DAYS_KEY = 'JWT_REFRESH_DAYS'


def _secret():
    return (current_app.config.get('JWT_SECRET_KEY')
            or current_app.config.get('SECRET_KEY'))


def make_token(subject_id, kind, role=None, ttl=None, token_type='access'):
    """
    kind:  'staff'  -> a row in users
           'customer' -> a row in customers
    """
    if ttl is None:
        if token_type == 'refresh':
            days = int(current_app.config.get(REFRESH_DAYS_KEY, 30) or 30)
            ttl = timedelta(days=days)
        else:
            # Minutes, not hours. The client refreshes silently on a 401, so a
            # short access token costs the operator nothing and bounds how long
            # a leaked one is worth anything. Falls back to the older
            # JWT_ACCESS_HOURS setting if that is all the config defines.
            minutes = current_app.config.get(ACCESS_MINUTES_KEY)
            if not minutes:
                minutes = float(current_app.config.get(ACCESS_HOURS_KEY, 0.25) or 0.25) * 60
            ttl = timedelta(minutes=float(minutes))

    now = datetime.utcnow()
    payload = {
        'sub': str(subject_id),
        'kind': kind,
        'role': role,
        'typ': token_type,
        'iat': now,
        'exp': now + ttl,
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def decode_token(token):
    """Return the claims dict, or ``{'_error': '...'}`` on failure."""
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return {'_error': 'token_expired'}
    except Exception:
        return {'_error': 'token_invalid'}


def _bearer():
    raw = request.headers.get('Authorization', '') or ''
    if raw.lower().startswith('bearer '):
        return raw[7:].strip()
    # Allow ?token= for download links that cannot set a header
    return (request.args.get('token') or '').strip()


def _auth_or_401(required_kind=None):
    """
    Returns ``(claims, None)`` on success or ``(None, response)`` on failure.
    """
    token = _bearer()
    if not token:
        return None, fail('missing_token', 401)

    claims = decode_token(token)
    if '_error' in claims:
        return None, fail(claims['_error'], 401)
    if claims.get('typ') != 'access':
        return None, fail('wrong_token_type', 401)
    if required_kind and claims.get('kind') != required_kind:
        return None, fail('forbidden', 403)
    return claims, None


def _active_account(claims):
    """Load the token owner and reject deleted or disabled accounts.

    JWTs are deliberately short-lived, but relying only on their embedded
    role/status still lets a disabled account call the API until expiry.  The
    database check also makes a role change effective immediately.
    """
    try:
        account_id = int(claims.get('sub'))
    except (TypeError, ValueError):
        return None, fail('token_invalid', 401)

    try:
        from models import Customer, User, db
        model = User if claims.get('kind') == 'staff' else Customer
        account = db.session.get(model, account_id)
    except Exception:
        current_app.logger.exception('Unable to validate API token owner')
        return None, fail('authentication_unavailable', 503)

    if account is None:
        return None, fail('account_inactive', 403)

    # `is_active` bars a STAFF account and only a staff account.
    #
    # Applying it to customers too was the check that actually locked people
    # out: the login could be as permissive as it liked, but every screen in
    # the portal passes through here, so a customer whose line had been cut
    # got a 403 on the dashboard, the invoice list and the payment endpoint -
    # the three things they needed in order to become a paying customer
    # again.
    #
    # A disabled customer keeps read access to their own billing history and
    # the ability to settle it. Nothing here grants service: is_active governs
    # the connection, renewals only take effect once the money lands, and the
    # portal has no endpoint that reconnects anyone.
    if claims.get('kind') == 'staff' and not account.is_active:
        return None, fail('account_inactive', 403)
    return account, None


def _capability_check(account):
    """403 if this user's capability list does not cover this request.

    Applied here rather than as a decorator on each endpoint. Every staff
    endpoint already goes through staff_required, so one check here covers all
    of them - and the alternative, 209 decorators, fails silently on the one
    somebody forgets to add.

    A user with no capability list is unrestricted and this returns None
    immediately, so nothing changes for anybody until an administrator ticks a
    box. See blueprints/api/permissions.py.
    """
    try:
        from .permissions import check
    except Exception:                                    # pragma: no cover
        return None
    missing = check(account)
    if missing is None:
        return None
    return fail('not_permitted', 403, capability=missing,
                detail='Your account does not have permission for this. Ask '
                       'an administrator to enable it under Staff.')


def staff_required(fn):
    """Any logged-in staff user (admin/support/field/accounts)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        claims, err = _auth_or_401('staff')
        if err:
            return err
        account, err = _active_account(claims)
        if err:
            return err
        err = _capability_check(account)
        if err:
            return err
        request.jwt = claims
        request.jwt_account = account
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    """Staff user whose role is ``admin``."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        claims, err = _auth_or_401('staff')
        if err:
            return err
        account, err = _active_account(claims)
        if err:
            return err
        if account.role != 'admin':
            return fail('forbidden', 403)
        request.jwt = claims
        request.jwt_account = account
        return fn(*args, **kwargs)
    return wrapper


def customer_required(fn):
    """Used by the React Native app and the customer portal API."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        claims, err = _auth_or_401('customer')
        if err:
            return err
        account, err = _active_account(claims)
        if err:
            return err
        request.jwt = claims
        request.jwt_account = account
        return fn(*args, **kwargs)
    return wrapper


def current_staff_id():
    claims = getattr(request, 'jwt', None) or {}
    try:
        return int(claims.get('sub'))
    except (TypeError, ValueError):
        return None


def current_customer_id():
    claims = getattr(request, 'jwt', None) or {}
    try:
        return int(claims.get('sub'))
    except (TypeError, ValueError):
        return None


def money(value):
    """Decimal/None -> whole rupees, safe for JSON.

    Every amount this API returns is a round number, because this business
    does not bill in paise. It used to hand back the raw float, so a plan
    priced 3050.855 arrived at the browser as 3050.855 and appeared as
    "3050.86" inside an editable Total Price box - a number no operator typed
    and none of them could explain. Rounding only on the way out to the screen
    was not enough: the FORM fields take their value straight from here.

    ROUND_HALF_UP, not Python's default banker's rounding: 2.5 becomes 3, the
    way everyone outside a statistics department expects. Done on the Decimal
    where possible so 0.145 does not become 0.14 through a float detour.
    """
    if value is None:
        return 0.0
    try:
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        return float(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (TypeError, ValueError, InvalidOperation):
        try:
            return float(round(float(value)))
        except (TypeError, ValueError):
            return 0.0


def iso(value):
    """date/datetime -> ISO string, None-safe."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec='seconds')
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


#: The business's own timezone. Everything the operator reads as "today" is
#: this timezone, never the server's.
#:
#: Render runs its containers in UTC. `date.today()` therefore answers with the
#: UTC date, which between 00:00 and 05:30 IST is YESTERDAY in Navi Mumbai. So
#: every date-anchored screen - the dashboard's seven-day cycle, the plan expiry
#: board, "expiring today", "collected today" - silently shifted by a day for
#: five and a half hours every night, and an operator opening the app before
#: half past five saw a week that started on the wrong date. Both halves of the
#: app have to agree on which day it is, and the browser is already in IST.
APP_TIMEZONE = 'Asia/Kolkata'

#: Fallback when the container ships without a tz database. IST has no daylight
#: saving, so a fixed offset is exactly right rather than merely close.
_IST_OFFSET = timedelta(hours=5, minutes=30)


def local_now():
    """Now, in the business's timezone, as a naive datetime.

    Naive on purpose: every date column in this schema is naive, and mixing
    aware and naive values is how comparisons start raising TypeError in
    production only.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(APP_TIMEZONE)).replace(tzinfo=None)
    except Exception:
        # No tzdata on this image - use the fixed offset.
        return datetime.utcnow() + _IST_OFFSET


def today_local():
    """Today's date in the business's timezone. Use instead of date.today()."""
    return local_now().date()


def paginate(query, default_per_page=25, max_per_page=200):
    """
    Reads ``?page=`` and ``?per_page=`` and returns
    ``(rows, meta_dict)``.
    """
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.args.get('per_page', default_per_page))
    except (TypeError, ValueError):
        per_page = default_per_page
    per_page = max(1, min(per_page, max_per_page))

    total = query.order_by(None).count()
    rows = query.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    return rows, {
        'page': page,
        'per_page': per_page,
        'total': total,
        'pages': pages,
        'has_next': page < pages,
        'has_prev': page > 1,
    }


def ok(data=None, **extra):
    payload = {'ok': True}
    if data is not None:
        payload['data'] = data
    payload.update(extra)
    return jsonify(payload)


#: A dependency we call out to - WhatsApp, SMS, email, the ISP panel, the
#: payment gateway - failed or refused.
#:
#: Deliberately NOT 502. A proxy returns 502 when the APPLICATION is
#: unreachable, and the API client treats 502/503/504 exactly that way: it
#: reports "Cannot reach the server", opens a circuit breaker that fails every
#: other panel on the page for five seconds, and raises the offline banner. So
#: a single failed WhatsApp send made the whole CRM look like it had gone down,
#: and the real reason - sitting in `detail` all along - was never shown.
GATEWAY_FAILED = 424


def fail(message, status=400, **extra):
    payload = {'ok': False, 'error': message}
    payload.update(extra)
    return jsonify(payload), status


def body():
    """Request JSON, always a dict.

    Falls back to parsing the raw payload when the Content-Type header is
    missing or wrong.

    `request.get_json()` returns None unless the request says
    `Content-Type: application/json`, so a client that sends a perfectly good
    JSON body without that header gets an empty dict here - and the endpoint
    then reports the fields as missing, which is true of what it received and
    completely misleading about what was sent. Proxies, older HTTP clients,
    `fetch` without headers and mobile SDKs all do this. The body either parses
    as a JSON object or it does not; the header is not worth failing a request
    over.
    """
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data

    # Raw JSON is tried BEFORE the form, on purpose. A JSON body sent with the
    # default `application/x-www-form-urlencoded` header parses as a form with
    # one nonsense key - `{'{"username":"admin",...}': ''}` - which is truthy,
    # so a form-first order would return that instead and the endpoint would
    # still see no username. A urlencoded body is not valid JSON, so genuine
    # form posts fall through untouched.
    raw = request.get_data(cache=True, as_text=True)
    if raw:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    if request.form:
        form = request.form.to_dict()
        # A JSON body that arrived labelled as a form is parsed by Werkzeug
        # into a single key holding the whole document and an empty value.
        # (`get_data()` above cannot see it: something earlier in the request -
        # the login rate limiter reads request.form - has already consumed the
        # stream, so the raw payload is gone by the time we look.) Recognise
        # that shape and read the JSON back out of it.
        if len(form) == 1:
            only_key, only_value = next(iter(form.items()))
            if only_value == '' and only_key.lstrip().startswith('{'):
                try:
                    parsed = json.loads(only_key)
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, dict):
                    return parsed
        return form
    return {}


def enum_values(model, field):
    """The values an Enum column accepts, or None if the column is not one."""
    col = model.__mapper__.columns.get(field)
    return list(getattr(getattr(col, 'type', None), 'enums', None) or []) or None


def check_enums(model, data, fields=None):
    """Validate incoming values against the model's Enum columns.

    Returns a list of {field, message, allowed}; empty means everything is
    acceptable. Call it BEFORE assigning anything to the row.

    Worth doing by hand because neither database this runs on stops it for
    you. SQLite does not enforce an Enum at all, so a value the ORM cannot
    read back is written and committed happily - and then every subsequent
    SELECT of that table raises LookupError, which takes the whole screen down
    until somebody deletes the row by hand. MySQL outside strict mode stores
    an empty string instead, which is quieter and no better.
    """
    problems = []
    for field in (fields if fields is not None else data.keys()):
        if field not in data:
            continue
        allowed = enum_values(model, field)
        if not allowed:
            continue
        value = data[field]
        if value in (None, ''):
            continue
        if value not in allowed:
            problems.append({
                'field': field,
                'message': (f"{field.replace('_', ' ').capitalize()} must be one "
                            f"of: {', '.join(allowed)} (got \"{value}\")."),
                'allowed': allowed,
            })
    return problems


def invalid_values(problems):
    """The 400 for a check_enums() result."""
    return fail('invalid_values', 400, fields=problems,
                detail=' '.join(p['message'] for p in problems))
