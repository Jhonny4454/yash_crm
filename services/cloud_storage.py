"""
services/cloud_storage.py
=========================

Files that survive a deploy.

The problem this exists for
--------------------------
The container this application runs on has an ephemeral disk. Everything under
``static/uploads`` - the company logo, KYC documents, payment proof
screenshots - is written to that disk and is **gone the next time the service
is deployed**. Nobody notices immediately, because the record still names the
file; the file simply stops being there. That is the whole story behind "the
logo is not showing on bills" and it is quietly happening to every Aadhaar
scan and payment screenshot uploaded since the last deploy.

The fix is to put uploads somewhere that is not the container. This module is
that somewhere: one small S3-compatible client, so the bucket can live on
Cloudflare R2, Backblaze B2, Wasabi, AWS S3 or Supabase Storage without a code
change. Only the endpoint URL differs between them.

How it decides whether it is switched on
----------------------------------------
Configuration is read from the environment FIRST and the ``settings`` table
second. That order is deliberate:

* On a hosted deploy the credentials belong in the host's environment
  variables, where they are not in the database, not in a backup of the
  database, and not on screen.
* The settings table is the fallback so the office can set this up from the
  admin panel without a redeploy - which is how every other integration in
  this application is configured, and the only option if nobody has access to
  the hosting dashboard.

Nothing here raises when storage is unconfigured. ``is_enabled()`` returns
False, callers fall back to the local disk, and the application behaves
exactly as it did before. Switching this on is meant to be reversible.

Privacy
-------
Objects are uploaded with no public-read ACL. A KYC document is an identity
document; it must not be reachable by anyone who guesses a URL. Reads go
through ``presigned_url()`` (a link that expires) or through the application's
own authenticated route, never through a permanent public bucket URL.
"""
import io
import os
import threading

from flask import current_app, g

#: Bucket prefixes. Kept here so the layout is described in one place rather
#: than spelled out as string literals at every call site.
UPLOAD_PREFIX = 'uploads'
BACKUP_PREFIX = 'backups'

#: setting key -> environment variable that overrides it.
CONFIG_KEYS = {
    'storage_backend': 'STORAGE_BACKEND',
    's3_endpoint_url': 'S3_ENDPOINT_URL',
    's3_region': 'S3_REGION',
    's3_bucket': 'S3_BUCKET',
    's3_access_key_id': 'S3_ACCESS_KEY_ID',
    's3_secret_access_key': 'S3_SECRET_ACCESS_KEY',
    's3_public_base_url': 'S3_PUBLIC_BASE_URL',
}

#: How long a generated download link stays valid. Long enough to open a
#: document from a page that has been sitting on screen, short enough that a
#: link copied out of the address bar is not a permanent key to somebody's
#: identity proof.
DEFAULT_LINK_SECONDS = 900

_client_lock = threading.Lock()
_client_cache = {}


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
def _setting_rows():
    """Every storage setting from the database, as one query.

    Cached on ``g`` because serialising a page of customers asks whether
    storage is on once per document, and the answer cannot change mid-request.
    """
    try:
        cached = g.get('_storage_settings')
    except Exception:                                    # outside a request
        cached = None
    if cached is not None:
        return cached

    values = {}
    try:
        from models_ext import Setting
        rows = Setting.query.filter(Setting.key.in_(tuple(CONFIG_KEYS))).all()
        values = {r.key: (r.value or '') for r in rows}
    except Exception:                                    # noqa: BLE001
        # No table yet (first boot, before schema sync) is not an error - it
        # just means storage is not configured.
        values = {}

    try:
        g._storage_settings = values
    except Exception:
        pass
    return values


def invalidate_cache():
    """Forget the per-request settings snapshot after a save."""
    try:
        g.pop('_storage_settings', None)
    except Exception:
        pass


def config():
    """The resolved storage configuration. Environment wins over database."""
    rows = _setting_rows()
    out = {}
    for key, env_name in CONFIG_KEYS.items():
        value = os.environ.get(env_name)
        if value is None or str(value).strip() == '':
            value = rows.get(key, '')
        out[key] = str(value or '').strip()
    if not out['s3_region']:
        # R2 rejects an empty region and accepts 'auto'; AWS ignores it when
        # the endpoint already names a region. 'auto' is the safe default.
        out['s3_region'] = 'auto'
    return out


