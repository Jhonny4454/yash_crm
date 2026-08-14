"""
blueprints/settings_bp.py
=========================

Fills in the four Masters screens that were linked to "#" in base.html:

    /settings                 Settings           (settings)
    /settings/backup          Database Backup    (database_backup)
    /settings/import-export   Import / Export    (import_export)
    /settings/isp             ISP integrations   (isp_list)

WIRING (app.py, after the models are imported)
----------------------------------------------
    from blueprints.settings_bp import settings_bp
    app.register_blueprint(settings_bp)

The blueprint declares no url_prefix on its endpoints' names, so existing
templates can keep calling url_for('settings'), url_for('database_backup')
and url_for('import_export') unchanged.
"""
import csv
import io
import os
import gzip
import shutil
import subprocess
import tempfile
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    current_app, Response, send_file, abort, jsonify, has_request_context
)
from flask_login import login_required, current_user
from sqlalchemy import inspect, text
from werkzeug.utils import secure_filename

from models import (
    db, Customer, Plan, CustomerPlan, Invoice, Payment,
    Building, Area, Locality, Zone, ServiceProvider,
)
from models_ext import (
    Setting, SETTING_DEFAULTS, BackupLog, ImportJob,
    ISPCredential, ISPSyncLog,
)
from services import isp_providers

settings_bp = Blueprint('settings_bp', __name__)


# --------------------------------------------------------------------------- #
#  Local admin guard (importing app.admin_required would be circular)
# --------------------------------------------------------------------------- #
def admin_only(f):
    @wraps(f)
    def _wrap(*a, **kw):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('Only an administrator can open that screen.', 'danger')
            abort(403)
        return f(*a, **kw)
    return _wrap


def _audit(action, details):
    """Mirror of app.log_audit without the circular import.

    Tolerates being called without a request / logged-in user, and never lets
    an audit failure roll back the operation it is recording.
    """
    from models import AuditLog
    try:
        try:
            user_id = current_user.id if (current_user and
                                          current_user.is_authenticated) else None
        except Exception:                                # noqa: BLE001
            user_id = None
        ip = request.remote_addr if has_request_context() else None
        db.session.add(AuditLog(
            user_id=user_id, action=action,
            details=(details or '')[:500], ip_address=ip))
        db.session.commit()
    except Exception:                                    # noqa: BLE001
        db.session.rollback()
        current_app.logger.warning("Could not write audit log for %r", action,
                                   exc_info=True)


# =========================================================================== #
#  SETTINGS
# =========================================================================== #
def seed_settings():
    """Idempotent: create any missing default rows. Call once at startup."""
    existing = {s.key for s in Setting.query.all()}
    created = 0
    for key, value, vtype in SETTING_DEFAULTS:
        if key not in existing:
            db.session.add(Setting(key=key, value=value, value_type=vtype))
            created += 1
    if created:
        db.session.commit()
    return created


# Which keys the form is allowed to write, and how to cast them.
_EDITABLE = {
    'staff_prefix': 'str',          'staff_next_no': 'int',
    'customer_prefix': 'str',       'customer_next_no': 'int',
    'invoice_prefix': 'str',        'invoice_next_no': 'int',
    'receipt_prefix': 'str',        'receipt_next_no': 'int',
    'tax_type': 'str',              'tax_on': 'str',
    'invoice_package_price': 'str', 'happy_code_enabled': 'bool',
    'coll_amount_change': 'bool',   'coll_date_change': 'bool',
    'coll_renew_only': 'bool',      'voucher_no': 'int',
    'discount_applicable': 'bool',  'banner_link': 'str',
    'sms_template_renewal': 'str',  'sms_template_expiry': 'str',
    'invoice_due_days': 'int',      'grace_period_days': 'int',
}

_ALLOWED_VALUES = {
    'tax_type': {'Include', 'Exclude'},
    'tax_on': {'Base', 'Total'},
    'invoice_package_price': {'Customer', 'Master'},
}


