"""
services/signed_links.py
========================

Links a customer can open without signing in, that nobody can guess or forge.

Why this is needed
------------------
The bill message tells a customer their invoice is ready and then gives them
no way to read it. The PDF endpoint that exists, ``/api/v1/invoices/<id>/pdf``,
requires a staff token - so the operator can see the bill and the customer
cannot.

The naive fix is to make that endpoint public, which would let anyone walk the
integers and download every invoice this business has ever issued, complete
with names, addresses and amounts. So the link carries a signature instead:

    /api/v1/public/invoices/12/pdf?exp=1786300000&sig=a3f...

``sig`` is an HMAC over the invoice id and the expiry, keyed on SECRET_KEY.
Changing either part invalidates it, the key never leaves the server, and the
link stops working on its own after a fixed window.

The expiry matters more than it looks: WhatsApp messages are forwarded, and
screenshots outlive the bill they show. A link that works forever is a slow
leak of customer data.
"""
import hashlib
import hmac
import time
from urllib.parse import urlencode

from flask import current_app

#: How long a bill link stays usable. Long enough for somebody to open it a
#: week later from an old message; short enough that a forwarded link is not a
#: permanent key to that customer's billing history.
DEFAULT_TTL_SECONDS = 30 * 24 * 3600


def _secret():
    key = current_app.config.get('SECRET_KEY')
    if not key or key in ('dev', 'dev-secret-key-change-me', 'changeme'):
        return None
    return key.encode()


def _signature(kind, ident, expires):
    secret = _secret()
    if secret is None:
        return None
    message = f'{kind}:{ident}:{expires}'.encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:32]


def sign(kind, ident, ttl=DEFAULT_TTL_SECONDS):
    """``(expires, signature)`` for one resource, or ``(0, None)`` if
    SECRET_KEY is missing/weak (links must not be forgeable).
    """
    expires = int(time.time()) + int(ttl)
    signature = _signature(kind, ident, expires)
    if signature is None:
        return 0, None
    return expires, signature


def verify(kind, ident, expires, signature):
    """True when the signature matches and the link has not expired."""
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        return False

    if expires < time.time():
        return False

    expected = _signature(kind, ident, expires)
    if expected is None:
        return False

    # compare_digest, not ==. A plain comparison returns as soon as two bytes
    # differ, and that timing difference is enough to recover a signature one
    # character at a time.
    return hmac.compare_digest(expected, str(signature or ''))


def public_base_url():
    """The address a CUSTOMER can reach, which is not always our own.

    Behind a proxy `request.url_root` is whatever the proxy passed through -
    often http://localhost:5000, which is useless in a WhatsApp message. So a
    configured value always wins.
    """
    configured = (current_app.config.get('PUBLIC_BASE_URL') or '').strip()
    if configured:
        return configured.rstrip('/')

    try:
        from flask import request
        return request.url_root.rstrip('/')
    except Exception:
        return ''


def invoice_pdf_link(invoice_id, ttl=DEFAULT_TTL_SECONDS):
    """A ready-to-send URL for one invoice's PDF, or '' if we have no base
    or no signing key.
    """
    base = public_base_url()
    if not base:
        return ''
    expires, signature = sign('invoice', invoice_id, ttl)
    if not signature:
        return ''
    query = urlencode({'exp': expires, 'sig': signature})
    return f'{base}/api/v1/public/invoices/{invoice_id}/pdf?{query}'


def receipt_pdf_link(payment_id, ttl=DEFAULT_TTL_SECONDS):
    """The same, for a payment receipt.

    Needed because WhatsApp's approved `receipt_attachment` template carries a
    DOCUMENT header, and Meta fetches that document from a public URL itself -
    it will not accept a file we hold behind a staff login. Signed the same
    way as an invoice, and with its own `kind` so an invoice signature cannot
    be replayed against a payment id.
    """
    base = public_base_url()
    if not base:
        return ''
    expires, signature = sign('receipt', payment_id, ttl)
    if not signature:
        return ''
    query = urlencode({'exp': expires, 'sig': signature})
    return f'{base}/api/v1/public/payments/{payment_id}/receipt.pdf?{query}'
