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
    return (current_app.config.get('SECRET_KEY') or 'dev').encode()


def _signature(kind, ident, expires):
    message = f'{kind}:{ident}:{expires}'.encode()
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()[:32]


def sign(kind, ident, ttl=DEFAULT_TTL_SECONDS):
    """``(expires, signature)`` for one resource."""
    expires = int(time.time()) + int(ttl)
    return expires, _signature(kind, ident, expires)


def verify(kind, ident, expires, signature):
    """True when the signature matches and the link has not expired."""
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        return False

    if expires < time.time():
        return False

    # compare_digest, not ==. A plain comparison returns as soon as two bytes
    # differ, and that timing difference is enough to recover a signature one
    # character at a time.
    return hmac.compare_digest(_signature(kind, ident, expires),
                               str(signature or ''))


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
    """A ready-to-send URL for one invoice's PDF, or '' if we have no base."""
    base = public_base_url()
    if not base:
        return ''
    expires, signature = sign('invoice', invoice_id, ttl)
    query = urlencode({'exp': expires, 'sig': signature})
    return f'{base}/api/v1/public/invoices/{invoice_id}/pdf?{query}'


#: An uploaded-file link is short-lived on purpose. A bill is a document the
#: customer is meant to keep; an identity proof is one that should stop being
#: reachable as soon as whoever opened it has finished looking.
UPLOAD_TTL_SECONDS = 15 * 60


def upload_link(folder, filename, ttl=UPLOAD_TTL_SECONDS, external=False):
    """A short-lived link to one uploaded file.

    KYC documents and payment proofs were served straight out of
    ``/static/uploads/...``, which Flask hands to anyone who asks - no login,
    no expiry, forever. The stored filenames carry a random token so they were
    not trivially guessable, but "hard to guess" is not access control, and a
    URL pasted into a chat stayed live indefinitely. These are Aadhaar and PAN
    scans.

    Signed here instead, so reaching the file needs a signature that expires.
    Returns a same-origin path by default, which is what the admin screens
    need; ``external=True`` gives an absolute URL for anything that leaves the
    browser.
    """
    if not filename:
        return None
    safe_folder = str(folder).strip('/')
    name = str(filename).replace('\\', '/').split('/')[-1]
    expires, signature = sign('upload', f'{safe_folder}/{name}', ttl)
    query = urlencode({'exp': expires, 'sig': signature})
    path = f'/api/v1/public/files/{safe_folder}/{name}?{query}'
    if not external:
        return path
    base = public_base_url()
    return f'{base}{path}' if base else path


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
    query = urlencode({'exp': expires, 'sig': signature})
    return f'{base}/api/v1/public/payments/{payment_id}/receipt.pdf?{query}'