@settings_bp.route('/settings', methods=['GET', 'POST'], endpoint='settings')
@login_required
@admin_only
def settings():
    seed_settings()

    if request.method == 'POST':
        errors = []
        staged = {}

        for key, vtype in _EDITABLE.items():
            if vtype == 'bool':
                # Unchecked checkboxes are absent from the payload entirely.
                staged[key] = ('bool', str(key in request.form
                                           or request.form.get(key) in
                                           ('1', 'true', 'yes', 'on', 'Enable', 'Yes')))
                continue

            if key not in request.form:
                continue
            raw = (request.form.get(key) or '').strip()

            if vtype == 'int':
                if raw == '':
                    errors.append(f"{key.replace('_', ' ').title()} cannot be blank.")
                    continue
                try:
                    n = int(raw)
                except ValueError:
                    errors.append(f"{key.replace('_', ' ').title()} must be a whole number.")
                    continue
                if n < 0:
                    errors.append(f"{key.replace('_', ' ').title()} cannot be negative.")
                    continue
                # Guard against rewinding a counter into numbers already used.
                if key == 'invoice_next_no':
                    used = _highest_numeric(Invoice.invoice_no)
                    if used and n <= used:
                        errors.append(
                            f"Invoice next number must be greater than {used}, "
                            f"which is already in use. Duplicate invoice numbers "
                            f"would break your books.")
                        continue
                staged[key] = ('int', str(n))

            elif vtype == 'str':
                allowed = _ALLOWED_VALUES.get(key)
                if allowed and raw not in allowed:
                    errors.append(f"{key} must be one of {', '.join(sorted(allowed))}.")
                    continue
                if key.endswith('_prefix') and len(raw) > 8:
                    errors.append(f"{key.replace('_', ' ').title()} is limited to 8 characters.")
                    continue
                if key.startswith('sms_template'):
                    bad = _bad_placeholders(raw)
                    if bad:
                        errors.append(
                            f"SMS template uses unknown placeholder(s): "
                            f"{', '.join(bad)}. Valid ones are "
                            f"{{name}}, {{plan}}, {{expiry}}, {{amount}}.")
                        continue
                staged[key] = ('str', raw)

        # Banner upload
        banner = request.files.get('banner_image')
        if banner and banner.filename:
            ok, msg = _save_banner(banner)
            if ok:
                staged['banner_image'] = ('str', msg)
            else:
                errors.append(msg)

        if request.form.get('remove_banner'):
            staged['banner_image'] = ('str', '')

        if errors:
            for e in errors:
                flash(e, 'danger')
            flash('Nothing was saved — fix the errors above and submit again.',
                  'warning')
            return redirect(url_for('settings'))

        for key, (vtype, val) in staged.items():
            Setting.set(key, val, vtype, user_id=current_user.id)
        db.session.commit()
        from services.messaging import invalidate_settings_cache
        invalidate_settings_cache()
        _audit('Update Settings', f"Changed {len(staged)} setting(s)")
        flash(f'{len(staged)} setting(s) saved.', 'success')
        return redirect(url_for('settings'))

    return render_template('settings/settings.html',
                           s=Setting.as_dict(),
                           title='Settings')


def _highest_numeric(column):
    highest = 0
    for (val,) in db.session.query(column).all():
        digits = ''.join(c for c in (val or '') if c.isdigit())
        if digits:
            highest = max(highest, int(digits[-7:]))
    return highest


def _bad_placeholders(template):
    import re
    allowed = {'name', 'plan', 'expiry', 'amount', 'invoice', 'mobile'}
    found = set(re.findall(r'\{(\w+)\}', template or ''))
    return sorted(found - allowed)


def _save_banner(fs):
    allowed = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
    ext = os.path.splitext(fs.filename)[1].lower()
    if ext not in allowed:
        return False, f"Banner must be one of {', '.join(sorted(allowed))}."
    fs.stream.seek(0, os.SEEK_END)
    size = fs.stream.tell()
    fs.stream.seek(0)
    if size > 2 * 1024 * 1024:
        return False, "Banner image must be under 2 MB."
    name = f"banner_{datetime.utcnow():%Y%m%d%H%M%S}{ext}"
    folder = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(folder, exist_ok=True)
    fs.save(os.path.join(folder, secure_filename(name)))
    return True, name


