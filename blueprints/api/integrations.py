"""
blueprints/api/integrations.py
==============================

JSON wrappers for the five admin screens that had no REST endpoints:
Settings, Database Backup, Import/Export, Bulk WhatsApp + Message Log, and
ISP Integrations.

These are thin adapters over the code that already backs the Jinja2 admin:
  services.messaging      (send_whatsapp, normalize_phone, MessageLog)
  services.isp_providers  (provision, test_credential, credential rows)
  blueprints.settings_bp  (seed_settings, backup dir, CSV import/export)
  models_ext.Setting / ISPCredential / ISPSyncLog / BackupLog

Nothing here re-implements business logic; it only exposes what exists.
"""
import csv
import io
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, current_app, request, send_file
from sqlalchemy import func

from models import (Customer, CustomerPlan, Invoice, MessageLog, Payment,
                    Plan, Product, Expense, ServiceProvider, db)
from models_ext import (BackupLog, ImportJob, ISPCredential, ISPSyncLog,
                        Setting, SETTING_DEFAULTS)

from .utils import (admin_required, body, current_staff_id, fail, iso, ok,
                    paginate, staff_required)

bp = Blueprint('api_integrations', __name__)

KNOWN_DRIVERS = ('log2space', 'synnefo', '24online', 'xceednet')
DRIVER_ALIASES = {'l2s': 'log2space'}


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #
@bp.get('/settings')
@staff_required
def settings_list():
    """Every setting, plus how it should be edited.

    The shape each row is drawn in comes from settings_schema, not from the
    screen, so the dropdown the operator sees and the validation the save
    runs are the same list. A settings page that offers a value the server
    then refuses is the classic way this table ends up holding something the
    rest of the application cannot read.
    """
    from .settings_schema import GROUPS, describe, is_secret

    try:
        from blueprints.settings_bp import seed_settings
        seed_settings()
    except Exception:
        pass

    rows = Setting.query.order_by(Setting.key).all()
    out = []
    for r in rows:
        value_type = r.value_type or 'str'
        entry = describe(r.key, value_type)
        entry['value_type'] = value_type
        entry['updated_at'] = iso(r.updated_at)

        stored = r.value if r.value is not None else ''
        if is_secret(r.key):
            # Never ship a live credential to the browser. The page shows
            # whether one is set and writes only when the admin types a new
            # one - so opening Settings and pressing Save cannot silently
            # blank the WhatsApp token or the Cashfree key, which is what
            # round-tripping a password field through the DOM invites.
            entry['value'] = ''
            entry['has_value'] = bool(stored)
        else:
            entry['value'] = stored
            entry['has_value'] = stored != ''
        out.append(entry)

    out.sort(key=lambda e: (e['group_order'], e['order'], e['key']))
    return ok(out, groups=[{'key': k, 'label': label, 'hint': hint}
                           for k, label, hint in GROUPS])


@bp.put('/settings')
@admin_required
def settings_update():
    """Body: {settings: [{key, value}, ...]}.

    Every value is validated against the same schema the screen renders from.
    A bad value is a 400 naming the field, not a silent skip: the old version
    dropped anything it did not recognise and still answered
    {"status": "saved"}, so a rejected setting looked saved until the page was
    reloaded.
    """
    from .settings_schema import coerce, is_secret

    data = body()
    items = data.get('settings')
    if not isinstance(items, list):
        return fail('settings_list_required', 400)

    rows = {r.key: r for r in Setting.query.all()}
    types = {k: t for k, _v, t in SETTING_DEFAULTS}
    known = set(types) | set(rows)

    errors, unknown, staged, skipped = [], [], {}, []

    for item in items:
        key = (item or {}).get('key') or ''
        if key not in known:
            unknown.append(key)
            continue

        raw = (item or {}).get('value')

        # An empty secret means "leave it alone", not "erase it".
        if is_secret(key) and (raw is None or str(raw).strip() == ''):
            skipped.append(key)
            continue

        value_type = (rows[key].value_type if key in rows else types.get(key)) or 'str'
        value, error = coerce(key, raw, value_type)
        if error:
            errors.append(error)
            continue
        staged[key] = (value, value_type)

    if errors:
        return fail('invalid_settings', 400, detail=' '.join(errors),
                    errors=errors)
    if unknown:
        return fail('unknown_settings', 400,
                    detail='Not a setting this system has: '
                           + ', '.join(sorted(set(unknown))),
                    keys=sorted(set(unknown)))

    saved = 0
    for key, (value, value_type) in staged.items():
        row = rows.get(key)
        if not row:
            row = Setting(key=key, value_type=value_type)
            db.session.add(row)
        row.value = value
        row.updated_by_id = current_staff_id()
        saved += 1

    db.session.commit()
    # The per-request settings cache was filled BEFORE this write, so the
    # response would otherwise report the values we just replaced.
    from services.messaging import invalidate_settings_cache
    invalidate_settings_cache()
    return ok({'status': 'saved', 'count': saved,
               'unchanged_secrets': skipped})


