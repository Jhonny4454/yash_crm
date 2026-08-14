"""
blueprints/api/company.py
=========================

Company (multi-company), branding/logo upload, and notification endpoints.

Logo fix
--------
Uploads always land in ``static/uploads/logos/`` and only the *filename* is
stored in Company.company_logo. ``serializers.logo_url()`` resolves it to an
absolute URL, and ``company_branding()`` is embedded in every invoice payload -
so the logo appears on the bill, in the app, and in the PDF from one upload.
"""
import os
from datetime import datetime

from flask import Blueprint, current_app, request
from werkzeug.utils import secure_filename

from models import Company, Customer, db
from models_api import (DeviceToken, Notification, NotificationTemplate,
                        seed_notification_templates)

from .serializers import company_branding, company_dict, logo_url
from .utils import (admin_required, body, current_customer_id,
                    customer_required, fail, iso, ok, paginate, staff_required)

bp = Blueprint('api_company', __name__)

UPLOAD_SUBDIR = os.path.join('uploads', 'logos')
ALLOWED_LOGO_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}

COMPANY_WRITABLE = (
    'name', 'mobile', 'phone', 'email', 'address', 'bank_account_details',
    'gstin', 'pan_no', 'sac_no', 'place_of_supply', 'state_code',
    'b2b_invoice_series', 'b2c_invoice_series', 'website_url',
    'company_type', 'invoice_notes',
)


# --------------------------------------------------------------------------- #
#  Companies
# --------------------------------------------------------------------------- #
@bp.get('/companies')
@staff_required
def company_list():
    rows = Company.query.order_by(Company.id).all()
    return ok([company_dict(c) for c in rows])


@bp.get('/companies/<int:cid>')
@staff_required
def company_get(cid):
    company = db.session.get(Company, cid)
    if not company:
        return fail('not_found', 404)
    return ok(company_dict(company))


@bp.get('/branding')
def branding():
    """
    Public - the login screen and the mobile splash need the logo before any
    token exists.
    """
    return ok(company_branding())


@bp.post('/companies')
@admin_required
def company_create():
    data = body()
    if not (data.get('name') or '').strip():
        return fail('name_required', 400)

    company = Company()
    for field in COMPANY_WRITABLE:
        if field in data:
            setattr(company, field, data[field])
    db.session.add(company)
    db.session.commit()
    return ok(company_dict(company)), 201


@bp.put('/companies/<int:cid>')
@admin_required
def company_update(cid):
    company = db.session.get(Company, cid)
    if not company:
        return fail('not_found', 404)
    data = body()
    for field in COMPANY_WRITABLE:
        if field in data:
            setattr(company, field, data[field])
    db.session.commit()
    return ok(company_dict(company))


@bp.delete('/companies/<int:cid>')
@admin_required
def company_delete(cid):
    if Company.query.count() <= 1:
        return fail('cannot_delete_last_company', 400)
    company = db.session.get(Company, cid)
    if not company:
        return fail('not_found', 404)
    db.session.delete(company)
    db.session.commit()
    return ok({'status': 'deleted'})


@bp.post('/companies/<int:cid>/logo')
@admin_required
def company_logo_upload(cid):
    company = db.session.get(Company, cid)
    if not company:
        return fail('not_found', 404)

    file = request.files.get('logo')
    if not file or not file.filename:
        return fail('no_file', 400)

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_LOGO_EXT:
        return fail('unsupported_file_type', 400)

    stamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    filename = secure_filename(f'company-{cid}-{stamp}.{ext}')
    folder = os.path.join(current_app.root_path, 'static', UPLOAD_SUBDIR)
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, filename))

    # Only the bare filename is stored - logo_url() resolves the rest.
    company.company_logo = filename
    db.session.commit()

    return ok({'company_logo': filename, 'logo_url': logo_url(filename)})


# --------------------------------------------------------------------------- #
#  Notification templates
# --------------------------------------------------------------------------- #
def _template_dict(t):
    return {
        'id': t.id,
        'code': t.code,
        'name': t.name,
        'title': t.title or '',
        'body': t.body or '',
        'description': t.description or '',
        'channel': t.channel,
        'send_push': bool(t.send_push),
        'send_whatsapp': bool(t.send_whatsapp),
        'is_active': bool(t.is_active),
    }


@bp.get('/notification-templates')
@staff_required
def template_list():
    seed_notification_templates()
    rows = NotificationTemplate.query.order_by(NotificationTemplate.code).all()
    return ok([_template_dict(t) for t in rows])


@bp.put('/notification-templates/<int:tid>')
@admin_required
def template_update(tid):
    template = db.session.get(NotificationTemplate, tid)
    if not template:
        return fail('not_found', 404)
    data = body()
    for field in ('name', 'title', 'body', 'description', 'channel',
                  'send_push', 'send_whatsapp', 'is_active'):
        if field in data:
            setattr(template, field, data[field])
    db.session.commit()
    return ok(_template_dict(template))