@settings_bp.route('/settings/sms-templates', endpoint='sms_templates')
@login_required
@admin_only
def sms_templates():
    return render_template('settings/sms_templates.html',
                           s=Setting.as_dict(), title='SMS Templates')


# =========================================================================== #
#  DATABASE BACKUP
# =========================================================================== #
def _backup_dir():
    d = os.path.join(current_app.root_path, 'backups')
    os.makedirs(d, exist_ok=True)
    return d


@settings_bp.route('/settings/backup', endpoint='database_backup')
@login_required
@admin_only
def database_backup():
    logs = (BackupLog.query.order_by(BackupLog.created_at.desc())
            .limit(50).all())
    # Show what is actually on disk, not just what the log claims.
    on_disk = {}
    for f in os.listdir(_backup_dir()):
        p = os.path.join(_backup_dir(), f)
        if os.path.isfile(p):
            on_disk[f] = os.path.getsize(p)

    return render_template('settings/backup.html',
                           logs=logs, on_disk=on_disk,
                           dialect=db.engine.dialect.name,
                           title='Database Backup')


@settings_bp.route('/settings/backup/run', methods=['POST'],
                   endpoint='backup_run')
@login_required
@admin_only
def backup_run():
    dialect = db.engine.dialect.name
    stamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    entry = BackupLog(kind='manual', status='running',
                      created_by_id=current_user.id)
    db.session.add(entry)
    db.session.commit()

    try:
        if dialect == 'sqlite':
            src = db.engine.url.database
            if not src or not os.path.exists(src):
                raise FileNotFoundError(f"SQLite file not found at {src}")
            fname = f"backup-{stamp}.sqlite.gz"
            dest = os.path.join(_backup_dir(), fname)
            # sqlite3 .backup keeps the file consistent under concurrent writes.
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
            import sqlite3
            with sqlite3.connect(src) as s, sqlite3.connect(tmp_path) as d:
                s.backup(d)
            with open(tmp_path, 'rb') as fin, gzip.open(dest, 'wb') as fout:
                shutil.copyfileobj(fin, fout)
            os.unlink(tmp_path)

        elif dialect == 'mysql':
            url = db.engine.url
            fname = f"backup-{stamp}.sql.gz"
            dest = os.path.join(_backup_dir(), fname)
            cmd = ['mysqldump',
                   '-h', url.host or 'localhost',
                   '-P', str(url.port or 3306),
                   '-u', url.username or 'root',
                   '--single-transaction', '--quick',
                   '--routines', '--triggers',
                   url.database]
            env = dict(os.environ)
            if url.password:
                env['MYSQL_PWD'] = url.password      # avoids password in argv
            with gzip.open(dest, 'wb') as fout:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=env,
                                      timeout=600, check=False)
                if proc.returncode != 0:
                    raise RuntimeError(
                        proc.stderr.decode('utf-8', 'replace')[:400]
                        or 'mysqldump failed')
                fout.write(proc.stdout)

        elif dialect == 'postgresql':
            url = db.engine.url
            fname = f"backup-{stamp}.dump"
            dest = os.path.join(_backup_dir(), fname)
            env = dict(os.environ)
            if url.password:
                env['PGPASSWORD'] = url.password
            cmd = ['pg_dump', '-Fc', '-h', url.host or 'localhost',
                   '-p', str(url.port or 5432), '-U', url.username or 'postgres',
                   '-f', dest, url.database]
            proc = subprocess.run(cmd, capture_output=True, env=env,
                                  timeout=600, check=False)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.decode('utf-8', 'replace')[:400])
        else:
            raise RuntimeError(f"Backup is not implemented for '{dialect}'.")

        entry.filename = fname
        entry.size_bytes = os.path.getsize(dest)
        entry.status = 'success'
        entry.message = f"{dialect} backup completed"
        db.session.commit()
        _audit('Database Backup', f"{fname} ({entry.size_human})")
        flash(f'Backup created: {fname} ({entry.size_human})', 'success')

    except FileNotFoundError as e:
        db.session.rollback()
        entry = db.session.get(BackupLog, entry.id)
        entry.status, entry.message = 'failed', str(e)[:500]
        db.session.commit()
        flash(f'Backup failed: {e}. If you are on MySQL, check that '
              f'mysqldump is installed and on PATH.', 'danger')
    except subprocess.TimeoutExpired:
        db.session.rollback()
        entry = db.session.get(BackupLog, entry.id)
        entry.status, entry.message = 'failed', 'Timed out after 10 minutes'
        db.session.commit()
        flash('Backup timed out after 10 minutes.', 'danger')
    except Exception as e:                               # noqa: BLE001
        db.session.rollback()
        entry = db.session.get(BackupLog, entry.id)
        entry.status, entry.message = 'failed', str(e)[:500]
        db.session.commit()
        current_app.logger.exception("Backup failed")
        flash(f'Backup failed: {e}', 'danger')

    return redirect(url_for('database_backup'))