@bp.post('/notifications/templates/restore-defaults')
@admin_required
def restore_templates():
    """Put the standard message templates back and switch them on.

    Sending a bill fails with "no active template" when the row is missing,
    deactivated or has an empty body - three different states that look the
    same from the outside. This repairs all three and reports what it changed.
    """
    from services.messaging import restore_default_templates

    try:
        result = restore_default_templates()
    except Exception as exc:
        db.session.rollback()
        return fail('restore_failed', 500, detail=str(exc)[:200])

    changed = len(result['created']) + len(result['reactivated']) + len(result['refilled'])
    result['changed'] = changed
    result['detail'] = ('Every standard template is present and active.'
                        if not changed else
                        f'{changed} template(s) repaired.')
    return ok(result)


def _redact_key(value):
    """Enough to recognise a key, not enough to use it."""
    text = str(value or '')
    if not text:
        return ''
    if len(text) <= 8:
        return '*' * len(text)
    return f'{text[:4]}{"*" * max(3, len(text) - 8)}{text[-4:]}'


def _template_health():
    """Which message templates can actually be sent, and which cannot."""
    from models import MessageTemplate
    from services.messaging import DEFAULT_TEMPLATES

    expected = {spec['template_type'] for spec in DEFAULT_TEMPLATES}
    usable, broken = set(), []
    for row in MessageTemplate.query.all():
        if not row.is_active:
            broken.append({'type': row.template_type, 'why': 'switched off'})
        elif not (row.body or '').strip():
            broken.append({'type': row.template_type, 'why': 'empty body'})
        else:
            usable.add(row.template_type)

    missing = sorted(expected - usable - {b['type'] for b in broken})

    # The ones mapped to an approved Meta template, so the tester can offer
    # them by name. These are the only messages that reach a customer who has
    # not written to the business today - which is every customer a bill,
    # reminder or expiry notice goes to.
    from services.messaging import DOCUMENT_HEADER_TEMPLATE_NAMES
    linked = []
    for row in MessageTemplate.query.order_by(MessageTemplate.template_type).all():
        name = (getattr(row, 'meta_template_name', '') or '').strip()
        if not name or not row.is_active:
            continue
        linked.append({
            'template_type': row.template_type,
            'meta_template_name': name,
            'language': (row.meta_language or 'en').strip() or 'en',
            # Flagged because these two cannot be tested until PUBLIC_BASE_URL
            # gives Meta somewhere to fetch the PDF from.
            'needs_pdf': name in DOCUMENT_HEADER_TEMPLATE_NAMES,
        })

    return {
        'usable': sorted(usable),
        'broken': broken,
        'missing': missing,
        'linked': linked,
        # The one that matters most: no usable bill template means the Send
        # Bill button fails for every customer.
        'bill_ready': 'bill' in usable,
        'needs_repair': bool(missing or broken),
    }


