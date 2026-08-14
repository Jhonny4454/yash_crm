"""
services/mailer.py
==================

SMTP delivery with attachments, configured the same way the WhatsApp gateway
is: settings row first, environment variable second, built-in default last.

Why this exists: ``send_email()`` in app.py is a stub that writes a line to the
application log and returns. Anything calling it reported success while the
customer received nothing. A screen that says "invoice emailed" when no mail
was sent is worse than one with no email button at all, because the operator
stops chasing the customer.

So this module never claims more than it did. With no SMTP host configured it
returns status ``dry-run`` - the same word the WhatsApp path uses - and the API
passes that back so the UI can say the mail was logged, not sent.
"""
import mimetypes
import os
import smtplib
from email.message import EmailMessage

#: setting key -> default. Mirrors messaging.DEFAULTS in shape and lookup order.
DEFAULTS = {
    'mail_enabled': '0',
    'mail_host': '',
    'mail_port': '587',
    'mail_username': '',
    'mail_password': '',
    'mail_from': '',
    'mail_from_name': '',
    'mail_use_tls': '1',
    'mail_use_ssl': '0',
    'mail_timeout': '20',
}


class MailResult:
    """Mirrors messaging.SendResult so callers can treat the two alike."""

    def __init__(self, ok, status, detail='', to='', subject=''):
        self.ok = ok
        self.status = status          # sent | dry-run | skipped | failed
        self.detail = detail
        self.to = to
        self.subject = subject

    def __bool__(self):
        return bool(self.ok)

    def __repr__(self):
        return f'<MailResult {self.status} to={self.to!r} {self.detail[:60]!r}>'


def _setting(key, default=''):
    try:
        from models_ext import Setting
        row = Setting.query.filter_by(key=key).first()
        if row is not None and (row.value or '').strip() != '':
            return row.value
    except Exception:
        pass
    env = os.environ.get(key.upper())
    if env:
        return env
    return DEFAULTS.get(key, default)


def _flag(key):
    return str(_setting(key)).strip().lower() in ('1', 'true', 'yes', 'on')


def is_configured():
    """True when there is enough to attempt a real send."""
    return _flag('mail_enabled') and bool((_setting('mail_host') or '').strip())


def _from_address():
    address = (_setting('mail_from') or _setting('mail_username') or '').strip()
    name = (_setting('mail_from_name') or '').strip()
    return f'{name} <{address}>' if name and address else address


def send_email(to, subject, body, attachments=None, html=None):
    """
    Send one email. Never raises.

    ``attachments`` is a list of ``(filename, bytes, mimetype)`` tuples;
    mimetype may be None and will be guessed from the filename.
    """
    to = (to or '').strip()
    if not to:
        return MailResult(False, 'skipped', 'No email address on file.')

    if not is_configured():
        return MailResult(True, 'dry-run',
                          'SMTP is not configured, so the message was not sent. '
                          'Add the mail settings under Settings.', to, subject)

    sender = _from_address()
    if not sender:
        return MailResult(False, 'failed',
                          'No From address is configured for outgoing mail.',
                          to, subject)

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = sender
    message['To'] = to
    message.set_content(body or '')
    if html:
        message.add_alternative(html, subtype='html')

    for item in attachments or []:
        try:
            filename, payload, mimetype = (list(item) + [None])[:3]
        except (TypeError, ValueError):
            continue
        if not payload:
            continue
        if not mimetype:
            mimetype = mimetypes.guess_type(filename or '')[0] \
                or 'application/octet-stream'
        maintype, _, subtype = mimetype.partition('/')
        message.add_attachment(payload, maintype=maintype,
                               subtype=subtype or 'octet-stream',
                               filename=filename or 'attachment')

    host = _setting('mail_host')
    port = int(_setting('mail_port') or 587)
    timeout = int(_setting('mail_timeout') or 20)
    username = _setting('mail_username')
    password = _setting('mail_password')

    try:
        if _flag('mail_use_ssl'):
            server = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)

        with server:
            server.ehlo()
            if _flag('mail_use_tls') and not _flag('mail_use_ssl'):
                server.starttls()
                server.ehlo()
            if username:
                server.login(username, password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        return MailResult(False, 'failed',
                          f'The mail server rejected the login: {exc}'[:200],
                          to, subject)
    except Exception as exc:
        return MailResult(False, 'failed',
                          f'{type(exc).__name__}: {exc}'[:200], to, subject)

    return MailResult(True, 'sent', '', to, subject)
