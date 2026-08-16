"""
services/cloudinary.py
======================

Cloudinary configuration helpers and a read-only credential check.

Credentials can come from two places, in this order of precedence:

  1. The ``CLOUDINARY_URL`` environment variable, set on the hosting
     platform (e.g. Render).  Format::

         cloudinary://API_KEY:API_SECRET@CLOUD_NAME

  2. The ``cloudinary_*`` rows edited on the Settings screen.

An env var set at deploy time is a platform-level decision and wins over
anything typed in the UI - the whole reason it exists is that a redeploy can
silently drop a config that was only ever sitting in a text field, and the
company logo with it.  Nothing here mutates anything: the credential check is
a single read-only Admin API call.
"""
import base64
import json
import os
import urllib.error
import urllib.request

URL_PREFIX = 'cloudinary://'
ADMIN_BASE = 'https://api.cloudinary.com/v1_1'


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