@bp.get('/settings/whatsapp/status')
@staff_required
def whatsapp_status():
    """What the gateway is configured to do, without sending anything."""
    from services.messaging import (is_configured, provider_endpoint,
                                    provider_sends_templates, _setting)

    enabled = _setting('wa_enabled') in ('1', 'true', 'True', 'yes', 'on')
    provider = _setting('wa_provider', 'generic').lower()
    endpoint_url = provider_endpoint()
    has_key = bool((_setting('wa_api_token') or '').strip())

    blocking = []
    if not enabled:
        blocking.append('WhatsApp sending is switched off.')
    if not endpoint_url:
        blocking.append('No API URL is set, and this provider has no built-in one.')
    if not has_key:
        blocking.append('No API key is set.')

    # Not "blocking" - a test message to a handset that has just messaged you
    # WILL arrive on this gateway. It is the thing that stops every BILL from
    # arriving, which is worse and much harder to notice, so it is reported
    # separately rather than buried in the same list.
    template_capable = provider_sends_templates(provider)
    warnings = []
    if not template_capable:
        warnings.append(
            'This gateway can only send free text. Meta delivers free text '
            'only to customers who messaged you in the last 24 hours, so '
            'bills, renewal notices and expiry reminders sent through it are '
            'accepted by the gateway and delivered to nobody. Approved '
            'templates are the only form that reaches a customer who has not '
            'written to you - switch the gateway to "Meta Cloud API (direct)" '
            'to use them.')

    from services.messaging import PROVIDER_ENDPOINTS

    return ok({
        'enabled': enabled,
        'provider': provider,
        'endpoint': endpoint_url,
        'api_url': _setting('wa_api_url') or '',
        'has_api_key': has_key,
        # Redacted, but the PREFIX survives - and for WabAssist the prefix is
        # the whole diagnosis: `key_` is the Key ID and never authenticates,
        # `ua_` is the credential. Without this the panel could say "API key:
        # Set" while holding a value that can never work.
        'api_key_hint': _redact_key(_setting('wa_api_token') or ''),
        'api_key_looks_wrong': (
            (_setting('wa_provider', 'generic') or '').lower() == 'webassist'
            and (_setting('wa_api_token') or '').startswith('key_')),
        'country_code': _setting('wa_country_code') or '91',
        'ready': is_configured() and has_key,
        'blocking': blocking,
        'template_capable': template_capable,
        'warnings': warnings,
        # So the form can offer the ones with a built-in address and mark the
        # rest as needing a URL.
        'instance_id': _setting('wa_instance_id') or '',
        # Template health belongs here, not on the Notifications screen: a
        # missing template is a SENDING problem, and this is the panel that
        # answers "can we send?". (It also lives in a different table from the
        # notification templates that screen edits, which is exactly the sort
        # of confusion that put the repair button on the wrong page.)
        'templates': _template_health(),
        'providers': [
            {'id': 'meta_cloud', 'label': 'Meta Cloud API (direct)',
             'endpoint': '', 'needs_instance': True,
             'note': 'Sends straight to Meta. Needs the Phone Number ID and a '
                     'permanent access token from Meta Business.'},
            {'id': 'webassist', 'label': 'WabAssist',
             'endpoint': PROVIDER_ENDPOINTS.get('webassist', ''),
             'needs_instance': False, 'note': ''},
            {'id': 'generic', 'label': 'Other / custom gateway',
             'endpoint': '', 'needs_instance': False,
             'note': 'Set the API URL and payload template in the settings above.'},
        ],
    })


