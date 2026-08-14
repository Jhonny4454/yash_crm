"""
cleanup_dead_files.py
=====================

Move files that nothing imports out of the source tree.

    python cleanup_dead_files.py            # show what would move
    python cleanup_dead_files.py --apply    # actually move them

Nothing is deleted. Everything goes to ``_removed/`` at the project root,
keeping its original path, with a MANIFEST.json alongside. If one turns out to
matter, move it straight back. Delete ``_removed/`` when you are satisfied.

The one exception is stale ``.pyc`` files - bytecode for modules that no longer
have a ``.py``. Those are deleted outright, because they are regenerated
artefacts with nothing to recover.

Why these files are dead
------------------------
*The referral campaign* - you asked for it removed. The React page and the API
blueprint went at the time, but ``blueprints/referral_bp.py`` was still
imported and registered in app.py, so ``/referral`` and its six sibling routes
carried on being served. That registration is gone now, which leaves the file
itself dead.

*Superseded shell parts* - Footer, Topbar, MobileSidebar and layouts/Base were
replaced by AppShell and AdminLayout. Base.css went with layouts/Base.jsx.

*Dashboard widgets that never worked* - the seven components under
``components/dashboard/`` fetch ``/api/v1/dashboard/recent``. That is wrong
twice over: the axios client already prefixes ``/api/v1``, so the real request
went to ``/api/v1/api/v1/dashboard/recent``; and no ``/dashboard/recent``
endpoint exists on the API at all. They could not have shown data in any
version of this app. RevenueChart is the interesting one - a dependency-free
SVG bar chart of the six-month trend. It is not part of the UniCRM dashboard
layout so it is not wired in, but it is worth keeping in case you want it.

Every file is re-checked for references at run time, so this stays safe to run
after the code has moved on: anything that has since been imported is left in
place and reported rather than moved.
"""
import argparse
import json
import pathlib
import re
import shutil
import sys

#: (path relative to the project root, why it is dead)
CANDIDATES = [
    ('blueprints/referral_bp.py',
     'The refer-a-friend campaign you asked to have removed. No longer '
     'registered in app.py, so nothing serves its routes.'),

    ('frontend/src/components/Footer.jsx', 'Superseded by AppShell.'),
    ('frontend/src/components/Topbar.jsx', 'Superseded by AppShell / AdminLayout.'),
    ('frontend/src/components/MobileSidebar.jsx', 'Superseded by Sidebar.'),
    ('frontend/src/layouts/Base.jsx', 'Superseded by AdminLayout.'),
    ('frontend/src/styles/Base.css', 'Stylesheet for the removed layouts/Base.jsx.'),

    ('frontend/src/components/customers/CustomerTable.jsx',
     'Superseded by the table inside Customers.jsx.'),
    ('frontend/src/components/customers/CustomerToolbar.jsx',
     'Superseded by the toolbar inside Customers.jsx.'),

    ('frontend/src/components/dashboard/DuePayments.jsx',
     'Calls /api/v1/dashboard/recent - double prefix, and no such endpoint.'),
    ('frontend/src/components/dashboard/ExpiringPlans.jsx',
     'Same broken endpoint; replaced by the plan lifecycle chips.'),
    ('frontend/src/components/dashboard/MetricCards.jsx',
     'Replaced by the metric strip on AdminDashboard.'),
    ('frontend/src/components/dashboard/QuickActions.jsx',
     'Replaced by the quick actions on AdminDashboard.'),
    ('frontend/src/components/dashboard/RecentCustomers.jsx',
     'Same broken endpoint; not part of the UniCRM dashboard layout.'),
    ('frontend/src/components/dashboard/RecentInvoices.jsx',
     'Same broken endpoint; not part of the UniCRM dashboard layout.'),
    ('frontend/src/components/dashboard/RevenueChart.jsx',
     'Dependency-free SVG trend chart. Works, but calls /api/v1/dashboard '
     '(double prefix) and is not in the reference dashboard layout.'),
]

SKIP_DIRS = {'_removed', 'node_modules', '.git', '.venv', '__pycache__',
             'dist', 'build', 'migrations'}

JS_IMPORT_RE = re.compile(r'(?:from|import\(|import)\s*["\']([^"\']+)["\']')


