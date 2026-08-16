"""
blueprints/api/serializers.py
=============================

JSON serializers for UniCRM models.

Every response the React app or the mobile app sees is built here, so field
names stay consistent across the whole product.

NOTE on the logo bug: the invoice serializer always embeds a fully-resolved
``company`` block (name, address, GSTIN, absolute logo URL). Previously the
invoice templates read the logo from a different place than the company
settings screen wrote it to, which is why an uploaded logo never appeared on
the bill. ``company_branding()`` below is now the single source of truth - the
web invoice view, the PDF, and the mobile app all call it.
"""
from flask import url_for

from models import Company, Customer, CustomerPlan, Invoice, Payment, Plan, User
from services.plans import current_plan_of

from . import permissions as _permissions
from .utils import iso, money


def static_url(name):
    """Absolute URL for a file under ``/static``.

    Built from PUBLIC_BASE_URL when it is set, and only from the incoming
    request as a fallback. That order matters in this deployment, because the
    two things that serve this application are not the same host:

      * The React app is a static site on its own domain. Any RELATIVE url
        here - which is what the old `except` branch returned whenever there
        was no request context, e.g. from the scheduler or a PDF build -
        resolves against THAT host, which has no /static directory. The image
        silently 404s and the page shows a broken logo.

      * `_external=True` builds from the host the request arrived on. Behind
        Render's proxy that is frequently an internal address, so the URL is
        one only the datacentre can resolve. This is the same trap that made
        WhatsApp bills go out with no attachment, and services/signed_links.py
        already solved it - so use the same helper rather than a second answer
        to the same question.
    """
    try:
        from services.signed_links import public_base_url
        base = public_base_url()
    except Exception:
        base = ''
    if base:
        return f"{base}/static/{name}"
    try:
        return url_for('static', filename=name, _external=True)
    except Exception:
        # Last resort. Correct only when the API and the page share an origin.
        return '/static/' + name


#: The logo that ships with the application, relative to /static.
BUNDLED_LOGO = 'images/logo.jpg'


def _static_file_exists(relative):
    """Is this actually on disk under static/, right now?"""
    import os
    try:
        from flask import current_app
        root = current_app.root_path
    except Exception:                                       # noqa: BLE001
        root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
    return os.path.exists(os.path.join(root, 'static', *relative.split('/')))


def logo_url(filename):
    """
    Resolve whatever is stored in ``Company.company_logo`` into a URL the
    browser / app can actually load.

    "Can actually load" is the part that was missing. This built a URL from
    the stored filename without ever asking whether that file is on this
    server, and uploads live on the web container's own disk - which the host
    rebuilds on every deploy. So the Company row goes on naming a file that no
    longer exists, the URL 404s, and every screen that prints the logo shows a
    broken-image icon with the company name beside it: the admin sidebar, the
    customer portal header, the mobile app.

    A missing upload therefore falls back to the logo the repository ships,
    which is the same mark. Uploading a new one still works and still wins -
    until the next deploy takes it away again, which is a hosting problem
    (see the note in services/invoice_pdf.py) rather than one this can solve.
    """
    if not filename:
        return static_url(BUNDLED_LOGO) if _static_file_exists(BUNDLED_LOGO) else None

    name = str(filename).replace('\\', '/').strip()
    if name.startswith('http://') or name.startswith('https://'):
        return name

    # Strip any historic prefix so only the bare filename remains.
    for prefix in ('static/', 'uploads/', 'uploads/logos/', '/static/'):
        if name.startswith(prefix):
            name = name[len(prefix):]
    name = name.lstrip('/')

    if not name.startswith('uploads/'):
        name = 'uploads/logos/' + name.split('/')[-1]

    # The places an upload has landed across the life of this application.
    bare = name.split('/')[-1]
    for candidate in (name, f'uploads/{bare}', f'images/{bare}', bare):
        if _static_file_exists(candidate):
            return static_url(candidate)

    if _static_file_exists(BUNDLED_LOGO):
        return static_url(BUNDLED_LOGO)
    # Nothing to point at. None, not a broken URL - the clients all fall back
    # to their own bundled asset when this is empty.
    return None


def company_name():
    """The trading name, cached for the life of the request.

    Serializers call this per ROW - a page of 25 plans would otherwise be 25
    `SELECT * FROM company LIMIT 1`, which is how a display fallback turns
    into a performance bug.
    """
    try:
        from flask import g
        cached = getattr(g, '_company_name', None)
        if cached is not None:
            return cached
    except Exception:                                       # noqa: BLE001
        g = None

    name = ''
    try:
        row = Company.query.first()
        name = (row.name or '') if row else ''
    except Exception:                                       # noqa: BLE001
        name = ''

    try:
        if g is not None:
            g._company_name = name
    except Exception:                                       # noqa: BLE001
        pass
    return name


