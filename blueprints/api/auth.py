"""
blueprints/api/auth.py
======================

Authentication for the REST API.

Two audiences, two token 'kinds':
  * staff    -> React admin panel      (users table)
  * customer -> React Native app       (customers table)

Replaces the old api.py stub, whose /login printed the payload and returned
success:true without ever checking a password.
"""
from flask import Blueprint, current_app, session

from models import Customer, User, db

from .serializers import (company_branding, customer_dict, customer_plan_dict,
                          user_dict)
from .utils import (body, current_customer_id, current_staff_id,
                    customer_required, decode_token, fail, make_token, ok,
                    staff_required)

bp = Blueprint('api_auth', __name__)


# --------------------------------------------------------------------------- #
#  Staff (admin panel)
# --------------------------------------------------------------------------- #
@bp.post('/auth/staff/login')
def staff_login():
    data = body()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        # Say WHICH of the three this is. One error code covered "you sent no
        # body at all", "you left the username blank" and "you left the
        # password blank", so a client bug that posted empty strings was
        # indistinguishable from an operator mistyping - and the message
        # accused them of leaving fields blank while they were looking at
        # fields that were not.
        if not data:
            return fail('no_credentials_received', 400,
                        detail='The sign-in request arrived with no data. If '
                               'this keeps happening, reload the page.')
        missing = [name for name, value in
                   (('username', username), ('password', password)) if not value]
        return fail('username_and_password_required', 400, fields=missing,
                    detail=('Enter your ' + ' and '.join(missing) + '.'))

    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        return fail('invalid_credentials', 401)
    if not user.is_active:
        return fail('account_disabled', 403)

    return ok({
        'user': user_dict(user),
        'access_token': make_token(user.id, 'staff', user.role, token_type='access'),
        'refresh_token': make_token(user.id, 'staff', user.role, token_type='refresh'),
        'branding': company_branding(),
    })


@bp.get('/auth/staff/me')
@staff_required
def staff_me():
    user = db.session.get(User, current_staff_id())
    if not user:
        return fail('not_found', 404)
    return ok({'user': user_dict(user), 'branding': company_branding()})


@bp.put('/auth/staff/me')
@staff_required
def staff_update_me():
    """Let a signed-in staff member maintain their own contact details.

    Deliberately narrow: role and is_active are NOT writable here, otherwise
    any staff user could promote themselves to admin.
    """
    user = db.session.get(User, current_staff_id())
    if not user:
        return fail('not_found', 404)

    data = body()
    for field in ('full_name', 'email', 'mobile'):
        if field in data:
            setattr(user, field, (data[field] or '').strip() or None)

    db.session.commit()
    return ok({'user': user_dict(user), 'branding': company_branding()})


@bp.post('/auth/staff/change-password')
@staff_required
def staff_change_password():
    """Password change for the admin panel profile screen."""
    data = body()
    old_password = data.get('old_password') or ''
    new_password = data.get('new_password') or ''
    confirm = data.get('confirm_password')

    if len(new_password) < 8:
        return fail('password_too_short', 400)
    if confirm is not None and confirm != new_password:
        return fail('passwords_do_not_match', 400)

    user = db.session.get(User, current_staff_id())
    if not user:
        return fail('not_found', 404)
    if not user.check_password(old_password):
        return fail('invalid_credentials', 401)
    if user.check_password(new_password):
        return fail('password_unchanged', 400)

    user.set_password(new_password)
    db.session.commit()
    return ok({'status': 'changed'})


# --------------------------------------------------------------------------- #
#  Customer (mobile app / portal)
# --------------------------------------------------------------------------- #
@bp.post('/auth/customer/login')
def customer_login():
    data = body()
    identifier = (data.get('identifier')
                  or data.get('username')
                  or data.get('mobile') or '').strip()
    password = data.get('password') or ''

    if not identifier or not password:
        return fail('identifier_and_password_required', 400)

    customer = (Customer.query.filter_by(username=identifier).first()
                or Customer.query.filter_by(mobile=identifier).first()
                or Customer.query.filter_by(reference_id=identifier).first())

    if not customer or not customer.password_hash \
            or not customer.check_password(password):
        return fail('invalid_credentials', 401)

    # A disabled, terminated or expired customer CAN still sign in.
    #
    # This used to answer 403 account_disabled, which locked people out of the
    # portal at the exact moment they needed it: somebody whose line was cut
    # for non-payment could no longer see what they owed, download the bill,
    # or pay it - so the one route back to being a paying customer was closed
    # by the same event that made them stop paying. They rang the office
    # instead, which is the outcome the portal exists to avoid.
    #
    # is_active governs the CONNECTION, not the login. Nothing in the portal
    # grants service: every screen is their own billing history, and renewing
    # raises an invoice that only takes effect once it is paid.

    active = next((cp for cp in customer.plans if cp.status == 'active'), None)

    return ok({
        'customer': customer_dict(customer, detail=True),
        'active_plan': customer_plan_dict(active),
        'access_token': make_token(customer.id, 'customer', token_type='access'),
        'refresh_token': make_token(customer.id, 'customer', token_type='refresh'),
        'branding': company_branding(),
    })


