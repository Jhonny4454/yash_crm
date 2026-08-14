"""Serve the built React application from Flask at ``/app``.

The legacy Jinja screens remain available while the SPA is deployed alongside
them.  Client-side paths fall back to React's index document; actual built
assets are served directly from ``frontend/dist``.
"""
import gzip
import mimetypes
from pathlib import Path
from threading import Lock

from flask import Blueprint, Response, current_app, request, send_from_directory

spa_bp = Blueprint('spa', __name__, url_prefix='/app')

#: File types worth compressing. Everything here is text; images and fonts in
#: a Vite build are already compressed and gzipping them wastes CPU to make
#: them very slightly bigger.
COMPRESSIBLE = ('.js', '.css', '.html', '.svg', '.json', '.map', '.txt')

#: Below this, the gzip header costs more than the saving.
MIN_COMPRESS_BYTES = 1024

#: path -> (mtime, size, gzipped bytes). A Vite build is a few dozen files and
#: they never change without their name changing, so this stays small and warm
#: for the life of the process. Keyed on mtime as well so a rebuild is picked
#: up without a restart.
_gz_cache = {}
_gz_lock = Lock()


def _dist_dir():
    return Path(current_app.root_path) / 'frontend' / 'dist'


#: A year. Everything Vite writes into /assets carries a content hash in its
#: filename, so a changed file is a DIFFERENT file - it can never go stale,
#: and the browser should never ask about it again.
ASSET_MAX_AGE = 60 * 60 * 24 * 365


@spa_bp.get('/')
@spa_bp.get('/<path:requested_path>')
def app(requested_path=''):
    dist = _dist_dir()
    index = dist / 'index.html'
    candidate = (dist / requested_path).resolve()
    # Never let a URL path escape the Vite build directory on Windows or Unix.
    if (requested_path and candidate.is_relative_to(dist.resolve())
            and candidate.is_file()):
        return _serve(dist, requested_path, candidate)
    if index.is_file():
        return _serve(dist, 'index.html', index)
    return Response(
        'React assets are not built. Run "npm run build" in the frontend folder.',
        status=503,
        mimetype='text/plain',
    )


def _serve(dist, name, path_on_disk):
    """The file, gzipped when that helps and the browser asked for it.

    Flask does not compress anything on its own, so the React bundle was going
    out at its full 256 KB on every first visit - about three times what it
    needs to be, and the single biggest reason the app takes a moment to
    appear on a slow connection. Nothing upstream is doing it either: this is
    Flask talking straight to the browser.

    Compressed once and kept in memory. The files are immutable (their names
    carry a content hash) and there are only a few dozen, so the second
    request onwards costs nothing.
    """
    compressible = str(name).lower().endswith(COMPRESSIBLE)
    accepts_gzip = 'gzip' in (request.headers.get('Accept-Encoding') or '')

    if compressible and accepts_gzip:
        try:
            stat = path_on_disk.stat()
            if stat.st_size >= MIN_COMPRESS_BYTES:
                key = str(path_on_disk)
                with _gz_lock:
                    cached = _gz_cache.get(key)
                if not cached or cached[0] != stat.st_mtime_ns:
                    payload = gzip.compress(path_on_disk.read_bytes(), 6)
                    with _gz_lock:
                        _gz_cache[key] = (stat.st_mtime_ns, payload)
                else:
                    payload = cached[1]

                mime = mimetypes.guess_type(str(name))[0] or 'application/octet-stream'
                response = Response(payload, mimetype=mime)
                response.headers['Content-Encoding'] = 'gzip'
                # Caches must not hand this body to a client that cannot
                # decode it.
                response.headers['Vary'] = 'Accept-Encoding'
                return _cache_headers(response, name)
        except Exception:
            # Never fail a page load over an optimisation.
            current_app.logger.debug('Could not gzip %s', name, exc_info=True)

    return _cache_headers(send_from_directory(dist, name), name)


def _cache_headers(response, path):
    """Cache the hashed assets forever; never cache index.html.

    This was one rule for both, from SEND_FILE_MAX_AGE_DEFAULT: seven days for
    everything. That is wrong in both directions.

    index.html is the file that NAMES the current bundle. Cached for a week,
    a browser that has been here before keeps asking for asset filenames from
    the previous build - which no longer exist - so a fresh deploy either
    shows the old application or a blank screen, and the only fix the operator
    knows is a hard refresh. Every build we shipped today was subject to that.

    The assets are the opposite case. Their names contain a content hash, so
    they are safe to keep for a year; at seven days a returning user
    re-downloads a quarter of a megabyte of JavaScript they already had.

    Net effect on a repeat visit: one small conditional request for
    index.html, and nothing else off the network.
    """
    name = str(path).replace('\\', '/')

    if name.endswith('.html'):
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        response.headers.pop('Expires', None)
        return response

    if name.startswith('assets/'):
        response.headers['Cache-Control'] = (
            f'public, max-age={ASSET_MAX_AGE}, immutable')
        return response

    # Anything else in dist (favicon, manifest) is named without a hash, so
    # it gets a day - long enough to be worth caching, short enough to fix.
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


def register(app):
    if 'spa' not in app.blueprints:
        app.register_blueprint(spa_bp)
