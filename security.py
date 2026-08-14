"""
security.py
===========

Cross-origin access, response headers, cookie flags and login rate limiting,
applied in one place so they cannot drift apart.

Call once, after the blueprints are registered::

    from security import harden
    harden(app)

Why this file exists
--------------------
``CORS_ORIGINS`` was read from the environment and then never used - nothing
imported flask_cors and nothing set the headers by hand. Locally that is
invisible, because Vite proxies /api to Flask so the browser sees one origin.
The moment the front end is deployed as its own service on a different host,
**every** API call fails the browser's origin check, and the failure surfaces
as an unexplained network error rather than anything pointing at CORS.

The other pieces are the ones a public deployment needs and a laptop does not:
HTTPS-only cookies, HSTS, a frame/content policy, and a lock on repeated
failed logins.
"""
import os
import time
from collections import defaultdict, deque

from flask import current_app, jsonify, request

#: Requests that may be answered without any Origin check at all.
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def _origins(app):
    raw = app.config.get('CORS_ORIGINS') or ''
    return [o.strip().rstrip('/') for o in raw.split(',') if o.strip()]


def _is_production(app):
    """Only ever True on an explicit signal.

    This used to fall back to ``not app.debug``, which looks reasonable and is
    wrong: harden() runs at import time, and Flask does not set app.debug until
    app.run(debug=True) executes much later. So a local dev server looked like
    production, the weak-secret guard below fired, app.py raised on import and
    Flask never started at all - every request answered 502.

    Guessing "production" from the absence of a flag is the wrong default when
    being wrong stops the app booting. Set FLASK_ENV=production (render.yaml
    does) to turn the strict checks on.
    """
    if os.environ.get('FLASK_ENV', '').lower() == 'production':
        return True
    # Hosts that set their own marker.
    return any(os.environ.get(k) for k in
               ('RENDER', 'RAILWAY_ENVIRONMENT', 'DYNO', 'FLY_APP_NAME'))


# --------------------------------------------------------------------------- #
#  Cross-origin access
# --------------------------------------------------------------------------- #
def _install_cors(app):
    """Answer pre-flights and echo an allowed Origin back.

    Written by hand rather than pulled in with flask-cors: the rules here are
    short, and an allow-list that is visible in the file it applies to is
    easier to audit than one assembled from decorator arguments.
    """
    allowed = _origins(app)

    @app.after_request
    def _cors_headers(response):
        origin = (request.headers.get('Origin') or '').rstrip('/')
        if not origin:
            return response

        # Echo the specific origin, never '*'. The portal's OTP flow relies on
        # the session cookie, and a wildcard is not permitted alongside
        # credentials - so a wildcard here would silently break password reset.
        if origin in allowed:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Vary'] = 'Origin'
            response.headers['Access-Control-Allow-Headers'] = (
                'Authorization, Content-Type, X-Requested-With')
            response.headers['Access-Control-Allow-Methods'] = (
                'GET, POST, PUT, PATCH, DELETE, OPTIONS')
            response.headers['Access-Control-Max-Age'] = '600'
        return response

    @app.before_request
    def _cors_preflight():
        if request.method != 'OPTIONS':
            return None
        if not request.path.startswith('/api/'):
            return None
        # An empty 204 is enough; _cors_headers above attaches the rest.
        return ('', 204)


# --------------------------------------------------------------------------- #
#  Response headers
# --------------------------------------------------------------------------- #
def _install_headers(app):
    production = _is_production(app)

    @app.after_request
    def _headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault(
            'Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault(
            'Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        response.headers.setdefault('X-XSS-Protection', '0')

        if production:
            # Only over HTTPS, and only in production - sending HSTS from a
            # local http server would pin the browser to https://localhost.
            if request.is_secure:
                response.headers.setdefault(
                    'Strict-Transport-Security',
                    'max-age=31536000; includeSubDomains')

        # A deliberately loose CSP. The app pulls Bootstrap, Font Awesome and
        # the Cashfree SDK from CDNs, so 'unsafe-inline' has to stay until
        # those are bundled; frame-ancestors and object-src are the parts
        # actually doing work here.
        response.headers.setdefault('Content-Security-Policy', '; '.join([
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net "
            "https://sdk.cashfree.com https://code.jquery.com "
            "https://stackpath.bootstrapcdn.com",
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com "
            "https://cdn.jsdelivr.net https://fonts.googleapis.com "
            "https://stackpath.bootstrapcdn.com",
            "font-src 'self' data: https://cdnjs.cloudflare.com "
            "https://fonts.gstatic.com",
            "img-src 'self' data: blob: https:",
            "connect-src 'self' " + ' '.join(_origins(app)
                                             + ['https://sdk.cashfree.com',
                                                'https://api.cashfree.com']),
            "frame-src 'self' https://sdk.cashfree.com",
            "object-src 'none'",
            "frame-ancestors 'self'",
            "base-uri 'self'",
        ]))
        return response


def _install_cookies(app):
    """Cookie flags for a public deployment."""
    production = _is_production(app)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='None' if production else 'Lax',
        SESSION_COOKIE_SECURE=production,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SECURE=production,
        # SameSite=None is required when the front end is on its own domain,
        # and browsers only accept it together with Secure - so these two are
        # set as a pair or not at all.
        PREFERRED_URL_SCHEME='https' if production else 'http',
        MAX_CONTENT_LENGTH=int(os.environ.get('MAX_UPLOAD_MB', 16)) * 1024 * 1024,
    )


