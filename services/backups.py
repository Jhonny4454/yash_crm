"""
services/backups.py
===================

Database backups that outlive the container.

Why this module exists
----------------------
There was already a backup button. It wrote the dump into ``<app>/backups/``,
which is a folder on the same ephemeral container disk that loses
``static/uploads`` on every deploy - so the backup and the thing it was meant
to protect against disappeared together. A backup that lives on the machine it
is backing up is a copy, not a backup.

There was also a second problem, quieter than that one. The React admin panel
called ``blueprints.settings_bp._run_backup``, and no function by that name
exists in that module. The import raised ``ImportError`` every time, the caller
fell through to its "copy the SQLite file" branch, and on MySQL that branch
answers *"Backup requires mysqldump/pg_dump on the server."* - so pressing
**Backup now** in the admin panel has never once produced a backup on this
deployment. This module is the missing function, and both screens now call it.

What a backup is
----------------
Whichever of these the server can do, in order:

1. ``mysqldump`` / ``pg_dump`` / SQLite's own online backup API - a complete,
   restorable dump including schema.
2. A plain-SQL dump written by this module, for the common hosted case where
   the database is MySQL but ``mysqldump`` is not installed in the container.
   It is data-only: restore it into an instance that has already created its
   schema on boot, which this application does automatically. Not as good as
   ``mysqldump``, and the log says so - but a data-only backup you have beats a
   perfect one you do not.

The result is gzipped, uploaded to the configured bucket when there is one,
and only kept on local disk when there is not.
"""
import gzip
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta

from flask import current_app

from services import cloud_storage

#: Keep at least this many of the newest backups no matter how old they are.
#: Retention is meant to stop the bucket growing forever, not to leave the
#: business with nothing because the nightly job has been failing for a month.
MIN_KEPT = 3

DEFAULT_RETENTION_DAYS = 30

#: A dump is allowed ten minutes. Past that something is wrong, and a
#: subprocess holding a connection open is worse than a failed backup.
DUMP_TIMEOUT = 600


def _setting(key, default=''):
    try:
        from models_ext import Setting
        value = Setting.get(key)
        return default if value in (None, '') else value
    except Exception:                                    # noqa: BLE001
        return default


def is_scheduled_on():
    raw = str(_setting('backup_enabled', 'False')).strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


def retention_days():
    try:
        value = int(str(_setting('backup_retention_days',
                                 DEFAULT_RETENTION_DAYS)).strip())
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    return max(1, min(value, 3650))


def scheduled_hour():
    try:
        return max(0, min(int(str(_setting('backup_hour', 3)).strip()), 23))
    except (TypeError, ValueError):
        return 3


def local_dir():
    folder = os.path.join(current_app.root_path, 'backups')
    os.makedirs(folder, exist_ok=True)
    return folder


def backup_key(filename):
    return f'{cloud_storage.BACKUP_PREFIX}/{os.path.basename(filename)}'


# --------------------------------------------------------------------------- #
#  Dump writers - each writes to `dest` and returns a note for the log
# --------------------------------------------------------------------------- #
def _dump_sqlite(url, dest):
    import sqlite3

    source = url.database
    if not source or not os.path.exists(source):
        raise FileNotFoundError(f'SQLite file not found at {source}')

    with tempfile.NamedTemporaryFile(delete=False) as handle:
        temp_path = handle.name
    try:
        # sqlite3's backup API copies a consistent snapshot even while the
        # application is writing. Copying the file with shutil does not.
        with sqlite3.connect(source) as src, sqlite3.connect(temp_path) as dst:
            src.backup(dst)
        with open(temp_path, 'rb') as fin, gzip.open(dest, 'wb') as fout:
            shutil.copyfileobj(fin, fout)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
    return 'SQLite online backup'


def _dump_mysql(url, dest):
    command = [
        'mysqldump',
        '-h', url.host or 'localhost',
        '-P', str(url.port or 3306),
        '-u', url.username or 'root',
        '--single-transaction', '--quick',
        '--routines', '--triggers',
        # Hosted MySQL almost never grants the PROCESS privilege, and without
        # this flag mysqldump 8 refuses to start at all with
        # "Access denied; you need PROCESS privilege".
        '--no-tablespaces',
        url.database,
    ]
    env = dict(os.environ)
    if url.password:
        # Via the environment, never argv - anything in argv is visible to
        # every other process on the machine through the process list.
        env['MYSQL_PWD'] = url.password

    proc = subprocess.run(command, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env,
                          timeout=DUMP_TIMEOUT, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode('utf-8', 'replace')[:400]
                           or 'mysqldump failed')
    with gzip.open(dest, 'wb') as fout:
        fout.write(proc.stdout)
    return 'mysqldump (schema and data)'