def is_available():
    """Whether the boto3 dependency is installed at all.

    Checked rather than assumed: boto3 is in requirements.txt, but a server
    that has not been redeployed since it was added does not have it, and the
    honest message for that is "the library is missing, redeploy" rather than
    an ImportError traceback on the first upload.
    """
    from importlib.util import find_spec
    return find_spec('boto3') is not None


def is_enabled():
    """True when uploads and backups should go to the bucket."""
    cfg = config()
    if (cfg['storage_backend'] or 'local').lower() != 's3':
        return False
    if not is_available():
        return False
    return all(cfg[k] for k in
               ('s3_bucket', 's3_access_key_id', 's3_secret_access_key'))


def status():
    """A description of the current configuration for the settings screen.

    Never includes the secret key. The access key id is shown truncated - it
    is not a credential on its own, and seeing the first characters is how an
    operator confirms they pasted the right one of two accounts.
    """
    cfg = config()
    backend = (cfg['storage_backend'] or 'local').lower()
    key_id = cfg['s3_access_key_id']
    missing = [name for name, value in (
        ('Bucket name', cfg['s3_bucket']),
        ('Access key ID', key_id),
        ('Secret access key', cfg['s3_secret_access_key']),
    ) if not value]

    return {
        'backend': backend,
        'enabled': is_enabled(),
        'library_installed': is_available(),
        'bucket': cfg['s3_bucket'],
        'endpoint': cfg['s3_endpoint_url'],
        'region': cfg['s3_region'],
        'access_key_hint': f'{key_id[:4]}…{key_id[-4:]}' if len(key_id) > 8 else '',
        'has_secret': bool(cfg['s3_secret_access_key']),
        'public_base_url': cfg['s3_public_base_url'],
        'missing': missing,
        'from_environment': sorted(
            name for key, name in CONFIG_KEYS.items()
            if str(os.environ.get(name) or '').strip()),
    }


# --------------------------------------------------------------------------- #
#  Client
# --------------------------------------------------------------------------- #
def client():
    """A boto3 S3 client, or None when storage is not configured.

    Cached per configuration rather than globally: changing the bucket in
    Settings has to take effect without a restart, and keying the cache on the
    values means a change simply misses the cache.
    """
    if not is_enabled():
        return None

    cfg = config()
    fingerprint = (cfg['s3_endpoint_url'], cfg['s3_region'],
                   cfg['s3_access_key_id'], cfg['s3_secret_access_key'])

    with _client_lock:
        existing = _client_cache.get(fingerprint)
        if existing is not None:
            return existing

        import boto3
        from botocore.config import Config as BotoConfig

        built = boto3.client(
            's3',
            endpoint_url=cfg['s3_endpoint_url'] or None,
            region_name=cfg['s3_region'],
            aws_access_key_id=cfg['s3_access_key_id'],
            aws_secret_access_key=cfg['s3_secret_access_key'],
            config=BotoConfig(
                signature_version='s3v4',
                # A hung upload must not hold a web worker open. Three
                # attempts covers a transient network blip without turning a
                # dead endpoint into a 90-second page load.
                connect_timeout=10, read_timeout=60,
                retries={'max_attempts': 3, 'mode': 'standard'},
            ),
        )
        _client_cache.clear()
        _client_cache[fingerprint] = built
        return built


def bucket():
    return config()['s3_bucket']


# --------------------------------------------------------------------------- #
#  Object operations
#
#  Every one of these returns rather than raises when storage is off, so a
#  caller can be written as "try the bucket, fall back to disk" without a
#  try/except around each line.
# --------------------------------------------------------------------------- #
def put_bytes(key, data, content_type=None):
    """Upload bytes. Returns True on success."""
    api = client()
    if api is None:
        return False
    extra = {}
    if content_type:
        extra['ContentType'] = content_type
    api.put_object(Bucket=bucket(), Key=key, Body=data, **extra)
    return True


def put_file(key, path, content_type=None):
    """Upload a file from disk by streaming it, without reading it into RAM.

    Matters for backups: a database dump is the one thing here that can be
    hundreds of megabytes, and loading it into memory on a small container is
    how a backup turns into an out-of-memory restart.
    """
    api = client()
    if api is None:
        return False
    extra = {'ContentType': content_type} if content_type else {}
    with open(path, 'rb') as handle:
        api.upload_fileobj(handle, bucket(), key,
                           ExtraArgs=extra or None)
    return True


def put_stream(key, fileobj, content_type=None):
    """Upload from an open file object (a Werkzeug upload stream, say)."""
    api = client()
    if api is None:
        return False
    extra = {'ContentType': content_type} if content_type else {}
    api.upload_fileobj(fileobj, bucket(), key, ExtraArgs=extra or None)
    return True


