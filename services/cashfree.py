"""
services/cashfree.py
====================
Cashfree Payment Gateway integration (PG API version 2023-08-01).

Flow used by the customer portal
--------------------------------
1.  Customer clicks "Pay & Renew"  ->  `create_order()` is called server-side.
2.  Cashfree returns a `payment_session_id`; the browser hands that to the
    Cashfree JS SDK, which opens the hosted checkout.
3.  Customer pays; Cashfree redirects back to `/customer/payment/return`.
4.  We call `fetch_order()` **server-side** to confirm the status. We never
    trust the browser redirect on its own.
5.  Cashfree also POSTs a webhook to `/webhooks/cashfree`; `verify_webhook()`
    checks the HMAC signature so a forged request cannot mark an order paid.

Configuration (Settings -> Payment Gateway, or environment variables)
--------------------------------------------------------------------
    CASHFREE_APP_ID          Client ID from the Cashfree dashboard
    CASHFREE_SECRET_KEY      Client Secret from the Cashfree dashboard
    CASHFREE_ENV             'sandbox' (default) or 'production'

Leave the credentials blank and online payment is simply disabled: the portal
hides the Pay button instead of throwing errors.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


API_VERSION = '2023-08-01'
BASE_URLS = {
    'sandbox':    'https://sandbox.cashfree.com/pg',
    'production': 'https://api.cashfree.com/pg',
}
#: Front-end SDK, injected into the portal template
SDK_URLS = {
    'sandbox':    'https://sdk.cashfree.com/js/v3/cashfree.js',
    'production': 'https://sdk.cashfree.com/js/v3/cashfree.js',
}


# --------------------------------------------------------------------------- #
#  Configuration helpers
# --------------------------------------------------------------------------- #
def _setting(key, default=''):
    try:
        from models_ext import Setting
        row = Setting.query.filter_by(key=key).first()
        if row is not None and (row.value or '').strip():
            return row.value.strip()
    except Exception:
        pass
    return os.environ.get(key.upper(), default)


def app_id():
    return _setting('cashfree_app_id')


def secret_key():
    return _setting('cashfree_secret_key')


def environment():
    env = (_setting('cashfree_env', 'sandbox') or 'sandbox').lower()
    return 'production' if env.startswith('prod') else 'sandbox'


def base_url():
    return BASE_URLS[environment()]


def sdk_url():
    return SDK_URLS[environment()]


def is_configured() -> bool:
    """True when both credentials are present, so the Pay button can show."""
    return bool(app_id() and secret_key() and requests is not None)


def credential_env() -> str:
    """Which environment the credentials on file BELONG to, read off the keys.

    Cashfree stamps this into the values themselves, in both key generations:

        Client ID   sandbox 'TEST430329ae...'   production '430329ae...'
        Secret      sandbox 'cfsk_ma_test_...'  production 'cfsk_ma_prod_...'

    Returns 'sandbox', 'production', or '' when the format is not recognised
    (an old key, or a format Cashfree has since changed).
    """
    app = (app_id() or '').lower()
    secret = (secret_key() or '').lower()
    if app.startswith('test') or '_test_' in secret or secret.startswith('test'):
        return 'sandbox'
    if '_prod_' in secret:
        return 'production'
    return ''


def config_problem() -> str:
    """The reason Cashfree will refuse us, in words, or '' when it will not.

    This exists because Cashfree's own answer to every credential fault is the
    string "authentication Failed" - no code, no hint, nothing to act on. The
    portal relayed that verbatim and the operator had a payment page that said
    two words and stopped.

    The common cause by a distance is an environment mismatch: production keys
    saved while the environment is still on Sandbox (which is the default, and
    what an unset CASHFREE_ENV falls back to), or sandbox keys left behind
    after going live. Sandbox credentials are rejected by the production host
    and vice versa, and the message is the same either way.
    """
    if requests is None:
        return ('The `requests` package is not installed on the server, so the '
                'payment gateway cannot be reached at all.')
    if not app_id() and not secret_key():
        return ('No Cashfree credentials are saved. Add them in '
                'Settings -> Payment Gateway.')
    if not app_id():
        return ('The Cashfree App ID is blank. Copy the Client ID from '
                'Cashfree -> Developers -> API Keys into Settings -> Payment '
                'Gateway.')
    if not secret_key():
        return ('The Cashfree secret key is blank. Copy the Client Secret from '
                'Cashfree -> Developers -> API Keys into Settings -> Payment '
                'Gateway.')

    belongs = credential_env()
    running = environment()
    if belongs and belongs != running:
        return (f'The saved credentials are {belongs.upper()} keys but the '
                f'gateway is set to {running.upper()}, so Cashfree rejects '
                f'them. Either change "Cashfree environment" to '
                f'{belongs.title()} in Settings -> Payment Gateway, or paste '
                f'the {running.title()} keys from Cashfree -> Developers -> '
                f'API Keys.')
    return ''


def _auth_detail() -> str:
    """Appended to Cashfree's own refusal, so it says something useful."""
    app = app_id() or ''
    shown = f'{app[:6]}…{app[-4:]}' if len(app) > 12 else (app or '(blank)')
    return (f' Cashfree refused the credentials for the {environment()} '
            f'environment ({base_url()}); the App ID on file is {shown}. '
            f'Check Settings -> Payment Gateway: sandbox keys only work '
            f'against Sandbox and production keys only against Production, '
            f'and a key that has been regenerated in the Cashfree dashboard '
            f'stops working the moment it is replaced.')