@settings_bp.route('/settings/backup/download/<path:filename>',
                   endpoint='backup_download')
@login_required
@admin_only
def backup_download(filename):
    safe = secure_filename(filename)
    path = os.path.join(_backup_dir(), safe)
    if not os.path.isfile(path):
        flash('That backup file no longer exists on disk.', 'danger')
        return redirect(url_for('database_backup'))
    _audit('Download Backup', safe)
    return send_file(path, as_attachment=True, download_name=safe)


@settings_bp.route('/settings/backup/delete/<path:filename>', methods=['POST'],
                   endpoint='backup_delete')
@login_required
@admin_only
def backup_delete(filename):
    safe = secure_filename(filename)
    path = os.path.join(_backup_dir(), safe)
    if os.path.isfile(path):
        os.unlink(path)
        BackupLog.query.filter_by(filename=safe).update({'status': 'failed',
                                                         'message': 'Deleted'})
        db.session.commit()
        _audit('Delete Backup', safe)
        flash(f'{safe} deleted.', 'success')
    else:
        flash('File not found.', 'warning')
    return redirect(url_for('database_backup'))


# =========================================================================== #
#  IMPORT / EXPORT
# =========================================================================== #
EXPORTS = {
    'customers': dict(label='Customers', model=Customer),
    'plans': dict(label='Plans', model=Plan),
    'invoices': dict(label='Invoices', model=Invoice),
    'payments': dict(label='Payments', model=Payment),
    'buildings': dict(label='Buildings', model=Building),
    'areas': dict(label='Areas', model=Area),
    'localities': dict(label='Localities', model=Locality),
}

IMPORT_SPECS = {
    'buildings': dict(label='Buildings', model=Building,
                      required=['name'], unique='name'),
    'areas': dict(label='Areas', model=Area,
                  required=['name'], unique='name'),
    'localities': dict(label='Localities', model=Locality,
                       required=['name'], unique='name'),
    'plans': dict(label='Plans', model=Plan,
                  required=['name', 'speed_mbps', 'price_monthly'],
                  unique='name'),
    'customers': dict(label='Customers', model=Customer,
                      required=['first_name', 'last_name', 'mobile'],
                      unique='mobile'),
}


@settings_bp.route('/settings/import-export', endpoint='import_export')
@login_required
@admin_only
def import_export():
    counts = {k: v['model'].query.count() for k, v in EXPORTS.items()}
    jobs = (ImportJob.query.order_by(ImportJob.created_at.desc())
            .limit(20).all())
    return render_template('settings/import_export.html',
                           exports=EXPORTS, imports=IMPORT_SPECS,
                           counts=counts, jobs=jobs,
                           title='Import / Export')


@settings_bp.route('/settings/export/<target>', endpoint='export_csv')
@login_required
@admin_only
def export_csv(target):
    spec = EXPORTS.get(target)
    if not spec:
        flash('Unknown export type.', 'danger')
        return redirect(url_for('import_export'))

    model = spec['model']
    cols = [c.key for c in inspect(model).columns
            if c.key not in ('password_hash',)]      # never export hashes

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for row in model.query.yield_per(500):
        w.writerow([_csv_safe(getattr(row, c, '')) for c in cols])

    _audit('Export CSV', f"{target} ({model.query.count()} rows)")
    return Response(
        buf.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition':
                 f'attachment; filename={target}-{date.today()}.csv'})