@bp.put('/settings/whatsapp')
@admin_required
def whatsapp_configure():
    """Write the handful of settings that decide whether sending works.

    The Settings screen already edits every setting as a raw key/value row, but
    finding `wa_enabled`, `wa_provider`, `wa_api_token` and `wa_api_url` among
    a hundred alphabetical keys - and knowing that the first must be exactly
    "1" - is a scavenger hunt. These four belong together on the screen that
    tests them.
    """
    from services.messaging import PROVIDER_ENDPOINTS

    data = body()
    fields = {
        # 'True'/'False', matching SETTING_DEFAULTS and what the Settings
        # screen writes. Every reader accepts either, but two screens writing
        # the same key in two encodings is how a value ends up looking wrong
        # on one of them.
        'wa_enabled': 'True' if data.get('enabled') else 'False',
        'wa_provider': (data.get('provider') or 'generic').strip().lower(),
        'wa_country_code': (str(data.get('country_code') or '91').strip()
                            .lstrip('+') or '91'),
    }

    # Meta puts the phone number id in the URL path, so it is configuration,
    # not a credential.
    if 'instance_id' in data:
        fields['wa_instance_id'] = (data.get('instance_id') or '').strip()

    # An empty API key means "leave the stored one alone", so re-saving the
    # form does not wipe a key the operator cannot see (it renders masked).
    # Refuse a webhook receiver up front, rather than letting it "succeed".
    candidate = (data.get('api_url') or '').strip()
    if candidate:
        from services.messaging import _looks_like_a_webhook
        if _looks_like_a_webhook(candidate):
            return fail('webhook_url_given', 400,
                        detail='That looks like a webhook URL - the address '
                               'Meta calls to deliver messages INTO your '
                               'provider. It cannot send. Use the provider\'s '
                               'send endpoint, or choose Meta Cloud API.')

    token = (data.get('api_token') or '').strip()
    if token:
        # A key containing mask characters is one that was copied off a
        # screen, not out of the provider. This app displays the stored key
        # redacted ("key_*******ZQGs") in the test output, so pasting that
        # back is an easy and completely invisible mistake - it saves without
        # complaint and every send then fails with "Invalid API key".
        if any(ch in token for ch in ('*', '\u2022', '\u00b7', '…')):
            return fail('masked_key', 400,
                        detail='That looks like the masked version of a key '
                               '(it contains * or bullet characters). Copy the '
                               'real key from your provider, not from this '
                               'screen.')
        if ' ' in token or '\n' in token:
            return fail('key_has_whitespace', 400,
                        detail='The key contains a space or line break. Copy '
                               'it again without the surrounding text.')

        # WabAssist shows TWO values on its API Docs page and only one of them
        # authenticates. The prominent one, labelled "Key ID", starts `key_`
        # and is an identifier; the credential starts `ua_` and is the one
        # their docs mean by "your actual key". Pasting the Key ID saves
        # cleanly and then fails every send with "Invalid API key", which
        # names the right field and gives no hint that it is the wrong VALUE.
        if fields['wa_provider'] == 'webassist' and token.startswith('key_'):
            return fail('key_id_not_secret', 400,
                        detail='That is the Key ID, not the API key. WabAssist '
                               'authenticates with the value beginning "ua_" - '
                               'their docs say "Use saved key (ua_...) for '
                               'auth". If you do not have it saved, use '
                               'Generate New Key on their API Docs page; the '
                               '"ua_" value is shown once when it is created.')

        fields['wa_api_token'] = token

    # Blank URL is meaningful: fall back to the provider's built-in endpoint.
    if 'api_url' in data:
        fields['wa_api_url'] = (data.get('api_url') or '').strip()

    if fields['wa_provider'] == 'meta_cloud':
        phone_id = fields.get('wa_instance_id') or _setting_value('wa_instance_id')
        if not phone_id and not (fields.get('wa_api_url') or _setting_value('wa_api_url')):
            return fail('phone_number_id_required', 400,
                        detail='Meta Cloud API needs the Phone Number ID. Find '
                               'it in Meta Business > WhatsApp > API Setup.')
    elif not PROVIDER_ENDPOINTS.get(fields['wa_provider']) and not (
            fields.get('wa_api_url') or _setting_value('wa_api_url')):
        return fail('api_url_required', 400,
                    detail=f"The '{fields['wa_provider']}' provider has no "
                           f"built-in address, so an API URL is required.")

    for key, value in fields.items():
        row = Setting.query.filter_by(key=key).first()
        if not row:
            row = Setting(key=key, value_type='str')
            db.session.add(row)
        row.value = value
        row.updated_by_id = current_staff_id()
    db.session.commit()

    from services.messaging import (invalidate_settings_cache, is_configured,
                                    provider_endpoint)
    invalidate_settings_cache()
    return ok({
        'saved': sorted(fields),
        'ready': is_configured() and bool(_setting_value('wa_api_token')),
        'endpoint': provider_endpoint(),
    })


def _setting_value(key):
    row = Setting.query.filter_by(key=key).first()
    return (row.value or '').strip() if row else ''