def provider_name(record):
    """Who provides this service, as a customer would answer it.

    `service_provider_id` names the UPSTREAM provider, and nothing has ever
    filled it in: no seed creates a ServiceProvider row, so the dropdowns on
    the plan and customer forms open empty, so every plan and every customer
    carries NULL - and every screen that prints a provider has been showing a
    dash. The Plan tab, Plan History, the plan master, the plan picker and
    the customer's own portal, all blank, on a field the business plainly has
    an answer to.

    So the answer, when nothing more specific is recorded, is this company:
    the customer's provider is whoever sells them the connection. An upstream
    provider that IS recorded still wins.
    """
    provider = getattr(record, 'service_provider', None)
    if provider is not None and getattr(provider, 'name', ''):
        return provider.name
    return company_name()


def company_branding(company=None):
    """The single source of truth for company identity on every document."""
    c = company or Company.query.first()
    if not c:
        return {
            'id': None, 'name': '', 'logo_url': None, 'address': '',
            'mobile': '', 'phone': '', 'email': '', 'gstin': '',
            'pan_no': '', 'sac_no': '', 'state_code': '',
            'place_of_supply': '', 'website_url': '',
        }
    return {
        'id': c.id,
        'name': c.name or '',
        'logo_url': logo_url(c.company_logo),
        'address': c.address or '',
        'mobile': c.mobile or '',
        'phone': c.phone or '',
        'email': c.email or '',
        'gstin': c.gstin or '',
        'pan_no': c.pan_no or '',
        'sac_no': c.sac_no or '',
        'state_code': c.state_code or '',
        'place_of_supply': c.place_of_supply or '',
        'website_url': c.website_url or '',
        'bank_account_details': c.bank_account_details or '',
        'invoice_notes': c.invoice_notes or '',
    }


def company_dict(c):
    if not c:
        return None
    data = company_branding(c)
    data.update({
        'company_type': c.company_type or '',
        'b2b_invoice_series': c.b2b_invoice_series or '',
        'b2c_invoice_series': c.b2c_invoice_series or '',
        'created_at': iso(c.created_at),
        'updated_at': iso(c.updated_at),
    })
    return data


def user_dict(u):
    if not u:
        return None
    return {
        'id': u.id,
        'username': u.username,
        'full_name': u.full_name or '',
        'email': u.email or '',
        'mobile': u.mobile or '',
        'role': u.role,
        'is_active': bool(u.is_active),
        'staff_type_id': u.staff_type_id,
        'staff_type': u.staff_type.name if u.staff_type else '',
        'monthly_salary': money(u.monthly_salary),
        'created_at': iso(u.created_at),
        # What this user is allowed to do. `permissions` is what the admin
        # actually ticked; `granted` is that plus everything it implies, and is
        # what the browser should test against - otherwise a user given
        # "record payments" has the invoice list hidden from them by a UI that
        # is stricter than the API it is talking to.
        'permissions': _permissions.parse(getattr(u, 'permissions', None)),
        'restricted': _permissions.is_restricted(u),
        'granted': sorted(_permissions.effective(u)),
    }


def customer_dict(c, detail=False):
    if not c:
        return None

    # Whichever row the Plan tab is showing - the picker is shared, so the
    # header's Bill Upto date cannot come from a different plan than the one
    # printed underneath it.
    active = current_plan_of(c)

    data = {
        'id': c.id,
        #: What the operator reads out on the phone. The live CRM prints the
        #: internal id with a C prefix, so keep the two in step.
        'account_id': f'C{c.id}',
        'title': c.title,
        'full_name': c.full_name,
        'active_plan_name': active.plan.name if active and active.plan else '',
        'active_plan_end': iso(active.end_date) if active else None,
        'first_name': c.first_name or '',
        'middle_name': c.middle_name or '',
        'last_name': c.last_name or '',
        'company_name': c.company_name or '',
        'customer_type': c.customer_type,
        'email': c.email or '',
        'mobile': c.mobile or '',
        'home_phone': c.home_phone or '',
        'username': c.username or '',
        'reference_id': c.reference_id or '',
        'zone': c.zone or '',
        'connection_type': c.connection_type,
        'billing_type': getattr(c, 'billing_type', None) or 'Prepaid',
        'wallet_balance': money(getattr(c, 'wallet_balance', 0)),
        'is_active': bool(c.is_active),
        'registration_date': iso(c.registration_date),
    }

    if detail:
        data.update({
            'gstin': c.gstin or '',
            'pan': c.pan or '',
            'aadhar': c.aadhar or '',
            'tax_type': c.tax_type,
            'flat_no': c.flat_no or '',
            'locality': c.locality or '',
            'area': c.area or '',
            'building': c.building or '',
            'billing_address': c.billing_address or '',
            'primary_address': c.primary_address or '',
            'notes': c.notes or '',
            'discount_percent': money(c.discount_percent),
            'discount_amount': money(c.discount_amount),
            'ip_address': getattr(c, 'ip_address', '') or '',
            'ipacct_id': getattr(c, 'ipacct_id', '') or '',
            'service_provider_id': getattr(c, 'service_provider_id', None),
            'service_provider': provider_name(c),
            'invoice_date': iso(getattr(c, 'invoice_date', None)),
            'latitude': getattr(c, 'latitude', '') or '',
            'longitude': getattr(c, 'longitude', '') or '',
            'created_at': iso(c.created_at),
            'updated_at': iso(c.updated_at),
            'active_plan': customer_plan_dict(active),
            'documents': customer_documents(c),
        })
    return data