def _dump_postgres(url, dest):
    env = dict(os.environ)
    if url.password:
        env['PGPASSWORD'] = url.password
    command = ['pg_dump', '-Fc', '-h', url.host or 'localhost',
               '-p', str(url.port or 5432), '-U', url.username or 'postgres',
               '-f', dest, url.database]
    proc = subprocess.run(command, capture_output=True, env=env,
                          timeout=DUMP_TIMEOUT, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode('utf-8', 'replace')[:400]
                           or 'pg_dump failed')
    return 'pg_dump (custom format)'


def _quote(value):
    """SQL literal for one Python value."""
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (bytes, bytearray)):
        return "X'" + value.hex() + "'"
    if isinstance(value, (datetime,)):
        return "'" + value.isoformat(sep=' ') + "'"
    text = str(value)
    return "'" + text.replace('\\', '\\\\').replace("'", "''") + "'"


def _dump_generic(dest):
    """Data-only SQL dump written through SQLAlchemy.

    The fallback for a hosted MySQL container with no mysqldump binary, which
    is the normal state of a slim Python image. Every table the models declare,
    in dependency order, as INSERT statements.

    Restoring: bring up the application against an empty database so it
    creates the schema, then feed this file in. The schema is not in here -
    which is the honest limitation, and why this only runs when the real tool
    is missing.
    """
    from models import db

    engine = db.engine
    written_rows = 0
    written_tables = 0

    with gzip.open(dest, 'wt', encoding='utf-8') as out:
        out.write('-- Yash Internet Services - data-only backup\n')
        out.write(f'-- taken {datetime.utcnow().isoformat()}Z\n')
        out.write(f'-- dialect: {engine.dialect.name}\n')
        out.write('--\n-- Restore: start the application against an empty\n')
        out.write('-- database so it creates the tables, then load this file.\n')
        out.write('SET FOREIGN_KEY_CHECKS=0;\n'
                  if engine.dialect.name == 'mysql' else '')

        with engine.connect() as connection:
            for table in db.metadata.sorted_tables:
                try:
                    rows = connection.execute(table.select()).fetchall()
                except Exception as exc:                 # noqa: BLE001
                    out.write(f'-- SKIPPED {table.name}: {str(exc)[:120]}\n')
                    continue
                if not rows:
                    continue
                written_tables += 1
                columns = [c.name for c in table.columns]
                column_sql = ', '.join(f'`{c}`' for c in columns) \
                    if engine.dialect.name == 'mysql' \
                    else ', '.join(f'"{c}"' for c in columns)
                name = (f'`{table.name}`' if engine.dialect.name == 'mysql'
                        else f'"{table.name}"')
                out.write(f'\n-- {table.name} ({len(rows)} rows)\n')
                for row in rows:
                    values = ', '.join(_quote(v) for v in row)
                    out.write(f'INSERT INTO {name} ({column_sql}) VALUES ({values});\n')
                    written_rows += 1

        out.write('\nSET FOREIGN_KEY_CHECKS=1;\n'
                  if engine.dialect.name == 'mysql' else '')

    return (f'data-only SQL dump ({written_rows} rows in {written_tables} '
            'tables) - mysqldump is not installed on this server, so the '
            'schema is not included')


