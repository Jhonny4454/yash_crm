"""
services/cloudinary.py
======================

Cloudinary configuration helpers, credential check, and upload.

Credentials can come from two places, in this order of precedence:

  1. The ``CLOUDINARY_URL`` environment variable, set on the hosting
     platform (e.g. Render).  Format::

         cloudinary://API_KEY:API_SECRET@CLOUD_NAME

  2. The ``cloudinary_*`` rows edited on the Settings screen.

An env var set at deploy time is a platform-level decision and wins over
anything typed in the UI - the whole reason it exists is that a redeploy can
silently drop a config that was only ever sitting in a text field, and the
company logo with it.
"""
import base64
import json
import os
import tempfile
import urllib.error
import urllib.request
from io import BytesIO

URL_PREFIX = 'cloudinary://'
ADMIN_BASE = 'https://api.cloudinary.com/v1_1'
UPLOAD_BASE = f'{ADMIN_BASE}'


def cloudinary_url():
    return (os.environ.get('CLOUDINARY_URL') or '').strip() or None


def parse_cloudinary_url(url):
    """``cloudinary://KEY:SECRET@CLOUD`` -> (cloud_name, api_key, api_secret).

    Returns None for anything that is not a usable Cloudinary URL.
    """
    if not url:
        return None
    rest = url.split(URL_PREFIX, 1)[-1]
    if '@' not in rest:
        return None
    creds, _, cloud = rest.partition('@')
    if not cloud or ':' not in creds:
        return None
    key, _, secret = creds.partition(':')
    if not (cloud and key and secret):
        return None
    return (cloud, key, secret)


def from_env():
    """The env-var configuration as (cloud_name, api_key, api_secret), or None."""
    url = cloudinary_url()
    parsed = parse_cloudinary_url(url)
    if not parsed:
        return None
    return {
        'cloud_name': parsed[0],
        'api_key': parsed[1],
        'api_secret': parsed[2],
        'source': 'env',
    }


def check_credentials(cloud_name, api_key, api_secret):
    """Ask Cloudinary whether these credentials work. Creates nothing.

    A read-only Admin API call (list up to one image) authenticated over
    HTTPS. A wrong key, secret or environment comes back 401/403 from
    Cloudinary itself, which this returns verbatim.

    Returns a dict shaped for ``ok(...)``: always ``ok`` at the HTTP layer,
    with ``ok`` inside carrying the verdict.
    """
    if not (cloud_name and api_key and api_secret):
        missing = [name for name, value in (
            ('cloud name', cloud_name),
            ('API key', api_key),
            ('API secret', api_secret),
        ) if not value]
        return {'ok': False, 'cloud': cloud_name or '',
                'detail': f"Not configured: {' and '.join(missing)} not set."}

    token = base64.b64encode(f'{api_key}:{api_secret}'.encode('utf-8')).decode('ascii')
    url = f'{ADMIN_BASE}/{cloud_name}/resources/image?max_results=1'
    req = urllib.request.Request(url, headers={
        'Authorization': f'Basic {token}',
        'Accept': 'application/json',
    })

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return {
            'ok': True,
            'cloud': cloud_name,
            'http_status': resp.status,
            'detail': f'Connected to Cloudinary as {cloud_name}. Credentials '
                      'are valid.',
        }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', 'replace')[:500]
        reason = ''
        try:
            reason = json.loads(raw).get('error', {}).get('message', '')
        except Exception:                               # noqa: BLE001
            reason = ''
        detail = f'Cloudinary refused the request (HTTP {exc.code}).'
        if reason:
            detail += f' {reason}'
        return {'ok': False, 'cloud': cloud_name,
                'http_status': exc.code, 'detail': detail}
    except Exception as exc:                            # noqa: BLE001
        return {'ok': False, 'cloud': cloud_name,
                'detail': f'The check itself failed: {exc}'}


# --------------------------------------------------------------------------- #
#  Upload / download helpers
# --------------------------------------------------------------------------- #

def _resolve_credentials():
    """Return (cloud_name, api_key, api_secret) from env or DB, or None."""
    env = from_env()
    if env:
        return env['cloud_name'], env['api_key'], env['api_secret']
    try:
        from models_ext import Setting
        cloud = Setting.get('cloudinary_cloud_name', '')
        key = Setting.get('cloudinary_api_key', '')
        secret = Setting.get('cloudinary_api_secret', '')
        if cloud and key and secret:
            return cloud, key, secret
    except Exception:                                   # noqa: BLE001
        pass
    return None