#: Filename column -> the label the KYC panel prints next to it.
#: (slot, column, label, type column). The *slot* is the name the upload and
#: delete routes speak - `reg_form`, not `reg_form_file`. It is carried here
#: so one identifier travels the whole way: the UI matched slots by their
#: human label, which meant renaming "Reg. Form" to "Registration Form" would
#: have quietly detached every uploaded file from its slot.
KYC_DOCUMENTS = (
    ('reg_form', 'reg_form_file', 'Reg. Form', None),
    ('address_proof', 'address_proof_file', 'Address Proof', 'address_proof_type'),
    ('id_proof', 'id_proof_file', 'ID Proof', 'id_proof_type'),
    ('photo', 'photo_file', 'Photo', None),
)


def customer_documents(c):
    """
    The KYC block, as a list rather than eight loose keys.

    Every entry is returned even when nothing has been uploaded, because the
    detail screen shows the empty slots too - an operator needs to see that a
    proof is *missing*, not just fail to see that it is present.
    """
    out = []
    for slot, column, label, type_column in KYC_DOCUMENTS:
        filename = getattr(c, column, None)
        out.append({
            'slot': slot,
            'key': column,
            'label': label,
            'filename': filename or '',
            'doc_type': (getattr(c, type_column, None) or '') if type_column else '',
            'type_field': type_column or '',
            'url': _upload_url(filename, 'kyc') if filename else None,
        })
    return out


def _upload_url(filename, folder):
    """Resolve a stored upload filename into a servable static URL."""
    if not filename:
        return None
    name = str(filename).replace('\\', '/').strip().lstrip('/')
    if name.startswith('http://') or name.startswith('https://'):
        return name
    for prefix in ('static/', '/static/'):
        if name.startswith(prefix):
            name = name[len(prefix):]
    if not name.startswith('uploads/'):
        name = f'uploads/{folder}/' + name.split('/')[-1]
    return static_url(name)


def plan_dict(p):
    if not p:
        return None
    return {
        'id': p.id,
        'name': p.name,
        'plan_code': p.plan_code or '',
        'plan_type': p.plan_type or '',
        'speed_mbps': p.speed_mbps,
        'price_monthly': money(p.price_monthly),
        'isp_amount': money(p.isp_amount),
        'validity_days': p.validity_days or 30,
        'service_provider_id': p.service_provider_id,
        'service_provider': provider_name(p),
        'is_active': bool(p.is_active),
        'created_at': iso(p.created_at),
    }


def customer_plan_dict(cp):
    if not cp:
        return None
    from datetime import date as _date
    today = _date.today()
    days_left = (cp.end_date - today).days if cp.end_date else None
    effective_price = cp.effective_price
    master_price = cp.plan.price_monthly if cp.plan else None
    return {
        'id': cp.id,
        'customer_id': cp.customer_id,
        'plan_id': cp.plan_id,
        'plan': plan_dict(cp.plan),
        'plan_name': cp.plan.name if cp.plan else '',
        'speed_mbps': cp.plan.speed_mbps if cp.plan else None,
        # ``price_monthly`` remains the compatibility field used by the
        # customer screens, but now means the amount this customer will
        # actually be charged. Keep the master value separately for places
        # that explicitly need to compare it.
        'price_monthly': money(effective_price),
        'price': money(effective_price),
        'master_price_monthly': money(master_price),
        'has_price_override': cp.price is not None,
        'start_date': iso(cp.start_date),
        'end_date': iso(cp.end_date),
        'days_left': days_left,
        'is_expired': bool(days_left is not None and days_left < 0),
        'status': cp.status,
        'auto_renew': bool(cp.auto_renew),
        #: The Plan tab's "Online Renewal" column, and the switch the portal
        #: checks before it lets a customer renew themselves.
        'online_renewal': cp.renewable_online,
        'grace_period_days': cp.grace_period_days or 0,
        'last_invoice_date': iso(cp.last_invoice_date),
        'suspension_review_status': cp.suspension_review_status,
    }


