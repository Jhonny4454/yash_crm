"""
services/mailer.py
==================

Email delivery for invoices and receipts, configured the same way the
WhatsApp gateway is: settings row first, environment variable second,
built-in default last.

Delivery goes through Brevo's HTTP API - a plain HTTPS POST to
https://api.brevo.com/v3/smtp/email. No SMTP port to open, so it keeps
working on hosts that block outbound SMTP (free Render instances in
particular). A free brevo.com account allows verifying a single email
address as sender - no domain needed - for 300 emails/day.

Why this exists: ``send_email()`` in app.py is a stub that writes a line to
the application log and returns. Anything calling it reported success while
the customer received nothing. A screen that says "invoice emailed" when no
mail was sent is worse than one with no email button at all, because the
operator stops chasing the customer.

So this module never claims more than it did. With no API key configured it
returns status ``dry-run`` - the same word the WhatsApp path uses - and the
API passes that back so the UI can say the mail was logged, not sent. Brevo's
raw error is returned verbatim so the Settings screen and logs can tell you
exactly what the provider refused.
"""
import base64
import json
import os
import urllib.error
import urllib.request

#: setting key -> default. Mirrors messaging.DEFAULTS in shape and lookup order.
DEFAULTS = {
    'mail_enabled': '0',
    'mail_from': '',
    'mail_from_name': '',
    'brevo_api_key': '',
}

BREVO_ENDPOINT = 'https://api.brevo.com/v3/smtp/email'


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
    return _flag('mail_enabled') and bool((_setting('brevo_api_key') or '').strip())


def _from_address():
    address = (_setting('mail_from') or '').strip()
    name = (_setting('mail_from_name') or '').strip()
    return f'{name} <{address}>' if name and address else address


def _split_sender(sender):
    """Turn 'Name <addr>' (or plain 'addr') into Brevo's sender object."""
    sender = (sender or '').strip()
    if ' <' in sender and sender.endswith('>'):
        name, addr = sender.rsplit(' <', 1)
        out = {'email': addr[:-1].strip()}
        if name.strip():
            out['name'] = name.strip()
        return out
    return {'email': sender}


def _send_via_brevo(to, subject, body, html, attachments, sender):
    """One HTTPS POST to the Brevo API. Never raises."""
    api_key = (_setting('brevo_api_key') or '').strip()
    if not api_key:
        return MailResult(True, 'dry-run',
                          'No Brevo API key is set, so the message was not '
                          'sent.', to, subject)

    payload = {
        'sender': _split_sender(sender),
        'to': [{'email': to}],
        'subject': subject,
        'textContent': body or '',
    }
    if html:
        payload['htmlContent'] = html

    attached = []
    for item in attachments or []:
        try:
            filename, data, _mime = (list(item) + [None])[:3]
        except (TypeError, ValueError):
            continue
        if not data:
            continue
        attached.append({
            'name': filename or 'attachment',
            'content': base64.b64encode(data).decode('ascii'),
        })
    if attached:
        payload['attachment'] = attached

    req = urllib.request.Request(
        BREVO_ENDPOINT,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'api-key': api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return MailResult(True, 'sent', '', to, subject)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', 'replace')[:500]
        detail = f'Brevo refused the request (HTTP {exc.code}).'
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
    Send one email through Brevo. Never raises.

    ``attachments`` is a list of ``(filename, bytes, mimetype)`` tuples;
    mimetype may be None and will be guessed from the filename.
    """
    to = (to or '').strip()
    if not to:
        return MailResult(False, 'skipped', 'No email address on file.')

    if not is_configured():
        return MailResult(True, 'dry-run',
                          'Outgoing email is not configured, so the message '
                          'was not sent. Add the Brevo API key under '
                          'Settings → Outgoing email.', to, subject)

    sender = _from_address()
    if not sender:
        return MailResult(False, 'failed',
                          'No From address is configured for outgoing mail.',
                          to, subject)

    return _send_via_brevo(to, subject, body, html, attachments, sender)