@bp.post('/settings/whatsapp/test')
@admin_required
def whatsapp_test():
    """Send one real message and report exactly what happened.

    Admin-only because it spends a message and reveals the gateway's reply.
    """
    data = body()
    mobile = (data.get('mobile') or '').strip()
    if not mobile:
        return fail('mobile_required', 400,
                    detail='Enter the number to send the test to.')

    # `template_type` switches the test from free text to an APPROVED
    # TEMPLATE. Free text only ever proved the gateway answers - it arrives
    # for anyone who messaged the business today and nobody else, which is the
    # case that was already working. Templates are the half that decides
    # whether bills reach anybody.
    template_type = (data.get('template_type') or '').strip()
    if template_type:
        from services.messaging import send_test_template
        return ok(send_test_template(mobile, template_type))

    from services.messaging import send_test_message
    result = send_test_message(mobile, (data.get('message') or '').strip() or None)

    # Always 200: a rejected send is a successful diagnosis, and the screen
    # needs the body either way. `status` inside carries the verdict.
    return ok(result)


# --------------------------------------------------------------------------- #
#  Backups
# --------------------------------------------------------------------------- #
def _backup_dir():
    try:
        from blueprints.settings_bp import _backup_dir as real
        return real()
    except Exception:
        folder = os.path.join(current_app.root_path, 'backups')
        os.makedirs(folder, exist_ok=True)
        return folder


@bp.get('/settings/backups')
@staff_required
def backup_list():
    rows, meta = paginate(BackupLog.query.order_by(BackupLog.created_at.desc()))
    return ok([{
        'id': b.id,
        'filename': b.filename or '',
        'size_bytes': b.size_bytes or 0,
        'size_human': b.size_human,
        'kind': b.kind,
        'status': b.status,
        'message': b.message or '',
        'created_at': iso(b.created_at),
        'download_url': (f'/api/v1/settings/backups/{b.id}/download'
                         if b.status == 'success' else None),
    } for b in rows], meta=meta)


@bp.post('/settings/backups')
@admin_required
def backup_create():
    log = BackupLog(kind='manual', status='running',
                    created_by_id=current_staff_id())
    db.session.add(log)
    db.session.commit()

    try:
        from blueprints.settings_bp import _run_backup
        _run_backup(log)
    except ImportError:
        # Fall back to copying the SQLite file if that is the database.
        try:
            uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
            if uri.startswith('sqlite:///'):
                import shutil
                src = uri.replace('sqlite:///', '')
                if not os.path.isabs(src):
                    src = os.path.join(current_app.root_path, src)
                stamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
                name = f'backup-{stamp}.db'
                dst = os.path.join(_backup_dir(), name)
                shutil.copy2(src, dst)
                log.filename = name
                log.size_bytes = os.path.getsize(dst)
                log.status = 'success'
            else:
                log.status = 'failed'
                log.message = 'Backup requires mysqldump/pg_dump on the server.'
        except Exception as exc:
            log.status = 'failed'
            log.message = str(exc)[:500]
    except Exception as exc:
        log.status = 'failed'
        log.message = str(exc)[:500]

    db.session.commit()
    if log.status != 'success':
        return fail('backup_failed', 500, detail=log.message or '')
    return ok({'id': log.id, 'filename': log.filename,
               'size_human': log.size_human})


@bp.get('/settings/backups/<int:bid>/download')
@admin_required
def backup_download(bid):
    log = db.session.get(BackupLog, bid)
    if not log or not log.filename:
        return fail('not_found', 404)
    path = os.path.join(_backup_dir(), log.filename)
    if not os.path.exists(path):
        return fail('file_missing', 404)
    return send_file(path, as_attachment=True, download_name=log.filename)


# --------------------------------------------------------------------------- #
#  Import / Export
# --------------------------------------------------------------------------- #
EXPORT_FIELDS = {
    'customers': ('id', 'first_name', 'middle_name', 'last_name', 'mobile',
                  'email', 'username', 'zone', 'area', 'locality',
                  'connection_type', 'reference_id', 'is_active'),
    'plans': ('id', 'name', 'plan_code', 'plan_type', 'speed_mbps',
              'price_monthly', 'validity_days', 'is_active'),
    'invoices': ('id', 'invoice_no', 'customer_id', 'issue_date', 'due_date',
                 'total_amount', 'discount_amount', 'status', 'invoice_type'),
    'payments': ('id', 'invoice_id', 'customer_id', 'amount', 'payment_date',
                 'payment_mode', 'mode_detail', 'status', 'source'),
}