def invoice_dict(inv, detail=False):
    if not inv:
        return None

    data = {
        'id': inv.id,
        'invoice_no': inv.invoice_no,
        'customer_id': inv.customer_id,
        'customer_name': inv.customer.full_name if inv.customer else '',
        'customer_mobile': inv.customer.mobile if inv.customer else '',
        'issue_date': iso(inv.issue_date),
        'due_date': iso(inv.due_date),
        'total_amount': money(inv.total_amount),
        'tax_amount': money(inv.tax_amount),
        'discount_percent': money(inv.discount_percent),
        'discount_amount': money(inv.discount_amount),
        'net_amount': money(inv.net_amount),
        'paid_amount': money(inv.paid_amount),
        'balance': money(inv.balance),
        'status': inv.status,
        'invoice_type': inv.invoice_type,
        'caption': inv.display_caption,
        'receipt_number': inv.receipt_number or '',
        'created_at': iso(inv.created_at),
    }

    if detail:
        data.update({
            'remarks': inv.remarks or '',
            'vendor': inv.vendor or '',
            'customer_plan_id': inv.customer_plan_id,
            'plan_name': (inv.customer_plan.plan.name
                          if inv.customer_plan and inv.customer_plan.plan
                          else ''),
            'company': company_branding(),
            'payments': [payment_dict(p) for p in
                         sorted(inv.payments, key=lambda x: (x.payment_date, x.id))],
            'items': _invoice_items(inv),
        })
    return data


def _invoice_items(inv):
    """Line items, if this deployment has the table.

    invoice_items arrived with models_ext and is created by db.create_all().
    A database that predates it (or one where the migration has not been run)
    raises OperationalError here, which used to surface as a 500 on any
    detailed invoice response - including the one the Addon Invoice screen
    reads back after saving. An invoice with no itemisation is a perfectly
    valid thing to show, so degrade to an empty list instead.
    """
    try:
        from models_ext import InvoiceItem
    except Exception:
        return []

    try:
        rows = InvoiceItem.query.filter_by(invoice_id=inv.id).all()
    except Exception:
        from models import db as _db
        _db.session.rollback()
        return []

    return [{
        'id': r.id,
        'description': r.description,
        'item_type': r.item_type,
        'quantity': r.quantity,
        'unit_price': money(r.unit_price),
        'discount_amount': money(r.discount_amount),
        'tax_percent': money(r.tax_percent),
        'serial_number': r.serial_number or '',
        'period_from': iso(r.period_from),
        'period_to': iso(r.period_to),
        'line_total': money((r.quantity or 1) * float(r.unit_price or 0)
                            - float(r.discount_amount or 0)),
    } for r in rows]


def payment_dict(p):
    if not p:
        return None
    return {
        'id': p.id,
        'invoice_id': p.invoice_id,
        'invoice_no': p.invoice.invoice_no if p.invoice else '',
        'customer_id': p.customer_id,
        # The Payments screen renders `customer_name || customer_id`, and this
        # key was never sent - so every row showed a bare number.
        'customer_name': p.customer.full_name if p.customer else '',
        'amount': money(p.amount),
        'discount_amount': money(p.discount_amount),
        'payment_date': iso(p.payment_date),
        'payment_mode': p.payment_mode,
        'mode_group': p.mode_group,
        'mode_detail': p.mode_detail or '',
        'status': p.status,
        'source': p.source or 'admin',
        'source_label': p.source_label,
        # The number on the receipt itself - the manual book number when
        # there is one, `R<id>` when there is not. The portal's Payments
        # screen has a column for it.
        'receipt_no': p.receipt_no,
        'book_receipt_no': p.book_receipt_no or '',
        'gateway_transaction_id': p.gateway_transaction_id or '',
        'remarks': p.remarks or '',
        'is_authorized': p.is_authorized,
        'needs_authorization': p.needs_authorization,
        'authorized_at': iso(p.authorized_at),
        'authorized_by': (p.authorized_by_user.full_name
                          if p.authorized_by_user else ''),
        'received_by': (p.received_by_user.full_name
                        if p.received_by_user else ''),
        # Who to print in a "Received by" column.
        #
        # A payment the customer made themselves has no staff member behind
        # it, so that column was a dash - and a dash reads as "nobody recorded
        # this", which is the opposite of what happened. It says Self Renew
        # instead, so an operator scanning the payment history can see at a
        # glance how much of it came through the portal without opening a
        # single row.
        'received_by_label': (
            p.received_by_user.full_name if p.received_by_user
            else ('Self Renew' if (p.source or '') in ('portal', 'gateway')
                  else '')),
        'created_at': iso(p.created_at),
    }