# --------------------------------------------------------------------------- #
#  Login rate limiting
# --------------------------------------------------------------------------- #
#: (ip, username) -> timestamps of recent failures.
_FAILURES = defaultdict(lambda: deque(maxlen=64))

LOGIN_PATHS = ('/api/v1/auth/staff/login', '/api/v1/auth/customer/login',
               '/login', '/customer/login')

MAX_ATTEMPTS = int(os.environ.get('LOGIN_MAX_ATTEMPTS', 8))
WINDOW_SECONDS = int(os.environ.get('LOGIN_WINDOW_SECONDS', 300))
LOCK_SECONDS = int(os.environ.get('LOGIN_LOCK_SECONDS', 300))


def _client_ip():
    # Render and Railway both sit behind a proxy, so the socket address is the
    # proxy's. Trust the first hop of X-Forwarded-For and nothing beyond it.
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _key():
    identifier = ''
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        identifier = str(payload.get('username')
                         or payload.get('identifier') or '')[:64]
    else:
        identifier = str(request.form.get('username') or '')[:64]
    return (_client_ip(), identifier.lower())


def _install_rate_limit(app):
    @app.before_request
    def _guard():
        if request.method != 'POST' or request.path not in LOGIN_PATHS:
            return None

        recent = _FAILURES[_key()]
        now = time.time()
        while recent and now - recent[0] > WINDOW_SECONDS:
            recent.popleft()

        if len(recent) >= MAX_ATTEMPTS:
            wait = int(LOCK_SECONDS - (now - recent[-1]))
            if wait > 0:
                current_app.logger.warning(
                    'Login locked for %s after %d failures', _key()[0], len(recent))
                response = jsonify({
                    'ok': False,
                    'error': 'too_many_attempts',
                    'detail': f'Too many failed sign-in attempts. Try again in '
                              f'{max(1, wait // 60)} minute(s).',
                })
                response.status_code = 429
                response.headers['Retry-After'] = str(wait)
                return response
            recent.clear()
        return None

    @app.after_request
    def _record(response):
        if request.method == 'POST' and request.path in LOGIN_PATHS:
            # 401 is a wrong password; 429 is us already refusing. Only the
            # former is a fresh failure worth counting.
            if response.status_code in (400, 401, 403):
                _FAILURES[_key()].append(time.time())
            elif 200 <= response.status_code < 300:
                _FAILURES.pop(_key(), None)
        return response


# --------------------------------------------------------------------------- #
def harden(app):
    """Apply everything. Safe to call once per app."""
    if getattr(app, '_hardened', False):
        return app

    _install_cookies(app)
    _install_cors(app)
    _install_headers(app)
    _install_rate_limit(app)

    # A public deployment with the shipped development secret is not a
    # deployment, it is a liability - every session token would be forgeable
    # by anyone who has read this repository.
    if _is_production(app):
        weak = ('dev-secret-key-change-me', 'dev', 'changeme', '', None)
        bad = [name for name in ('SECRET_KEY', 'JWT_SECRET_KEY')
               if app.config.get(name) in weak]
        if bad:
            message = (f"{' and '.join(bad)} still hold the development "
                       f"default. Generate real values with: python -c "
                       f'"import secrets; print(secrets.token_hex(32))"')
            # STRICT_SECRETS=0 lets someone run a production-flagged build
            # locally without being locked out of their own app.
            if os.environ.get('STRICT_SECRETS', '1') != '0':
                raise RuntimeError(message)
            app.logger.error('INSECURE: %s', message)
        if not _origins(app):
            app.logger.warning(
                'CORS_ORIGINS is empty. If the front end is served from its '
                'own domain, its API calls will be blocked by the browser.')

    app._hardened = True
    return app