ENTITY_MODELS = {
    'customers': Customer,
    'plans': Plan,
    'invoices': Invoice,
    'payments': Payment,
    'products': Product,
    'expenses': Expense,
}


def _model_for(entity):
    return ENTITY_MODELS.get(entity)


@bp.get('/settings/export')
@staff_required
def export_csv():
    entity = (request.args.get('entity') or '').strip()
    model = _model_for(entity)
    if not model:
        return fail('unknown_entity', 400,
                    allowed=sorted(ENTITY_MODELS.keys()))

    fields = EXPORT_FIELDS.get(
        entity, tuple(c.key for c in model.__mapper__.columns))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(fields)
    for row in model.query.all():
        line = []
        for f in fields:
            value = getattr(row, f, '')
            if isinstance(value, Decimal):
                value = float(value)
            elif isinstance(value, (date, datetime)):
                value = value.isoformat()
            line.append('' if value is None else value)
        writer.writerow(line)

    payload = buffer.getvalue().encode('utf-8')
    stamp = date.today().isoformat()
    return send_file(io.BytesIO(payload), mimetype='text/csv',
                     as_attachment=True,
                     download_name=f'{entity}-{stamp}.csv')


@bp.post('/settings/import')
@admin_required
def import_csv():
    entity = (request.form.get('entity') or request.args.get('entity') or '').strip()
    model = _model_for(entity)
    if not model:
        return fail('unknown_entity', 400,
                    allowed=sorted(ENTITY_MODELS.keys()))

    file = request.files.get('file')
    if not file or not file.filename:
        return fail('no_file', 400)

    try:
        text = file.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return fail('bad_encoding', 400)

    job = ImportJob(target=entity, filename=file.filename, status='running',
                    created_by_id=current_staff_id())
    db.session.add(job)
    db.session.commit()

    reader = csv.DictReader(io.StringIO(text))
    columns = {c.key for c in model.__mapper__.columns}
    errors, ok_rows, total = [], 0, 0

    for index, raw in enumerate(reader, start=2):
        total += 1
        data = {k: v for k, v in raw.items() if k in columns and v != ''}
        try:
            row_id = data.pop('id', None)
            row = db.session.get(model, int(row_id)) if row_id else None
            if not row:
                row = model()
                db.session.add(row)
            for key, value in data.items():
                setattr(row, key, value)
            db.session.flush()
            ok_rows += 1
        except Exception as exc:
            db.session.rollback()
            errors.append(f'Row {index}: {str(exc)[:180]}')

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        job.status = 'failed'
        job.error_report = str(exc)[:2000]
        db.session.commit()
        return fail('import_failed', 500, detail=str(exc)[:200])

    job.total_rows = total
    job.ok_rows = ok_rows
    job.failed_rows = len(errors)
    job.error_report = '\n'.join(errors[:200])
    job.status = 'done'
    db.session.commit()

    return ok({'job_id': job.id, 'total': total, 'imported': ok_rows,
               'failed': len(errors), 'errors': errors[:50]})


# --------------------------------------------------------------------------- #
#  Messaging
# --------------------------------------------------------------------------- #
@bp.get('/messages/log')
@staff_required
def message_log():
    query = MessageLog.query
    q = (request.args.get('q') or '').strip()
    if q:
        query = query.filter(MessageLog.phone.ilike(f'%{q}%'))
    status = request.args.get('status')
    if status:
        query = query.filter(MessageLog.status == status)
    channel = request.args.get('channel')
    if channel:
        query = query.filter(MessageLog.channel == channel)
    customer_id = request.args.get('customer_id')
    if customer_id:
        query = query.filter(MessageLog.customer_id == customer_id)

    rows, meta = paginate(query.order_by(MessageLog.created_at.desc()))
    return ok([{
        'id': m.id,
        'customer_id': m.customer_id,
        'customer_name': m.customer.full_name if m.customer else '',
        'phone': m.phone or '',
        'channel': m.channel,
        'template_type': m.template_type or '',
        'body': m.body or '',
        'status': m.status,
        'error': m.error or '',
        'created_at': iso(m.created_at),
    } for m in rows], meta=meta)


