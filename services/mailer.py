"""
services/mailer.py
==================

Email delivery with attachments, configured the same way the WhatsApp gateway
is: settings row first, environment variable second, built-in default last.

Two delivery paths are supported:

  * SMTP  - a mail host over port 587 (STARTTLS) or 465 (SSL). Requires the
    host to accept connections from where the app runs.
  * Resend - a plain HTTPS POST to https://api.resend.com/emails. No SMTP port
    to open, so it keeps working on hosts that block outbound SMTP (free
    Render instances in particular). A free resend.com account is enough for
    the volumes this app sends.

Why this exists: ``send_email()`` in app.py is a stub that writes a line to the
application log and returns. Anything calling it reported success while the
customer received nothing. A screen that says "invoice emailed" when no mail
was sent is worse than one with no email button at all, because the operator
stops chasing the customer.

So this module never claims more than it did. With neither SMTP nor a Resend
key configured it returns status ``dry-run`` - the same word the WhatsApp path
uses - and the API passes that back so the UI can say the mail was logged, not
sent. The chosen path's raw error is returned verbatim so the Settings screen
and logs can tell you exactly what the provider refused.
"""
import base64
import json
import mimetypes
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

#: setting key -> default. Mirrors messaging.DEFAULTS in shape and lookup order.
DEFAULTS = {
    'mail_enabled': '0',
    'mail_provider': 'smtp',       # smtp | resend
    'mail_host': '',
    'mail_port': '587',
    'mail_username': '',
    'mail_password': '',
    'mail_from': '',
    'mail_from_name': '',
    'mail_use_tls': '1',
    'mail_use_ssl': '0',
    'mail_timeout': '20',
    'resend_api_key': '',
}

RESEND_ENDPOINT = 'https://api.resend.com/emails'


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


def _provider():
    return (_setting('mail_provider') or 'smtp').strip().lower()


def is_configured():
    """True when there is enough to attempt a real send."""
    if not _flag('mail_enabled'):
        return False
    if _provider() == 'resend':
        return bool((_setting('resend_api_key') or '').strip())
    return bool((_setting('mail_host') or '').strip())


def _from_address():
    address = (_setting('mail_from') or _setting('mail_username') or '').strip()
    name = (_setting('mail_from_name') or '').strip()
    return f'{name} <{address}>' if name and address else address


def _send_via_resend(to, subject, body, html, attachments, sender):
    """One HTTPS POST to the Resend API. Never raises."""
    api_key = (_setting('resend_api_key') or '').strip()
    if not api_key:
        return MailResult(True, 'dry-run',
                          'Resend is selected but no API key is set, so the '
                          'message was not sent.', to, subject)

    payload = {'from': sender, 'to': [to], 'subject': subject, 'text': body or ''}
    if html:
        payload['html'] = html

    attached = []
    for item in attachments or []:
        try:
            filename, data, _mime = (list(item) + [None])[:3]
        except (TypeError, ValueError):
            continue
        if not data:
            continue
        attached.append({
            'filename': filename or 'attachment',
            'content': base64.b64encode(data).decode('ascii'),
        })
    if attached:
        payload['attachments'] = attached

    req = urllib.request.Request(
        RESEND_ENDPOINT,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return MailResult(True, 'sent', '', to, subject)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', 'replace')[:500]
        detail = f'Resend refused the request (HTTP {exc.code}).'
        try:
            message = json.loads(raw).get('message') or \
                json.loads(raw).get('error', '')
            if message:
                detail += f' {message}'
        except Exception:                               # noqa: BLE001
            pass
        return MailResult(False, 'failed', detail[:200], to, subject)
    except Exception as exc:                            # noqa: BLE001
        return MailResult(False, 'failed',
                          f'{type(exc).__name__}: {exc}'[:200], to, subject)


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
        provider = _provider()
        if provider == 'resend':
            hint = 'Add the Resend API key under Settings → Outgoing email.'
        else:
            hint = 'Add the mail settings under Settings.'
        return MailResult(True, 'dry-run',
                          f'Outgoing email is not configured, so the message '
                          f'was not sent. {hint}', to, subject)

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

    if _provider() == 'resend':
        return _send_via_resend(to, subject, body, html, attachments, sender)

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