def _is_auth_failure(status, message) -> bool:
    return status in (401, 403) or 'authentication' in str(message or '').lower()


def check_credentials() -> dict:
    """Ask Cashfree whether these keys work, without taking any money.

    Fetching an order id that cannot exist is enough: a 404 means we were
    authenticated and the order simply is not there, which is exactly the
    answer we want. Anything about authentication means the keys are wrong.
    """
    problem = config_problem()
    if problem:
        return {'ok': False, 'environment': environment(), 'detail': problem}

    probe = f'PING-{secrets.token_hex(6).upper()}'
    try:
        resp = requests.get(f'{base_url()}/orders/{probe}',
                            headers=_headers(), timeout=15)
    except Exception as exc:
        return {'ok': False, 'environment': environment(),
                'detail': f'Could not reach Cashfree: {exc}'}

    data = _json(resp)
    message = data.get('message') or ''
    if _is_auth_failure(resp.status_code, message):
        return {'ok': False, 'environment': environment(),
                'detail': f'{message}.{_auth_detail()}'}
    if resp.status_code in (200, 404):
        return {'ok': True, 'environment': environment(),
                'detail': f'Cashfree accepted these {environment()} '
                          f'credentials.'}
    return {'ok': False, 'environment': environment(),
            'detail': message or f'Cashfree returned HTTP {resp.status_code}.'}


def _headers():
    return {
        'accept': 'application/json',
        'content-type': 'application/json',
        'x-api-version': API_VERSION,
        'x-client-id': app_id(),
        'x-client-secret': secret_key(),
    }


class CashfreeError(RuntimeError):
    """Raised for any non-2xx response or transport failure."""


# --------------------------------------------------------------------------- #
#  Order lifecycle
# --------------------------------------------------------------------------- #
def new_order_id(prefix='YIS') -> str:
    """A collision-resistant, human-readable order reference."""
    return f"{prefix}-{datetime.utcnow():%Y%m%d%H%M%S}-{secrets.token_hex(3).upper()}"