def _audience_customers(audience, zone=None):
    """Resolve an audience key to a list of Customer rows."""
    today = date.today()
    query = Customer.query.filter_by(is_active=True)
    if zone:
        query = query.filter(Customer.zone == zone)

    if audience == 'all_active':
        return query.all()

    if audience == 'expiring_7':
        ids = [cp.customer_id for cp in CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date >= today,
            CustomerPlan.end_date <= today + timedelta(days=7)).all()]
        return query.filter(Customer.id.in_(ids or [0])).all()

    if audience == 'expired':
        ids = [cp.customer_id for cp in CustomerPlan.query.filter(
            CustomerPlan.status == 'active',
            CustomerPlan.end_date < today).all()]
        return query.filter(Customer.id.in_(ids or [0])).all()

    if audience == 'unpaid':
        ids = {i.customer_id for i in Invoice.query.filter(
            Invoice.status.in_(('draft', 'sent', 'overdue'))).all()
            if i.balance > 0}
        return query.filter(Customer.id.in_(list(ids) or [0])).all()

    return query.all()


@bp.post('/messages/bulk')
@staff_required
def messages_bulk():
    try:
        from services import messaging
    except Exception:
        return fail('messaging_unavailable', 503)

    data = body()
    message = (data.get('message') or '').strip()
    if not message:
        return fail('message_required', 400)

    audience = data.get('audience') or 'all_active'
    customers = _audience_customers(audience, data.get('zone'))

    if not customers:
        return ok({'audience': audience, 'recipients': 0, 'job': None,
                   'detail': 'Nobody matched that audience.'})

    from services import outbox

    # One run at a time. Two overlapping runs message everybody twice and the
    # screen gives no clue that the first is still going.
    running = outbox.active_job('bulk')
    if running:
        return fail('bulk_send_in_progress', 409,
                    detail=f"A bulk send is still running "
                           f"({running['done']} of {running['total']} done). "
                           f"Wait for it to finish.")

    def send_one(customer):
        context = messaging.build_context(customer=customer)
        rendered = messaging.render(message, context)
        result = messaging.send_whatsapp(customer.mobile, rendered,
                                         customer_id=customer.id,
                                         template_type='bulk')
        return getattr(result, 'ok', False)

    # Returns straight away. Sending N messages inline meant a request that
    # ran for minutes, a browser that gave up before it finished, and a
    # thread held for the whole time - which is what made every OTHER screen
    # slow while a bulk run was going.
    job = outbox.start(current_app._get_current_object(), 'bulk',
                       f'{len(customers)} {audience.replace("_", " ")}',
                       customers, send_one)

    return ok({'audience': audience, 'recipients': len(customers),
               'job': job,
               'detail': f'Sending to {len(customers)} customer(s) in the '
                         f'background. Progress is on this screen, and every '
                         f'attempt is recorded in the message log.'})


@bp.get('/messages/jobs/<job_id>')
@staff_required
def messages_job(job_id):
    """Progress of a background send."""
    from services import outbox
    job = outbox.get_job(job_id)
    if not job:
        return fail('not_found', 404,
                    detail='That send has finished and its progress has been '
                           'forgotten. The message log has the outcome.')
    return ok(job)


@bp.get('/messages/jobs')
@staff_required
def messages_job_current():
    """Whatever bulk send is running now, if any."""
    from services import outbox
    return ok(outbox.active_job('bulk'))


# --------------------------------------------------------------------------- #
#  ISP integrations
# --------------------------------------------------------------------------- #
def _normalize_driver(provider):
    return DRIVER_ALIASES.get((provider or '').lower(), provider or 'log2space')


def _cred_for_provider(provider):
    return ISPCredential.query.filter_by(
        driver=_normalize_driver(provider)).first()