# --------------------------------------------------------------------------- #
#  The job
# --------------------------------------------------------------------------- #
def run_backup(log):
    """Take a backup and record the outcome on ``log`` (a BackupLog row).

    Does not commit - the caller owns the transaction, because the API route
    and the scheduled job need different behaviour around one.
    """
    from models import db

    url = db.engine.url
    dialect = db.engine.dialect.name
    stamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')

    if dialect == 'sqlite':
        filename, writer = f'backup-{stamp}.sqlite.gz', _dump_sqlite
    elif dialect == 'mysql':
        filename, writer = f'backup-{stamp}.sql.gz', _dump_mysql
    elif dialect == 'postgresql':
        filename, writer = f'backup-{stamp}.dump', _dump_postgres
    else:
        filename, writer = f'backup-{stamp}.sql.gz', None

    workdir = tempfile.mkdtemp(prefix='yis-backup-')
    temp_path = os.path.join(workdir, filename)

    try:
        note = None
        if writer is not None:
            try:
                note = writer(url, temp_path)
            except FileNotFoundError:
                # The dump binary is not on PATH. Normal on a slim container.
                note = None
            except subprocess.TimeoutExpired:
                raise RuntimeError('The dump timed out after 10 minutes.')

        if note is None:
            # Either there is no native tool for this dialect, or it is not
            # installed. Rename unconditionally: the fallback always writes
            # plain SQL, so leaving a SQLite run named "backup-x.sqlite.gz"
            # would hand somebody a file that is not what its name says it is,
            # at the exact moment they are trying to restore from it.
            filename = f'backup-{stamp}.sql.gz'
            temp_path = os.path.join(workdir, filename)
            note = _dump_generic(temp_path)

        size = os.path.getsize(temp_path)
        if size == 0:
            raise RuntimeError('The dump produced an empty file.')

        # ---- put it somewhere that is not this container --------------- #
        if cloud_storage.is_enabled():
            cloud_storage.put_file(backup_key(filename), temp_path,
                                   content_type='application/gzip')
            location = 's3'
            where = f'stored in {cloud_storage.bucket()}'
        else:
            shutil.move(temp_path, os.path.join(local_dir(), filename))
            location = 'local'
            where = ('kept on the server disk - set up cloud storage in '
                     'Settings, or this copy is lost on the next deploy')

        log.filename = filename
        log.size_bytes = size
        log.status = 'success'
        log.location = location
        log.message = f'{note}; {where}'
        return log

    except Exception as exc:                             # noqa: BLE001
        log.status = 'failed'
        log.message = str(exc)[:500]
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def prune(days=None, min_kept=MIN_KEPT):
    """Delete backups older than the retention window.

    Returns {'deleted': [...], 'kept': n}. The newest ``min_kept`` are never
    touched: if the nightly job has been failing silently, the last thing this
    should do is remove the one good copy left.
    """
    from models import db
    from models_ext import BackupLog

    days = retention_days() if days is None else int(days)
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = (BackupLog.query
            .filter(BackupLog.status == 'success')
            .filter(BackupLog.filename.isnot(None))
            .filter(BackupLog.purged_at.is_(None))
            .order_by(BackupLog.created_at.desc())
            .all())

    deleted = []
    for row in rows[min_kept:]:
        if (row.created_at or datetime.utcnow()) >= cutoff:
            continue
        name = row.filename
        if (row.location or 'local') == 's3':
            cloud_storage.delete(backup_key(name))
        else:
            try:
                os.remove(os.path.join(local_dir(), name))
            except OSError:
                pass
        # The row stays, and `status` stays 'success', because it is a true
        # record that a backup was taken that night. `purged_at` is what says
        # the file itself is gone. Rewriting status to 'failed' - which the
        # old delete route did - turns the history into a lie about whether
        # backups were working.
        row.purged_at = datetime.utcnow()
        row.message = f'File removed automatically after {days} days.'
        deleted.append(name)

    if deleted:
        db.session.commit()
    return {'deleted': deleted, 'kept': len(rows) - len(deleted)}


def open_backup(log):
    """(stream, size) for one recorded backup, or (None, None).

    Checks both places regardless of what the row says, because a backup taken
    before cloud storage was switched on has ``location='local'`` and may since
    have been migrated - and a row whose file is simply gone should report that
    rather than 500.
    """
    if not log or not log.filename or log.purged_at:
        return None, None

    path = os.path.join(local_dir(), os.path.basename(log.filename))
    if os.path.isfile(path):
        return open(path, 'rb'), os.path.getsize(path)

    key = backup_key(log.filename)
    stream = cloud_storage.open_stream(key)
    if stream is not None:
        return stream, cloud_storage.size_of(key)
    return None, None


def run_scheduled():
    """Entry point for the nightly job. Never raises - it runs unattended."""
    from models import db
    from models_ext import BackupLog

    if not is_scheduled_on():
        return {'skipped': 'Automatic backups are switched off in Settings.'}

    log = BackupLog(kind='scheduled', status='running')
    db.session.add(log)
    db.session.commit()

    try:
        run_backup(log)
        db.session.commit()
        current_app.logger.info('Scheduled backup: %s', log.message)
    except Exception as exc:                             # noqa: BLE001
        db.session.rollback()
        row = db.session.get(BackupLog, log.id)
        if row is not None:
            row.status = 'failed'
            row.message = str(exc)[:500]
            db.session.commit()
        current_app.logger.exception('Scheduled backup failed')
        return {'ok': False, 'error': str(exc)[:200]}

    try:
        prune()
    except Exception:                                    # noqa: BLE001
        current_app.logger.exception('Backup pruning failed')

    return {'ok': True, 'filename': log.filename}
