"""
blueprints/api/invoices.py
==========================

Invoice delivery: PDF download, WhatsApp send, and the dues reminder.

All three existed only as Jinja form-posts in app.py (`invoice_send`,
`customer_send_reminder`), so the React app had no way to get a bill to a
customer. As with customer_actions.py, these call the *same* helpers app.py
uses, imported inside the functions to avoid a circular import.
"""
from flask import Blueprint, Response, request

from models import Customer, Invoice, db

from .serializers import invoice_dict
from .utils import fail, ok, staff_required

bp = Blueprint('api_invoices', __name__)


def _logo_path():
    """Absolute path to the company logo, if one is configured."""
    try:
        import os
        from flask import current_app
        from models import Company
        company = Company.query.first()
        name = getattr(company, 'company_logo', None)
        if not name:
            return None
        path = os.path.join(current_app.root_path, 'static', 'uploads', name)
        return path if os.path.exists(path) else None
    except Exception:
        return None


@bp.get('/invoices/<int:iid>/pdf')
@staff_required
def invoice_pdf(iid):
    """The printed bill, as a PDF.

    ``?detail=1`` returns the detailed version: the same bill plus every
    payment recorded against it, with mode, bank reference, transaction and
    receipt numbers.
    """
    invoice = db.session.get(Invoice, iid)
    if not invoice:
        return fail('not_found', 404)

    try:
        from services.invoice_pdf import build_invoice_pdf
    except ImportError as exc:
        # Report what actually failed. Asserting "ReportLab is not installed"
        # is a guess: the same ImportError is raised if any import inside
        # invoice_pdf.py fails, and sending someone to reinstall a package
        # that is already there wastes the one clue they had.
        from flask import current_app
        current_app.logger.error('Invoice PDF unavailable: %s', exc)
        return fail('pdf_unavailable', 503,
                    detail=f'The PDF builder could not be loaded: {exc}. '
                           f'If this mentions reportlab, activate the venv and '
                           f'run "pip install -r requirements.txt".')

    try:
        detailed = (request.args.get('detail') or '').strip() in ('1', 'true', 'yes')
        pdf = build_invoice_pdf(invoice, logo_path=_logo_path(), detailed=detailed)
    except Exception as exc:
        return fail('pdf_failed', 500, detail='An error occurred while generating the PDF.')

    return Response(pdf, mimetype='application/pdf', headers={
        'Content-Disposition':
            f'inline; filename="{invoice.invoice_no or "invoice"}.pdf"',
    })


@bp.get('/public/invoices/<int:iid>/pdf')
def public_invoice_pdf(iid):
    """The bill, for the customer, without a login.

    Deliberately unauthenticated - and deliberately not guessable. The link
    carries an HMAC over the invoice id and an expiry; without a valid one this
    answers 403 and never touches the database, so walking the integers reveals
    nothing about which invoices exist.

    Kept separate from the staff endpoint rather than relaxing that one: the
    two have different rules and merging them is how a "just for customers"
    exception ends up applying to everything.
    """
    from services.signed_links import verify

    if not verify('invoice', iid, request.args.get('exp'), request.args.get('sig')):
        return fail('link_invalid_or_expired', 403,
                    detail='This bill link is no longer valid. Ask us to send '
                           'it again.')

    invoice = db.session.get(Invoice, iid)
    if not invoice:
        return fail('not_found', 404)

    try:
        from services.invoice_pdf import build_invoice_pdf
        pdf = build_invoice_pdf(invoice, logo_path=_logo_path())
    except Exception as exc:
        from flask import current_app
        current_app.logger.exception('Public invoice PDF failed')
        return fail('pdf_failed', 500, detail='An error occurred while generating the PDF.')

    return Response(pdf, mimetype='application/pdf', headers={
        'Content-Disposition':
            f'inline; filename="{invoice.invoice_no or "invoice"}.pdf"',
        # A signed link is per-customer; a shared cache must not keep it.
        'Cache-Control': 'private, max-age=300',
    })


@bp.post('/invoices/<int:iid>/send')
@staff_required
def invoice_send(iid):
    """
    Deliver the bill to the customer.

    ``channel`` picks the transport: ``whatsapp`` (the default, and what the
    operator reaches for) renders the message template; ``email`` attaches the
    same PDF the download button produces, so the customer receives the
    document rather than a paraphrase of it.
    """
    invoice = db.session.get(Invoice, iid)
    if not invoice:
        return fail('not_found', 404)

    customer = invoice.customer
    if not customer:
        return fail('customer_missing', 409)

    from flask import request
    payload = request.get_json(silent=True) or {}
    channel = (payload.get('channel') or 'whatsapp').strip().lower()

    if channel == 'email':
        return _invoice_send_email(invoice, customer)

    if not customer.mobile:
        return fail('no_mobile_number', 400,
                    detail='This customer has no mobile number on file.')

    template_type = payload.get('template_type') or 'bill'

    try:
        from app import _bill_context, send_template_message
        result = send_template_message(customer, template_type, **_bill_context(invoice))
    except Exception as exc:
        return fail('send_failed', 424, detail='An error occurred while sending the message.')

    status = getattr(result, 'status', 'unknown')
    detail = getattr(result, 'detail', '') or ''

    # A dry-run means the gateway is unconfigured: the message was logged, not
    # sent. Reporting that as success would be a lie the operator acts on.
    if status in ('sent', 'queued'):
        return ok({'status': status, 'to': customer.mobile,
                   'invoice_no': invoice.invoice_no,
                   'detail': detail if status == 'queued' else ''})
    if status == 'dry-run':
        return ok({'status': 'dry-run', 'to': customer.mobile,
                   'invoice_no': invoice.invoice_no,
                   'detail': 'WhatsApp gateway is not configured, so the '
                             'message was logged instead of sent.'})
    return fail('send_failed', 424, detail=detail or 'The gateway rejected the message.')