@bp.get('/isp/credentials')
@staff_required
def isp_credentials():
    """Masked view - secret and api_key are never returned."""
    rows = ISPCredential.query.order_by(ISPCredential.id).all()
    return ok([{
        'id': c.id,
        'driver': c.driver,
        'label': c.label or '',
        'service_provider_id': c.service_provider_id,
        'service_provider': (c.service_provider.name
                             if c.service_provider else ''),
        'base_url': c.base_url or '',
        'username': c.username or '',
        'has_secret': bool(c._secret),
        'has_api_key': bool(c.api_key),
        'nas': (c.options or {}).get('nas', ''),
        'site': (c.options or {}).get('site', ''),
        'verify_ssl': bool(c.verify_ssl),
        'timeout_seconds': c.timeout_seconds or 20,
        'is_active': bool(c.is_active),
        'is_sandbox': bool(c.is_sandbox),
        'health': c.health,
        'last_ok_at': iso(c.last_ok_at),
        'last_error': (c.last_error or '')[:300],
    } for c in rows])


@bp.post('/isp/credentials')
@admin_required
def isp_credentials_save():
    data = body()
    driver = _normalize_driver(data.get('driver') or data.get('provider'))
    if driver not in KNOWN_DRIVERS:
        return fail('unknown_driver', 400, allowed=list(KNOWN_DRIVERS))

    cred_id = data.get('id')
    cred = db.session.get(ISPCredential, int(cred_id)) if cred_id else None

    if not cred:
        sp_id = data.get('service_provider_id')
        if not sp_id:
            return fail('service_provider_id_required', 400,
                        detail='Create a service provider first, then link '
                               'this integration to it.')
        if not db.session.get(ServiceProvider, int(sp_id)):
            return fail('service_provider_not_found', 404)
        cred = ISPCredential(driver=driver, service_provider_id=int(sp_id),
                             base_url='')
        db.session.add(cred)

    cred.driver = driver
    for field in ('base_url', 'username', 'label', 'api_key'):
        if field in data:
            setattr(cred, field, data[field] or '')
    for field in ('verify_ssl', 'is_active', 'is_sandbox'):
        if field in data:
            setattr(cred, field, bool(data[field]))
    if 'timeout_seconds' in data:
        cred.timeout_seconds = int(data['timeout_seconds'] or 20)

    if data.get('password'):
        if hasattr(cred, 'set_secret'):
            cred.set_secret(data['password'])
        else:
            return fail('credential_key_missing', 500)

    options = dict(cred.options or {})
    for field in ('nas', 'site'):
        if field in data:
            options[field] = data[field] or ''
    cred.options = options

    if not cred.base_url:
        return fail('base_url_required', 400)

    db.session.commit()
    return ok({'id': cred.id, 'driver': cred.driver, 'status': 'saved'})


@bp.post('/isp/test')
@admin_required
def isp_test():
    from services import isp_providers

    data = body()
    cred = (db.session.get(ISPCredential, int(data['id']))
            if data.get('id') else _cred_for_provider(data.get('driver')
                                                      or data.get('provider')))
    if not cred:
        return fail('no_credential', 404)

    try:
        result = isp_providers.test_credential(cred)
    except Exception as exc:
        return fail('test_failed', 424, detail=str(exc)[:200])

    success = bool(getattr(result, 'ok', False))
    message = getattr(result, 'message', '') or 'Connection tested.'
    return ok({'ok': success, 'message': message, 'health': cred.health})


@bp.get('/isp/sync-logs')
@staff_required
def isp_sync_logs():
    query = ISPSyncLog.query
    cred_id = request.args.get('credential_id')
    if cred_id:
        query = query.filter(ISPSyncLog.credential_id == cred_id)
    customer_id = request.args.get('customer_id')
    if customer_id:
        query = query.filter(ISPSyncLog.customer_id == customer_id)

    rows, meta = paginate(query.order_by(ISPSyncLog.created_at.desc()))
    return ok([{
        'id': r.id,
        'credential_id': r.credential_id,
        'driver': r.credential.driver if r.credential else '',
        'customer_id': r.customer_id,
        'action': r.action or '',
        'http_status': r.http_status,
        'success': bool(r.success),
        'duration_ms': r.duration_ms,
        'request_summary': (r.request_summary or '')[:500],
        'response_summary': (r.response_summary or '')[:500],
        'created_at': iso(r.created_at),
    } for r in rows], meta=meta)