def _csv_safe(v):
    """Neutralise CSV/formula injection before Excel opens the file."""
    if v is None:
        return ''
    s = str(v)
    if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + s
    return s


@settings_bp.route('/settings/import/<target>/template', endpoint='import_template')
@login_required
@admin_only
def import_template(target):
    spec = IMPORT_SPECS.get(target)
    if not spec:
        abort(404)
    cols = [c.key for c in inspect(spec['model']).columns
            if c.key not in ('id', 'created_at', 'updated_at', 'password_hash')]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerow(['' for _ in cols])
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':
                             f'attachment; filename={target}-template.csv'})


@settings_bp.route('/settings/import/<target>', methods=['POST'],
                   endpoint='import_csv')
@login_required
@admin_only
def import_csv(target):
    spec = IMPORT_SPECS.get(target)
    if not spec:
        flash('Unknown import type.', 'danger')
        return redirect(url_for('import_export'))

    fs = request.files.get('file')
    if not fs or not fs.filename:
        flash('Choose a CSV file first.', 'danger')
        return redirect(url_for('import_export'))
    if not fs.filename.lower().endswith(('.csv', '.txt')):
        flash('Upload a .csv file. If you have an .xls/.xlsx, use '
              '"Save As -> CSV" in Excel first.', 'danger')
        return redirect(url_for('import_export'))

    raw = fs.read()
    if len(raw) > 10 * 1024 * 1024:
        flash('File is larger than 10 MB. Split it into smaller files.', 'danger')
        return redirect(url_for('import_export'))

    try:
        text_data = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text_data = raw.decode('latin-1')
        except UnicodeDecodeError:
            flash('Could not read the file encoding. Save it as UTF-8 CSV.',
                  'danger')
            return redirect(url_for('import_export'))

    reader = csv.DictReader(io.StringIO(text_data))
    if not reader.fieldnames:
        flash('The file has no header row.', 'danger')
        return redirect(url_for('import_export'))

    headers = {h.strip().lower() for h in reader.fieldnames if h}
    missing = [c for c in spec['required'] if c not in headers]
    if missing:
        flash(f"Missing required column(s): {', '.join(missing)}. "
              f"Download the template for the exact header row.", 'danger')
        return redirect(url_for('import_export'))

    model = spec['model']
    valid_cols = {c.key for c in inspect(model).columns} - {'id'}
    unique_col = spec.get('unique')

    job = ImportJob(target=target, filename=secure_filename(fs.filename),
                    status='running', created_by_id=current_user.id)
    db.session.add(job)
    db.session.commit()

    ok = failed = 0
    errors = io.StringIO()
    ew = csv.writer(errors)
    ew.writerow(['row', 'reason'] + list(reader.fieldnames))

    dry_run = bool(request.form.get('dry_run'))

    for n, row in enumerate(reader, start=2):
        clean = {(k or '').strip().lower(): (v.strip() if isinstance(v, str) else v)
                 for k, v in row.items() if k}

        problem = None
        for col in spec['required']:
            if not clean.get(col):
                problem = f"'{col}' is blank"
                break

        if not problem and unique_col:
            existing = model.query.filter(
                getattr(model, unique_col) == clean.get(unique_col)).first()
            if existing:
                problem = f"{unique_col} '{clean.get(unique_col)}' already exists"

        payload = {}
        if not problem:
            try:
                for k, v in clean.items():
                    if k in valid_cols and v not in (None, ''):
                        payload[k] = _coerce(model, k, v)
            except (ValueError, InvalidOperation) as e:
                problem = str(e)

        if problem:
            failed += 1
            ew.writerow([n, problem] + [row.get(h, '') for h in reader.fieldnames])
            continue

        if not dry_run:
            try:
                db.session.add(model(**payload))
                db.session.flush()
                ok += 1
            except Exception as e:                       # noqa: BLE001
                db.session.rollback()
                failed += 1
                ew.writerow([n, f"db error: {e.__class__.__name__}"]
                            + [row.get(h, '') for h in reader.fieldnames])
        else:
            ok += 1

    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()

    job = db.session.get(ImportJob, job.id)
    job.total_rows = ok + failed
    job.ok_rows = ok
    job.failed_rows = failed
    job.error_report = errors.getvalue() if failed else None
    job.status = 'done'
    db.session.commit()

    _audit('Import CSV', f"{target}: {ok} ok, {failed} failed"
                         + (' (dry run)' if dry_run else ''))

    if dry_run:
        flash(f'Dry run: {ok} row(s) would import, {failed} would fail. '
              f'Nothing was saved.',
              'info' if failed == 0 else 'warning')
    elif failed:
        flash(f'Imported {ok} row(s); {failed} failed. Download the error '
              f'report to see why.', 'warning')
    else:
        flash(f'Imported {ok} row(s) successfully.', 'success')

    return redirect(url_for('import_export'))