def _invoice_send_email(invoice, customer):
    """Email the bill with the PDF attached."""
    if not customer.email:
        return fail('no_email_address', 400,
                    detail='This customer has no email address on file.')

    try:
        from services.invoice_pdf import build_invoice_pdf
        pdf = build_invoice_pdf(invoice, logo_path=_logo_path())
    except ImportError as exc:
        # Report what actually failed. Asserting "ReportLab is not installed"
        # is a guess: the same ImportError is raised if any import inside
        # invoice_pdf.py fails, and sending someone to reinstall a package
        # that is already there wastes the one clue they had.
        from flask import current_app
        current_app.logger.error('Invoice PDF unavailable: %s', exc)
        return fail('pdf_unavailable', 503,
                    detail=f'The PDF builder could not be loaded: {exc}. '
                           f'If this mentions reportlab, activate the venv and '
                           f'run "pip install -r requirements.txt".')
    except Exception as exc:
        return fail('pdf_failed', 500, detail='An error occurred while generating the PDF.')

    company = ''
    try:
        from models import Company
        row = Company.query.first()
        company = (row.name if row else '') or ''
    except Exception:
        pass

    subject = f'Invoice {invoice.invoice_no}'
    message = (f'Dear {customer.full_name},\n\n'
               f'Please find attached invoice {invoice.invoice_no} dated '
               f'{invoice.issue_date:%d-%m-%Y} for '
               f'{float(invoice.net_amount):.2f}.\n\n'
               + (f'Thank you for your business.\n\n{company}'
                  if company else 'Thank you for your business.'))

    # services.mailer, not app.send_email: the latter is a stub that writes a
    # log line and returns, so every "invoice emailed" message the UI showed
    # would have been a lie.
    from services.mailer import send_email

    result = send_email(customer.email, subject, message,
                        attachments=[(f'{invoice.invoice_no}.pdf', pdf,
                                      'application/pdf')])

    _log_message(customer, 'email', 'bill', message, result)

    if result.status == 'sent':
        return ok({'status': 'sent', 'channel': 'email', 'to': customer.email,
                   'invoice_no': invoice.invoice_no})
    if result.status == 'dry-run':
        return ok({'status': 'dry-run', 'channel': 'email',
                   'to': customer.email, 'invoice_no': invoice.invoice_no,
                   'detail': result.detail})
    return fail('email_failed', 424,
                detail=result.detail or 'The mail server rejected the message.')


def _log_message(customer, channel, template_type, body, result):
    """Record the attempt so it shows on the customer's SMS/message log.

    Email went nowhere near that log before, so an operator checking "did we
    contact them?" saw only the WhatsApp attempts.
    """
    try:
        from models import MessageLog
        db.session.add(MessageLog(
            customer_id=customer.id,
            phone=customer.email or '',
            channel=channel,
            template_type=template_type,
            body=body,
            status=getattr(result, 'status', 'unknown'),
            error=('' if getattr(result, 'ok', False)
                   else getattr(result, 'detail', ''))[:500],
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


@bp.post('/customers/<int:cid>/send-reminder')
@staff_required
def customer_send_reminder(cid):
    """Outstanding-dues nudge for every unpaid invoice on the account."""
    customer = db.session.get(Customer, cid)
    if not customer:
        return fail('not_found', 404)
    if not customer.mobile:
        return fail('no_mobile_number', 400,
                    detail='This customer has no mobile number on file.')

    unpaid = [i for i in Invoice.query.filter(
        Invoice.customer_id == cid,
        Invoice.status.in_(('draft', 'sent', 'overdue'))).all() if i.balance > 0]
    due_total = float(round(sum(i.balance for i in unpaid)))

    if due_total <= 0:
        return fail('nothing_outstanding', 400,
                    detail='This customer has no outstanding balance.')

    latest = max(unpaid, key=lambda i: (i.issue_date, i.id))

    try:
        from models import CustomerPlan
        active_plan = CustomerPlan.query.filter_by(
            customer_id=cid, status='active').first()
        from app import send_template_message
        result = send_template_message(
            customer, 'due_reminder',
            {'due_amount': due_total, 'balance': due_total,
             'invoice_count': len(unpaid)},
            customer_plan=active_plan, invoice=latest)
    except Exception as exc:
        return fail('send_failed', 424, detail='An error occurred while sending the message.')

    status = getattr(result, 'status', 'unknown')
    from services.messaging import DELIVERABLE_STATUSES
    if status in DELIVERABLE_STATUSES:
        return ok({
            'status': status,
            'to': customer.mobile,
            'due_amount': due_total,
            'invoice_count': len(unpaid),
            'detail': ('WhatsApp gateway is not configured, so the reminder '
                       'was logged instead of sent.') if status == 'dry-run'
            else getattr(result, 'detail', '') if status == 'queued' else '',
        })
    return fail('send_failed', 424,
                detail=getattr(result, 'detail', '') or 'The gateway rejected the message.')


@bp.get('/invoices/<int:iid>/full')
@staff_required
def invoice_full(iid):
    """Invoice with the fields the print/preview screen needs."""
    invoice = db.session.get(Invoice, iid)
    if not invoice:
        return fail('not_found', 404)

    data = invoice_dict(invoice, detail=True)
    customer = invoice.customer
    if customer:
        data['customer'] = {
            'id': customer.id,
            'full_name': customer.full_name,
            'mobile': customer.mobile or '',
            'email': customer.email or '',
            'username': customer.username or '',
            'gstin': customer.gstin or '',
            'address': ', '.join(filter(None, [
                customer.flat_no, customer.building,
                customer.locality, customer.area])),
        }
    return ok(data)