def create_order(*, order_id, amount, customer_id, customer_phone,
                 customer_name='', customer_email='', return_url='',
                 notify_url='', note='') -> dict:
    """
    Create a Cashfree order and return the parsed JSON response.

    The important key in the response is `payment_session_id`, which the
    browser passes to the Cashfree JS SDK to open checkout.
    """
    # Say what is wrong BEFORE the round trip, so the operator gets the actual
    # fault rather than Cashfree's two-word refusal relayed through a 424.
    problem = config_problem()
    if problem:
        raise CashfreeError(problem)

    amount = round(float(amount), 2)
    if amount <= 0:
        raise CashfreeError('Order amount must be greater than zero.')

    payload = {
        'order_id': order_id,
        'order_amount': amount,
        'order_currency': 'INR',
        'customer_details': {
            # Cashfree rejects customer_id values with spaces or symbols
            'customer_id': f"CUST{customer_id}",
            'customer_phone': _clean_phone(customer_phone),
            'customer_name': (customer_name or '')[:100],
            'customer_email': (customer_email or '')[:100],
        },
        'order_meta': {},
        'order_note': (note or '')[:200],
    }
    if return_url:
        payload['order_meta']['return_url'] = return_url
    if notify_url:
        payload['order_meta']['notify_url'] = notify_url

    try:
        resp = requests.post(f"{base_url()}/orders", json=payload,
                             headers=_headers(), timeout=20)
    except Exception as exc:
        raise CashfreeError(f"Could not reach Cashfree: {exc}") from exc

    data = _json(resp)
    if resp.status_code >= 300:
        message = data.get('message') or f"HTTP {resp.status_code}"
        if _is_auth_failure(resp.status_code, message):
            raise CashfreeError(f'{message}.{_auth_detail()}')
        raise CashfreeError(message)
    if not data.get('payment_session_id'):
        raise CashfreeError('Cashfree did not return a payment session.')
    return data


def fetch_order(order_id) -> dict:
    """Server-side status check. Returns the order object from Cashfree."""
    if not is_configured():
        raise CashfreeError('Cashfree is not configured.')
    try:
        resp = requests.get(f"{base_url()}/orders/{order_id}",
                            headers=_headers(), timeout=20)
    except Exception as exc:
        raise CashfreeError(f"Could not reach Cashfree: {exc}") from exc
    data = _json(resp)
    if resp.status_code >= 300:
        raise CashfreeError(data.get('message') or f"HTTP {resp.status_code}")
    return data


def fetch_payments(order_id) -> list:
    """All payment attempts for an order (used to pull the bank/UPI ref no)."""
    if not is_configured():
        return []
    try:
        resp = requests.get(f"{base_url()}/orders/{order_id}/payments",
                            headers=_headers(), timeout=20)
        data = _json(resp)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def successful_payment(order_id) -> dict | None:
    """Return the first SUCCESS payment for an order, or None."""
    for p in fetch_payments(order_id):
        if str(p.get('payment_status', '')).upper() == 'SUCCESS':
            return p
    return None


def is_paid(order_data: dict) -> bool:
    return str(order_data.get('order_status', '')).upper() == 'PAID'


# --------------------------------------------------------------------------- #
#  Webhook verification
# --------------------------------------------------------------------------- #
def verify_webhook(raw_body: bytes | str, signature: str, timestamp: str) -> bool:
    """
    Verify Cashfree's `x-webhook-signature`.

    Signature = base64( HMAC_SHA256( timestamp + raw_body, secret_key ) )

    Always use the *raw* request body — re-serialising the parsed JSON changes
    the bytes and the signature will never match.
    """
    if not (signature and timestamp and secret_key()):
        return False
    if isinstance(raw_body, bytes):
        raw_body = raw_body.decode('utf-8', 'replace')
    signed = f"{timestamp}{raw_body}".encode('utf-8')
    digest = hmac.new(secret_key().encode('utf-8'), signed, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode('utf-8')
    return hmac.compare_digest(expected, signature)


# --------------------------------------------------------------------------- #
#  Internals
# --------------------------------------------------------------------------- #
def _json(resp):
    try:
        return resp.json()
    except Exception:
        return {'message': (resp.text or '')[:300]}


def _clean_phone(raw):
    """Cashfree wants 10 digits for Indian numbers, no country code."""
    digits = ''.join(ch for ch in str(raw or '') if ch.isdigit())
    if digits.startswith('91') and len(digits) == 12:
        digits = digits[2:]
    return digits[-10:] if len(digits) >= 10 else digits