def _coerce(model, column, value):
    """Cast a CSV string to the column's python type, with a readable error."""
    col = inspect(model).columns.get(column)
    if col is None:
        return value
    t = col.type.__class__.__name__.lower()
    try:
        if 'integer' in t:
            return int(float(value))
        if 'numeric' in t or 'decimal' in t or 'float' in t:
            return Decimal(str(value))
        if 'boolean' in t:
            return str(value).strip().lower() in ('1', 'true', 'yes', 'y', 'on')
        if t == 'date':
            for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y'):
                try:
                    return datetime.strptime(value, fmt).date()
                except ValueError:
                    continue
            raise ValueError(f"'{column}': '{value}' is not a recognised date "
                             f"(use YYYY-MM-DD)")
        if 'datetime' in t:
            return datetime.fromisoformat(value)
    except (ValueError, InvalidOperation):
        raise ValueError(f"'{column}': '{value}' is not a valid "
                         f"{t.replace('_', ' ')}")
    return value


@settings_bp.route('/settings/import/job/<int:job_id>/errors',
                   endpoint='import_errors')
@login_required
@admin_only
def import_errors(job_id):
    job = db.session.get(ImportJob, job_id)
    if not job or not job.error_report:
        flash('No error report for that job.', 'warning')
        return redirect(url_for('import_export'))
    return Response(job.error_report, mimetype='text/csv',
                    headers={'Content-Disposition':
                             f'attachment; filename=import-errors-{job_id}.csv'})


# =========================================================================== #
#  ISP INTEGRATIONS
# =========================================================================== #
@settings_bp.route('/settings/isp', endpoint='isp_list')
@login_required
@admin_only
def isp_list():
    creds = ISPCredential.query.order_by(ISPCredential.id).all()
    recent = (ISPSyncLog.query.order_by(ISPSyncLog.created_at.desc())
              .limit(30).all())
    return render_template('isp/list.html', creds=creds, recent=recent,
                           title='ISP Integrations')


@settings_bp.route('/settings/isp/add', methods=['GET', 'POST'],
                   endpoint='isp_add')
@settings_bp.route('/settings/isp/<int:cred_id>/edit', methods=['GET', 'POST'],
                   endpoint='isp_edit')