@bp.get('/auth/customer/me')
@customer_required
def customer_me():
    customer = db.session.get(Customer, current_customer_id())
    if not customer:
        return fail('not_found', 404)
    active = next((cp for cp in customer.plans if cp.status == 'active'), None)
    return ok({
        'customer': customer_dict(customer, detail=True),
        'active_plan': customer_plan_dict(active),
        'branding': company_branding(),
    })


@bp.post('/auth/customer/change-password')
@customer_required
def customer_change_password():
    data = body()
    old_password = data.get('old_password') or ''
    new_password = data.get('new_password') or ''

    if len(new_password) < 8:
        return fail('password_too_short', 400)

    customer = db.session.get(Customer, current_customer_id())
    if not customer:
        return fail('not_found', 404)
    if not customer.password_hash or not customer.check_password(old_password):
        return fail('invalid_credentials', 401)

    customer.set_password(new_password)
    db.session.commit()
    return ok({'status': 'changed'})


@bp.post('/auth/customer/forgot-password')
def customer_forgot_password():
    """Issue a portal-reset OTP without leaking whether an account exists."""
    identifier = (body().get('identifier') or '').strip()
    if not identifier:
        return fail('identifier_required', 400)

    from blueprints.portal_bp import _find_customer, _issue_otp
    # Deliberately not filtered by is_active. A disabled customer can still
    # sign in (see customer_login), so they must still be able to recover a
    # forgotten password - otherwise the account they can reach is one they
    # cannot get back into.
    customer = _find_customer(identifier)
    if customer:
        _issue_otp(customer, 'reset_password')

    # Always return the same response to prevent account enumeration.
    return ok({'status': 'if_the_account_exists_an_otp_was_sent'})


@bp.post('/auth/customer/reset-password')
def customer_reset_password():
    data = body()
    password = data.get('password') or ''
    if len(password) < 8:
        return fail('password_too_short', 400)

    from blueprints.portal_bp import _verify_otp
    customer, error = _verify_otp(data.get('otp'), 'reset_password')
    if error:
        return fail('otp_invalid', 400, detail=error)

    customer.set_password(password)
    db.session.commit()
    return ok({'status': 'password_reset'})


# --------------------------------------------------------------------------- #
#  Staff password reset
#
#  Until now there was none. If an administrator forgot their password there
#  was no way back into the system short of editing the database by hand - the
#  Jinja screens that offered this are no longer reachable, and the REST API
#  never carried an equivalent. That is a lockout, not an inconvenience.
#
#  The OTP lives in the Flask session, exactly like the customer flow, so the
#  code never travels back to the client and cannot be replayed from a log.
# --------------------------------------------------------------------------- #
STAFF_OTP_TTL_MINUTES = 10
STAFF_OTP_MAX_ATTEMPTS = 5


def _staff_otp_hash(code):
    import hashlib
    salt = current_app.config.get('SECRET_KEY', '')
    return hashlib.sha256((salt + 'staff' + str(code)).encode()).hexdigest()


def _mask_mobile(value):
    """Show the last four digits, so the operator can tell which phone."""
    v = (value or '').strip()
    return ('*' * max(0, len(v) - 4)) + v[-4:] if len(v) > 4 else v


def _issue_staff_otp(user):
    """Put a fresh code in the session and try to deliver it. True if sent."""
    import secrets
    from datetime import datetime, timedelta

    code = f'{secrets.randbelow(1000000):06d}'
    session['staff_otp'] = {
        'user_id': user.id,
        'hash': _staff_otp_hash(code),
        'expires': (datetime.utcnow()
                    + timedelta(minutes=STAFF_OTP_TTL_MINUTES)).isoformat(),
        'attempts': 0,
    }
    session.modified = True

    if not user.mobile:
        return False

    text = (f'{code} is your admin panel password-reset code. '
            f'It expires in {STAFF_OTP_TTL_MINUTES} minutes.')
    try:
        from services import messaging
        for sender in (messaging.send_whatsapp, messaging.send_sms):
            result = sender(user.mobile, text, template_type='staff_otp')
            # A dry-run is not a delivery. Saying "sent" when the gateway is
            # switched off leaves someone waiting for a code that never comes.
            if getattr(result, 'ok', False) \
                    and getattr(result, 'status', '') == 'sent':
                return True
    except Exception:
        current_app.logger.warning('Could not send the staff OTP.')

    if current_app.debug:
        current_app.logger.warning('Staff OTP for %s is %s', user.username, code)
    return False