def get_bytes(key):
    """Download an object, or None if it is not there."""
    api = client()
    if api is None:
        return None
    try:
        response = api.get_object(Bucket=bucket(), Key=key)
        return response['Body'].read()
    except Exception:                                    # noqa: BLE001
        return None


def open_stream(key):
    """A file-like object for one stored object, or None.

    Used by the download route so a large backup is streamed to the browser
    instead of buffered.
    """
    api = client()
    if api is None:
        return None
    try:
        response = api.get_object(Bucket=bucket(), Key=key)
        return response['Body']
    except Exception:                                    # noqa: BLE001
        return None


def exists(key):
    api = client()
    if api is None:
        return False
    try:
        api.head_object(Bucket=bucket(), Key=key)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def size_of(key):
    api = client()
    if api is None:
        return None
    try:
        return int(api.head_object(Bucket=bucket(), Key=key)['ContentLength'])
    except Exception:                                    # noqa: BLE001
        return None


def delete(key):
    api = client()
    if api is None:
        return False
    try:
        api.delete_object(Bucket=bucket(), Key=key)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def list_objects(prefix, limit=1000):
    """[{key, size, last_modified}] under one prefix, newest first."""
    api = client()
    if api is None:
        return []
    out = []
    try:
        paginator = api.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket(), Prefix=prefix):
            for item in page.get('Contents', []):
                out.append({'key': item['Key'],
                            'size': int(item.get('Size') or 0),
                            'last_modified': item.get('LastModified')})
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
    except Exception:                                    # noqa: BLE001
        return []
    out.sort(key=lambda o: o['last_modified'] or 0, reverse=True)
    return out


def presigned_url(key, seconds=DEFAULT_LINK_SECONDS, download_name=None):
    """A temporary link to one object, or None.

    The bucket stays private; this is the only way a browser reaches an object
    directly, and the link stops working on its own.
    """
    api = client()
    if api is None:
        return None
    params = {'Bucket': bucket(), 'Key': key}
    if download_name:
        params['ResponseContentDisposition'] = \
            f'attachment; filename="{download_name}"'
    try:
        return api.generate_presigned_url('get_object', Params=params,
                                          ExpiresIn=int(seconds))
    except Exception:                                    # noqa: BLE001
        return None


def public_url(key):
    """A permanent URL, only for objects that are genuinely public.

    Returns None unless a public base URL has been configured, because
    guessing one would produce a link that 404s on a private bucket and look
    like a missing file. Only ever used for the company logo, which appears on
    bills sent to customers and is not confidential.
    """
    base = config()['s3_public_base_url']
    if not base:
        return None
    return f"{base.rstrip('/')}/{key.lstrip('/')}"


# --------------------------------------------------------------------------- #
#  Uploads: the bucket, with the local disk as a fallback
#
#  Reads try the disk first. Files uploaded before this was switched on are
#  still on the disk of a container that has not been redeployed yet, and they
#  should keep working until they are migrated or replaced.
# --------------------------------------------------------------------------- #
def upload_key(folder, filename):
    """The object key one upload lives at: uploads/kyc/c2-photo-ab12.jpg."""
    name = os.path.basename(str(filename or '').replace('\\', '/'))
    return f'{UPLOAD_PREFIX}/{folder}/{name}'


def local_upload_path(folder, filename):
    name = os.path.basename(str(filename or '').replace('\\', '/'))
    return os.path.join(current_app.root_path, 'static', 'uploads', folder, name)