@bp.post('/notification-templates')
@admin_required
def template_create():
    data = body()
    code = (data.get('code') or '').strip()
    name = (data.get('name') or '').strip()
    if not code or not name:
        return fail('code_and_name_required', 400)
    if NotificationTemplate.query.filter_by(code=code).first():
        return fail('code_exists', 409)

    template = NotificationTemplate(
        code=code, name=name,
        title=data.get('title') or '',
        body=data.get('body') or '',
        description=data.get('description') or '',
        channel=data.get('channel') or 'push',
        send_push=bool(data.get('send_push', True)),
        send_whatsapp=bool(data.get('send_whatsapp', False)),
    )
    db.session.add(template)
    db.session.commit()
    return ok(_template_dict(template)), 201


# --------------------------------------------------------------------------- #
#  Notifications
# --------------------------------------------------------------------------- #
def _notification_dict(n):
    return {
        'id': n.id,
        'customer_id': n.customer_id,
        'template_code': n.template_code or '',
        'title': n.title or '',
        'body': n.body or '',
        'channel': n.channel,
        'status': n.status,
        'is_read': bool(n.is_read),
        'created_at': iso(n.created_at),
        'read_at': iso(n.read_at),
    }


def queue_notification(customer_id, code=None, title=None, body_text=None,
                       context=None, channel='push'):
    """Create a queued Notification row. Returns the row."""
    if code:
        template = NotificationTemplate.query.filter_by(code=code).first()
        if template:
            t_title, t_body = template.render(context or {})
            title = title or t_title
            body_text = body_text or t_body
            channel = template.channel or channel

    row = Notification(
        customer_id=customer_id,
        template_code=code,
        title=(title or '')[:150],
        body=body_text or '',
        channel=channel,
        status='queued',
    )
    db.session.add(row)
    return row


@bp.post('/notifications/send')
@staff_required
def notification_send():
    data = body()

    if data.get('all'):
        recipients = [c.id for c in
                      Customer.query.filter_by(is_active=True).all()]
    elif data.get('customer_ids'):
        recipients = [int(x) for x in data['customer_ids']]
    elif data.get('customer_id'):
        recipients = [int(data['customer_id'])]
    else:
        return fail('no_recipients', 400)

    code = data.get('template_code')
    title = data.get('title')
    body_text = data.get('body')
    if not code and not (title and body_text):
        return fail('template_code_or_title_body_required', 400)

    queued = 0
    for cid in recipients:
        customer = db.session.get(Customer, cid)
        if not customer:
            continue
        context = dict(data.get('context') or {})
        context.setdefault('customer_name', customer.full_name)
        queue_notification(cid, code=code, title=title, body_text=body_text,
                           context=context,
                           channel=data.get('channel') or 'push')
        queued += 1

    db.session.commit()
    return ok({'status': 'queued', 'count': queued})


@bp.get('/notifications')
@staff_required
def notification_admin_list():
    query = Notification.query
    customer_id = request.args.get('customer_id')
    if customer_id:
        query = query.filter(Notification.customer_id == customer_id)
    rows, meta = paginate(query.order_by(Notification.created_at.desc()))
    return ok([_notification_dict(n) for n in rows], meta=meta)


@bp.get('/portal/notifications')
@customer_required
def portal_notifications():
    query = Notification.query.filter_by(customer_id=current_customer_id())
    rows, meta = paginate(query.order_by(Notification.created_at.desc()))
    unread = Notification.query.filter_by(customer_id=current_customer_id(),
                                          is_read=False).count()
    return ok([_notification_dict(n) for n in rows], meta=meta, unread=unread)


@bp.post('/portal/notifications/<int:nid>/read')
@customer_required
def portal_notification_read(nid):
    row = db.session.get(Notification, nid)
    if not row or row.customer_id != current_customer_id():
        return fail('not_found', 404)
    row.mark_read()
    db.session.commit()
    return ok(_notification_dict(row))


@bp.post('/portal/notifications/read-all')
@customer_required
def portal_notifications_read_all():
    rows = Notification.query.filter_by(customer_id=current_customer_id(),
                                        is_read=False).all()
    for row in rows:
        row.mark_read()
    db.session.commit()
    return ok({'status': 'marked_read', 'count': len(rows)})


# --------------------------------------------------------------------------- #
#  Device tokens
# --------------------------------------------------------------------------- #
@bp.post('/portal/device-token')
@customer_required
def portal_register_device():
    """The app posts its push token after login and on every token refresh."""
    data = body()
    token = (data.get('token') or '').strip()
    if not token:
        return fail('token_required', 400)

    row = DeviceToken.query.filter_by(customer_id=current_customer_id(),
                                      token=token).first()
    if not row:
        row = DeviceToken(customer_id=current_customer_id(), token=token)
        db.session.add(row)

    row.platform = data.get('platform') or 'android'
    row.provider = data.get('provider') or 'expo'
    row.app_version = data.get('app_version')
    row.is_active = True
    db.session.commit()
    return ok({'status': 'registered'})


@bp.delete('/portal/device-token')
@customer_required
def portal_unregister_device():
    token = (body().get('token') or request.args.get('token') or '').strip()
    query = DeviceToken.query.filter_by(customer_id=current_customer_id())
    if token:
        query = query.filter_by(token=token)
    for row in query.all():
        row.is_active = False
    db.session.commit()
    return ok({'status': 'unregistered'})
