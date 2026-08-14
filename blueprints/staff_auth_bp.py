"""
blueprints/staff_auth_bp.py
===========================

Staff "forgot password" self-service.

``templates/login.html`` had a dead ``href="#"`` behind its "Forgot password?"
link. This adds a real flow: a staff member enters their username, a one-time
code goes to the mobile number on their account, and they set a new password.

If no messaging gateway is configured the screen says so plainly and tells
them to ask an administrator to reset it from Staff -> Edit, rather than
silently doing nothing.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from flask import (current_app, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user

from models import AuditLog, User, db

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


def _hash_otp(code):
    salt = current_app.config.get('SECRET_KEY', '')
    return hashlib.sha256((salt + 'staff' + str(code)).encode()).hexdigest()


def _mask(value):
    v = (value or '').strip()
    return ('*' * max(0, len(v) - 4)) + v[-4:] if len(v) > 4 else v


def _issue(user):
    code = f'{secrets.randbelow(1000000):06d}'
    session['staff_otp'] = {
        'user_id': user.id,
        'hash': _hash_otp(code),
        'expires': (datetime.utcnow()
                    + timedelta(minutes=OTP_TTL_MINUTES)).isoformat(),
        'attempts': 0,
    }
    session.modified = True

    text = (f'{code} is your admin panel password-reset code. '
            f'It expires in {OTP_TTL_MINUTES} minutes.')

    if not user.mobile:
        return False

    try:
        from services import messaging
        for sender in (messaging.send_whatsapp, messaging.send_sms):
            result = sender(user.mobile, text, template_type='staff_otp')
            if getattr(result, 'ok', False):
                return True
    except Exception:
        current_app.logger.warning('Could not send staff OTP.')

    if current_app.debug:
        current_app.logger.warning('Staff OTP for %s is %s', user.username, code)
    return False


def _verify(code):
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
    if data.get('attempts', 0) >= OTP_MAX_ATTEMPTS:
        session.pop('staff_otp', None)
        return None, 'Too many incorrect attempts. Please start again.'

    if _hash_otp((code or '').strip()) != data.get('hash'):
        data['attempts'] = data.get('attempts', 0) + 1
        session['staff_otp'] = data
        session.modified = True
        return None, 'That code is not correct.'

    user = db.session.get(User, data['user_id'])
    session.pop('staff_otp', None)
    return (user, None) if user else (None, 'We could not find that account.')


def _audit(action, details):
    try:
        db.session.add(AuditLog(action=action, details=details,
                                ip_address=request.remote_addr))
        db.session.commit()
    except Exception:
        db.session.rollback()


# --------------------------------------------------------------------------- #
#  Views
# --------------------------------------------------------------------------- #
def staff_forgot_password():
    if getattr(current_user, 'is_authenticated', False):
        return redirect(url_for('profile'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        user = User.query.filter_by(username=username).first()

        if user and user.is_active and user.mobile:
            delivered = _issue(user)
            if delivered:
                flash(f'A verification code has been sent to '
                      f'{_mask(user.mobile)}.', 'success')
                return render_template('staff_reset_password.html',
                                       masked=_mask(user.mobile))
            flash('We could not send the code right now. Please ask an '
                  'administrator to reset your password from Staff -> Edit.',
                  'warning')
            return render_template('staff_forgot_password.html')

        # Never reveal whether the username exists.
        flash('If that account exists and has a mobile number on file, a '
              'verification code has been sent to it.', 'info')
        return render_template('staff_forgot_password.html')

    return render_template('staff_forgot_password.html')


def staff_reset_password():
    user, error = _verify(request.form.get('otp'))
    if error:
        flash(error, 'danger')
        return render_template('staff_reset_password.html', masked='')

    password = request.form.get('password') or ''
    confirm = request.form.get('confirm_password') or ''
    if password != confirm:
        flash('The two passwords do not match.', 'danger')
        return redirect(url_for('staff_forgot_password'))
    if len(password) < 8:
        flash('Please use a password of at least 8 characters.', 'danger')
        return redirect(url_for('staff_forgot_password'))

    user.set_password(password)
    db.session.commit()
    _audit('Staff Password Reset', f'{user.username} reset their password')
    flash('Your password has been changed. Please sign in.', 'success')
    return redirect(url_for('login'))


def register(app):
    app.add_url_rule('/forgot-password', 'staff_forgot_password',
                     staff_forgot_password, methods=['GET', 'POST'])
    app.add_url_rule('/reset-password', 'staff_reset_password',
                     staff_reset_password, methods=['POST'])