def _verify_staff_otp(code):
    """(user, None) on success, (None, reason) otherwise."""
    from datetime import datetime

    data = session.get('staff_otp')
    if not data:
        return None, 'Please request a new code.'

    try:
        expires = datetime.fromisoformat(data['expires'])
    except (KeyError, ValueError):
        return None, 'Please request a new code.'

    if datetime.utcnow() > expires:
        session.pop('staff_otp', None)
        return None, 'That code has expired. Please request a new one.'

    if data.get('attempts', 0) >= STAFF_OTP_MAX_ATTEMPTS:
        session.pop('staff_otp', None)
        return None, 'Too many incorrect attempts. Please start again.'

    if _staff_otp_hash((code or '').strip()) != data.get('hash'):
        data['attempts'] = data.get('attempts', 0) + 1
        session['staff_otp'] = data
        session.modified = True
        remaining = STAFF_OTP_MAX_ATTEMPTS - data['attempts']
        return None, (f'That code is not correct. '
                      f'{remaining} attempt(s) left.' if remaining > 0
                      else 'Too many incorrect attempts. Please start again.')

    user = db.session.get(User, data['user_id'])
    session.pop('staff_otp', None)
    if user is None:
        return None, 'We could not find that account.'
    return user, None


@bp.post('/auth/staff/forgot-password')
def staff_forgot_password():
    """
    Send a reset code to the mobile on the staff account.

    The response never says whether the username exists - that would turn this
    endpoint into a way to enumerate staff accounts. It does distinguish
    "we sent it" from "we could not send it", because a member of staff
    standing there waiting needs to know to ask an admin instead.
    """
    username = (body().get('username') or '').strip()
    if not username:
        return fail('username_required', 400,
                    detail='Enter your username.')

    user = User.query.filter_by(username=username).first()
    delivered = False
    masked = ''

    if user and user.is_active and user.mobile:
        delivered = _issue_staff_otp(user)
        masked = _mask_mobile(user.mobile)

    if delivered:
        return ok({'status': 'sent', 'masked_mobile': masked,
                   'expires_in_minutes': STAFF_OTP_TTL_MINUTES})

    return ok({
        'status': 'not_sent',
        'detail': 'If that account exists and has a mobile number on file, a '
                  'code has been sent. If nothing arrives, ask an '
                  'administrator to reset your password from Staff - Edit.',
    })


@bp.post('/auth/staff/reset-password')
def staff_reset_password():
    """Set a new password against the code sent to the staff member's phone."""
    data = body()
    password = data.get('password') or ''
    if len(password) < 8:
        return fail('password_too_short', 400,
                    detail='Use at least 8 characters.')

    user, error = _verify_staff_otp(data.get('otp'))
    if error:
        return fail('otp_invalid', 400, detail=error)

    user.set_password(password)
    db.session.commit()

    try:
        from app import log_audit
        log_audit('Staff Password Reset',
                  f'{user.username} reset their own password by OTP')
    except Exception:
        pass

    return ok({'status': 'password_reset', 'username': user.username})


# --------------------------------------------------------------------------- #
#  Token lifecycle
# --------------------------------------------------------------------------- #
@bp.post('/auth/refresh')
def refresh():
    data = body()
    token = (data.get('refresh_token') or '').strip()
    if not token:
        return fail('missing_refresh_token', 400)

    claims = decode_token(token)
    if '_error' in claims:
        return fail(claims['_error'], 401)
    if claims.get('typ') != 'refresh':
        return fail('wrong_token_type', 401)

    kind = claims.get('kind')
    try:
        subject_id = int(claims.get('sub'))
    except (TypeError, ValueError):
        return fail('token_invalid', 401)

    if kind == 'staff':
        user = db.session.get(User, subject_id)
        if not user or not user.is_active:
            return fail('account_disabled', 403)
        access = make_token(user.id, 'staff', user.role, token_type='access')
    elif kind == 'customer':
        # No is_active check, matching the login above: a customer whose line
        # is cut keeps their portal session. Refusing here would have signed
        # them out the moment their access token expired, which is a slower
        # and more confusing version of the lockout the login no longer does.
        customer = db.session.get(Customer, subject_id)
        if not customer:
            return fail('token_invalid', 401)
        access = make_token(customer.id, 'customer', token_type='access')
    else:
        return fail('token_invalid', 401)

    return ok({'access_token': access})


@bp.post('/auth/logout')
def logout():
    """Stateless JWT - the client drops the token. Here for API symmetry."""
    return ok({'status': 'logged_out'})