def is_enabled():
    """True when Cloudinary is configured AND enabled in Settings."""
    enabled = False
    try:
        from models_ext import Setting
        enabled = str(Setting.get('cloudinary_enabled', 'False')).lower() in (
            '1', 'true', 'yes', 'on')
    except Exception:                                   # noqa: BLE001
        return False
    if not enabled:
        return False
    return _resolve_credentials() is not None


def _cloudinary_folder():
    """The upload folder prefix from Settings, or ''."""
    try:
        from models_ext import Setting
        return (Setting.get('cloudinary_folder', '') or '').strip()
    except Exception:                                   # noqa: BLE001
        return ''


def upload(file_storage, public_id=None, folder=None, resource_type='image'):
    """Upload a file to Cloudinary via the unsigned upload API.

    ``file_storage`` is a Werkzeug ``FileStorage`` (from ``request.files``).
    Returns the full HTTPS URL on success, or None on failure.

    Uses the ``upload_preset`` configured in Settings. If no preset is set,
    falls back to the signed upload API.
    """
    creds = _resolve_credentials()
    if not creds:
        return None
    cloud_name, api_key, api_secret = creds

    folder = folder or _cloudinary_folder()

    # Read the file into memory.
    file_storage.stream.seek(0)
    raw = file_storage.stream.read()
    file_storage.stream.seek(0)

    if not public_id:
        from werkzeug.utils import secure_filename as _sf
        base = _sf(file_storage.filename or 'upload')
        # Strip extension — Cloudinary appends format from content_type.
        if '.' in base:
            base = base.rsplit('.', 1)[0]
        import time
        public_id = f'{folder}/{base}_{int(time.time())}' if folder else f'{base}_{int(time.time())}'

    # Use the signed upload API (supports all features without presets).
    import time as _time
    timestamp = int(_time.time())
    params_to_sign = {'folder': folder, 'public_id': public_id,
                      'timestamp': timestamp, 'type': 'upload'}
    # Sort and build the string to sign.
    to_sign = '&'.join(f'{k}={v}' for k, v in sorted(params_to_sign.items())
                       if v is not None and v != '')
    import hashlib
    signature = hashlib.sha1(f'{to_sign}{api_secret}'.encode()).hexdigest()

    url = f'{UPLOAD_BASE}/{cloud_name}/upload'
    # Multipart form data.
    boundary = f'----WebKitFormBoundary{int(timestamp)}'
    body_parts = []

    def add_field(name, value):
        body_parts.append(f'--{boundary}\r\n'
                          f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                          f'{value}\r\n'.encode())

    add_field('api_key', api_key)
    add_field('timestamp', str(timestamp))
    add_field('signature', signature)
    add_field('type', 'upload')
    if folder:
        add_field('folder', folder)
    add_field('public_id', public_id)
    if resource_type:
        add_field('resource_type', resource_type)

    # File part.
    filename = file_storage.filename or 'upload'
    content_type = file_storage.content_type or 'application/octet-stream'
    body_parts.append(
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'.encode()
    )
    body_parts.append(raw)
    body_parts.append(f'\r\n--{boundary}--\r\n'.encode())

    payload = b''.join(body_parts)
    req = urllib.request.Request(url, data=payload, headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Accept': 'application/json',
    }, method='POST')

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        return result.get('secure_url') or result.get('url')
    except (urllib.error.HTTPError, urllib.error.URLError, Exception) as exc:
        try:
            from flask import current_app
            current_app.logger.error('Cloudinary upload failed: %s', exc)
        except Exception:                               # noqa: BLE001
            pass
        return None


def download_to_temp(url):
    """Download an image from a URL to a named temp file. Returns the path.

    Used by the PDF generator, which needs a filesystem path for ReportLab.
    The caller is responsible for cleaning up the file.
    """
    req = urllib.request.Request(url, headers={'User-Agent': 'YashApp/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        suffix = '.png'
        ct = resp.headers.get('Content-Type', '')
        if 'jpeg' in ct or 'jpg' in ct:
            suffix = '.jpg'
        elif 'webp' in ct:
            suffix = '.webp'
        elif 'gif' in ct:
            suffix = '.gif'
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(data)
        tmp.flush()
        tmp.close()
        return tmp.name
    except Exception:                                   # noqa: BLE001
        return None