def find_root(start):
    """The project root: the folder holding both app.py and frontend/."""
    for base in (start, start.parent, start.parent.parent):
        if (base / 'app.py').exists() and (base / 'frontend').is_dir():
            return base
    return None


def walk(root, suffixes):
    for path in root.rglob('*'):
        if path.suffix not in suffixes or not path.is_file():
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


def js_importers(root, target):
    """Front-end files that import `target`."""
    hits = []
    for path in walk(root, {'.jsx', '.js', '.css'}):
        if path.resolve() == target.resolve():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        for match in JS_IMPORT_RE.finditer(text):
            spec = match.group(1)
            if not spec.startswith('.'):
                continue
            resolved = (path.parent / spec).resolve()
            candidates = (resolved, resolved.with_suffix('.jsx'),
                          resolved.with_suffix('.js'),
                          resolved.parent / (resolved.name + '.css'),
                          resolved / 'index.jsx', resolved / 'index.js')
            if target.resolve() in candidates:
                hits.append(str(path.relative_to(root)))
                break
    return sorted(set(hits))


def py_importers(root, target):
    """Python files that import the module `target` defines."""
    module = target.stem
    pattern = re.compile(rf'\b(?:import|from)\s+[\w.]*\b{re.escape(module)}\b')
    hits = []
    for path in walk(root, {'.py'}):
        if path.resolve() == target.resolve():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if pattern.search(text):
            hits.append(str(path.relative_to(root)))
    return sorted(set(hits))


def stale_bytecode(root):
    """.pyc files whose .py is gone - a directory listing that lies."""
    found = []
    for cache in root.rglob('__pycache__'):
        if {'_removed', 'node_modules', '.venv', '.git'} & set(cache.parts):
            continue
        for pyc in cache.glob('*.cpython-*.pyc'):
            if not (cache.parent / f'{pyc.name.split(".")[0]}.py').exists():
                found.append(pyc)
    return found


def main():
    parser = argparse.ArgumentParser(
        description='Move unreferenced source files into _removed/.')
    parser.add_argument('--apply', action='store_true',
                        help='actually move them (default is a dry run)')
    args = parser.parse_args()

    root = find_root(pathlib.Path.cwd())
    if root is None:
        print('Could not find the project root (needs app.py and frontend/).')
        print('Run this from the folder containing app.py.')
        return 1

    destination = root / '_removed'
    moved, kept, absent = [], [], []

    for relative, reason in CANDIDATES:
        source = root / relative
        if not source.exists():
            absent.append(relative)
            continue

        users = (py_importers(root, source) if source.suffix == '.py'
                 else js_importers(root, source))
        if users:
            kept.append((relative, users))
            continue

        moved.append((relative, reason))
        if args.apply:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))

    pyc = stale_bytecode(root)

    verb = 'Moved' if args.apply else 'Would move'
    print(f'{verb} {len(moved)} unreferenced file(s):')
    for relative, reason in moved:
        print(f'  {relative}\n      {reason}')
    if not moved:
        print('  (none)')

    if kept:
        print('\nLeft in place - something imports these now:')
        for relative, users in kept:
            print(f'  {relative}\n      imported by {", ".join(users)}')

    if pyc:
        verb = 'Deleted' if args.apply else 'Would delete'
        print(f'\n{verb} {len(pyc)} stale .pyc file(s) whose .py is gone:')
        for path in pyc[:8]:
            print(f'  {path.relative_to(root)}')
        if len(pyc) > 8:
            print(f'  ... and {len(pyc) - 8} more')
        if args.apply:
            for path in pyc:
                try:
                    path.unlink()
                except OSError:
                    pass

    if absent:
        print(f'\nAlready gone: {len(absent)} file(s)')

    if not args.apply:
        print('\nDry run - nothing was touched. Re-run with --apply.')
        return 0

    if moved:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'MANIFEST.json').write_text(
            json.dumps([{'file': f, 'reason': r} for f, r in moved], indent=2),
            encoding='utf-8')
        print(f'\nMoved into _removed/, with a MANIFEST.json listing each one.')
        print('Check the app still starts and "npm run build" still passes, '
              'then delete _removed/ when you are happy.')

    return 0


if __name__ == '__main__':
    sys.exit(main())