@login_required
@admin_only
def isp_form(cred_id=None):
    cred = db.session.get(ISPCredential, cred_id) if cred_id else None
    if cred_id and cred is None:
        abort(404)

    if request.method == 'POST':
        errors = []
        base_url = (request.form.get('base_url') or '').strip().rstrip('/')
        driver = (request.form.get('driver') or '').strip()
        sp_id = request.form.get('service_provider_id', type=int)

        if not base_url.startswith(('http://', 'https://')):
            errors.append('Base URL must start with http:// or https://')
        if base_url.startswith('http://') and not request.form.get('allow_http'):
            errors.append('Refusing to send API credentials over plain HTTP. '
                          'Use https://, or tick "allow insecure HTTP" if this '
                          'is a lab box on your own LAN.')
        if driver not in dict(isp_providers.available_drivers()):
            errors.append('Choose a valid driver.')
        if not sp_id:
            errors.append('Select which service provider this belongs to.')

        secret = request.form.get('secret') or ''
        if not cred and not secret and not request.form.get('api_key'):
            errors.append('Enter an API key or secret.')

        options_raw = (request.form.get('options_json') or '').strip()
        options = {}
        if options_raw:
            import json
            try:
                options = json.loads(options_raw)
                if not isinstance(options, dict):
                    raise ValueError
            except (ValueError, TypeError):
                errors.append('Driver options must be a JSON object, '
                              'e.g. {"site": "AIROLI", "nas": "NAS1"}')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('isp/form.html', cred=cred,
                                   drivers=isp_providers.available_drivers(),
                                   providers=ServiceProvider.query.all(),
                                   form=request.form,
                                   title='ISP Integration')

        if cred is None:
            cred = ISPCredential()
            db.session.add(cred)

        cred.service_provider_id = sp_id
        cred.driver = driver
        cred.label = (request.form.get('label') or '').strip() or None
        cred.base_url = base_url
        cred.username = (request.form.get('username') or '').strip() or None
        cred.api_key = (request.form.get('api_key') or '').strip() or None
        cred.options = options
        cred.verify_ssl = bool(request.form.get('verify_ssl'))
        cred.timeout_seconds = request.form.get('timeout_seconds', type=int) or 20
        cred.is_active = bool(request.form.get('is_active'))
        cred.is_sandbox = bool(request.form.get('is_sandbox'))

        if secret:
            try:
                cred.set_secret(secret)
            except RuntimeError as e:
                flash(str(e), 'danger')
                return redirect(url_for('isp_list'))

        db.session.commit()
        _audit('ISP Credential Saved',
               f"{cred.driver} @ {cred.base_url}")   # never log the secret
        flash('Integration saved. Use "Test" to verify it before relying on it.',
              'success')
        return redirect(url_for('isp_list'))

    return render_template('isp/form.html', cred=cred,
                           drivers=isp_providers.available_drivers(),
                           providers=ServiceProvider.query.all(),
                           form={}, title='ISP Integration')


@settings_bp.route('/settings/isp/<int:cred_id>/test', methods=['POST'],
                   endpoint='isp_test')
@login_required
@admin_only
def isp_test(cred_id):
    cred = db.session.get(ISPCredential, cred_id) or abort(404)
    result = isp_providers.test_credential(cred)
    if result.ok:
        flash(f'Connection OK ({result.duration_ms} ms).', 'success')
    else:
        flash(f'Connection failed: {result.message}', 'danger')
    return redirect(url_for('isp_list'))


@settings_bp.route('/settings/isp/<int:cred_id>/delete', methods=['POST'],
                   endpoint='isp_delete')
@login_required
@admin_only
def isp_delete(cred_id):
    cred = db.session.get(ISPCredential, cred_id) or abort(404)
    label = cred.label or cred.base_url
    ISPSyncLog.query.filter_by(credential_id=cred.id).update(
        {'credential_id': None})
    db.session.delete(cred)
    db.session.commit()
    _audit('ISP Credential Deleted', label)
    flash('Integration removed.', 'success')
    return redirect(url_for('isp_list'))


# =========================================================================== #
#  REGISTRATION
# =========================================================================== #
def register(app):
    """
    Call this from app.py instead of app.register_blueprint():

        from blueprints.settings_bp import register as register_settings
        register_settings(app)

    It registers the blueprint and then adds flat endpoint aliases, so that
    existing templates can keep calling url_for('settings'),
    url_for('database_backup') and url_for('import_export') without the
    'settings_bp.' prefix.
    """
    app.register_blueprint(settings_bp)
    for rule in list(app.url_map.iter_rules()):
        if not rule.endpoint.startswith('settings_bp.'):
            continue
        short = rule.endpoint.split('.', 1)[1]
        if short in app.view_functions:
            continue
        app.add_url_rule(
            rule.rule, short, app.view_functions[rule.endpoint],
            methods=sorted(rule.methods - {'HEAD', 'OPTIONS'}))

    # init_database() owns data seeding after it has created/synchronised the
    # schema.  Doing it here runs while app.py is still importing blueprints,
    # before a new database has its ``settings`` table, and used to emit the
    # stale "run migrate_v2.py" warning on every clean start.