def save_upload(folder, filename, stream, content_type=None):
    """Store one uploaded file. Returns ('s3'|'local', error_or_None).

    Falls back to the local disk when the bucket is unreachable rather than
    losing the upload: a customer standing at the counter with their Aadhaar
    card should not be turned away because a storage endpoint is down.
    """
    if is_enabled():
        try:
            stream.seek(0)
            put_stream(upload_key(folder, filename), stream, content_type)
            return 's3', None
        except Exception as exc:                         # noqa: BLE001
            current_app.logger.warning('Cloud upload failed, using local disk: %s', exc)

    path = local_upload_path(folder, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        stream.seek(0)
    except Exception:                                    # noqa: BLE001
        pass
    try:
        with open(path, 'wb') as handle:
            handle.write(stream.read())
    except OSError as exc:
        return None, str(exc)[:200]
    return 'local', None


def read_upload(folder, filename):
    """(bytes, source) for one stored upload, or (None, None).

    Disk first, then the bucket - see the module note above.
    """
    path = local_upload_path(folder, filename)
    if os.path.isfile(path):
        try:
            with open(path, 'rb') as handle:
                return handle.read(), 'local'
        except OSError:
            pass
    data = get_bytes(upload_key(folder, filename))
    if data is not None:
        return data, 's3'
    return None, None


def upload_exists(folder, filename):
    if not filename:
        return False
    if os.path.isfile(local_upload_path(folder, filename)):
        return True
    return exists(upload_key(folder, filename))


def delete_upload(folder, filename):
    """Remove a superseded upload from wherever it is."""
    if not filename:
        return
    try:
        path = local_upload_path(folder, filename)
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
    if is_enabled():
        delete(upload_key(folder, filename))


# --------------------------------------------------------------------------- #
#  Connection test
# --------------------------------------------------------------------------- #
def test_connection():
    """Write a small object, read it back, delete it. Returns a verdict dict.

    A credentials check that only lists the bucket passes with a read-only
    key, and then every upload fails later with no clue why. This exercises
    the three permissions the application actually needs.
    """
    cfg = config()
    if (cfg['storage_backend'] or 'local').lower() != 's3':
        return {'ok': False,
                'detail': 'Storage is set to the local disk. Switch "File storage" '
                          'to the cloud bucket first, then save.'}
    if not is_available():
        return {'ok': False,
                'detail': 'The boto3 library is not installed on the server. '
                          'Add boto3 to requirements.txt and redeploy.'}

    missing = status()['missing']
    if missing:
        return {'ok': False,
                'detail': 'Still needed: ' + ', '.join(missing) + '.'}

    key = f'{UPLOAD_PREFIX}/.connection-test'
    probe = b'yash-internet-services storage check'
    try:
        api = client()
        api.put_object(Bucket=cfg['s3_bucket'], Key=key, Body=probe,
                       ContentType='text/plain')
    except Exception as exc:                             # noqa: BLE001
        return {'ok': False, 'stage': 'write',
                'detail': _explain(exc, cfg)}

    try:
        got = api.get_object(Bucket=cfg['s3_bucket'], Key=key)['Body'].read()
    except Exception as exc:                             # noqa: BLE001
        return {'ok': False, 'stage': 'read',
                'detail': 'Upload worked but reading it back did not: '
                          + _explain(exc, cfg)}

    if got != probe:
        return {'ok': False, 'stage': 'read',
                'detail': 'The file read back did not match what was written.'}

    deleted = True
    try:
        api.delete_object(Bucket=cfg['s3_bucket'], Key=key)
    except Exception:                                    # noqa: BLE001
        deleted = False

    detail = f"Connected to {cfg['s3_bucket']}. Wrote, read and removed a test file."
    if not deleted:
        detail = (f"Connected to {cfg['s3_bucket']} and uploads work, but the key "
                  "cannot delete objects. Replaced files and expired backups "
                  "will pile up.")
    return {'ok': True, 'detail': detail, 'bucket': cfg['s3_bucket'],
            'can_delete': deleted}


def _explain(exc, cfg):
    """Turn a boto3 error into something an operator can act on.

    Every one of these arrives as the same ClientError with a code buried in a
    dict, and the raw string names neither the bucket nor what to change.
    """
    code = ''
    try:
        code = exc.response['Error']['Code']              # type: ignore[attr-defined]
    except Exception:                                    # noqa: BLE001
        code = type(exc).__name__

    endpoint = cfg['s3_endpoint_url'] or 'the default AWS endpoint'
    known = {
        'NoSuchBucket': f"There is no bucket called '{cfg['s3_bucket']}' at {endpoint}. "
                        "Check the spelling, and that the bucket is in this account.",
        'InvalidAccessKeyId': 'The access key ID is not recognised. Copy it again '
                              'from the storage dashboard.',
        'SignatureDoesNotMatch': 'The secret access key does not match the access key '
                                 'ID. These are issued as a pair - copying one from '
                                 'an older token is the usual cause.',
        'AccessDenied': 'The credentials are valid but not allowed to write to this '
                        'bucket. The token needs object read and write permission.',
        'InvalidBucketName': f"'{cfg['s3_bucket']}' is not a valid bucket name.",
        'EndpointConnectionError': f'Could not reach {endpoint}. Check the endpoint URL.',
    }
    if code in known:
        return known[code]
    return f'{code}: {str(exc)[:200]}'


def probe_bytes():
    """Exposed for tests: an in-memory file object helper."""
    return io.BytesIO
