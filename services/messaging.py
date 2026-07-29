"""
services/messaging.py
=====================
Outbound WhatsApp / SMS gateway for YASH Internet Services CRM.

Design goals
------------
* **Nothing is hard-coded.** Every endpoint, credential and payload shape is
  read from the `settings` table (editable from Settings -> Messaging in the
  admin UI) with an environment-variable fallback.
* **Fails soft.** If the gateway is not configured, or the HTTP call fails, we
  log the attempt and return a failure result. We never raise into a request
  handler.
* **Everything is logged.** Every send attempt is written to `message_logs`.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from decimal import Decimal

try:
    import requests
except Exception:
    requests = None


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
SETTING_KEYS = {
    'enabled':       'wa_enabled',
    'provider':      'wa_provider',
    'api_url':       'wa_api_url',
    'api_token':     'wa_api_token',
    'instance_id':   'wa_instance_id',
    'sender':        'wa_sender',
    'method':        'wa_http_method',
    'payload':       'wa_payload_template',
    'country_code':  'wa_country_code',
    'doc_url':       'wa_document_url',
}

DEFAULTS = {
    'wa_enabled':          '0',
    'wa_provider':         'generic',
    'wa_api_url':          '',
    'wa_api_token':        '',
    'wa_instance_id':      '',
    'wa_sender':           '',
    'wa_http_method':      'POST',
    'wa_payload_template': '{"number": "{phone}", "type": "text", "message": "{message}", "instance_id": "{instance_id}", "access_token": "{token}"}',
    'wa_country_code':     '91',
    'wa_document_url':     '',
}


def _setting(key, default=''):
    """Read a setting from the DB, falling back to env, then to `default`."""
    try:
        from models_ext import Setting
        row = Setting.query.filter_by(key=key).first()
        if row is not None and (row.value or '').strip() != '':
            return row.value
    except Exception:
        pass
    env = os.environ.get(key.upper())
    if env:
        return env
    return DEFAULTS.get(key, default)


def is_configured() -> bool:
    """True when the gateway has enough config to attempt a real send."""
    return _setting('wa_enabled') in ('1', 'true', 'True', 'yes', 'on') \
        and bool((_setting('wa_api_url') or '').strip())


# --------------------------------------------------------------------------- #
#  Phone normalisation
# --------------------------------------------------------------------------- #
def normalize_phone(raw: str, country_code: str | None = None) -> str | None:
    """
    Turn whatever the operator typed into a bare international MSISDN.
    """
    if not raw:
        return None
    cc = (country_code or _setting('wa_country_code') or '91').lstrip('+')
    digits = re.sub(r'\D', '', str(raw))
    if not digits:
        return None
    if digits.startswith('0'):
        digits = digits.lstrip('0')
    if len(digits) < 8:
        return None
    if not digits.startswith(cc):
        digits = cc + digits
    return digits


# --------------------------------------------------------------------------- #
#  Template rendering
# --------------------------------------------------------------------------- #
_PLACEHOLDER_RE = re.compile(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}')


def _fmt_money(v):
    if v is None:
        return '0'
    if isinstance(v, Decimal):
        v = float(v)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}"


def _fmt_date(v):
    if not v:
        return ''
    if isinstance(v, (date, datetime)):
        return v.strftime('%d-%b-%Y')
    return str(v)


def build_context(customer=None, plan=None, customer_plan=None,
                  invoice=None, payment=None, extra=None) -> dict:
    """Assemble the placeholder dictionary for a template render."""
    ctx = {
        'today':         _fmt_date(date.today()),
        'app_link':      _setting('app_link', 'https://bit.ly/4bBo8kd'),
        'web_link':      _setting('web_link', 'https://yashinternetservices.in'),
        'company_name':  'YASH INTERNET SERVICES',
        'company_phone': '9029508777',
    }

    try:
        from models import Company
        company = Company.query.first()
        if company:
            ctx['company_name'] = company.name or ctx['company_name']
            ctx['company_phone'] = company.mobile or company.phone or ctx['company_phone']
            if company.website_url:
                ctx['web_link'] = company.website_url
    except Exception:
        pass

    if customer is not None:
        ctx.update({
            'customer_name': customer.full_name or '',
            'first_name':    customer.first_name or '',
            'username':      customer.username or '',
            'mobile':        customer.mobile or '',
        })

    cp = customer_plan
    if cp is not None:
        ctx['expiry_date'] = _fmt_date(cp.end_date)
        ctx['renew_date'] = _fmt_date(cp.start_date)
        if cp.end_date:
            ctx['days'] = str(max((cp.end_date - date.today()).days, 0))
        plan = plan or cp.plan

    if plan is not None:
        ctx['plan_name'] = plan.name or ''
        ctx['speed'] = str(plan.speed_mbps or '')
        ctx.setdefault('amount', _fmt_money(plan.price_monthly))

    if invoice is not None:
        ctx['invoice_no'] = invoice.invoice_no or ''
        ctx['amount'] = _fmt_money(invoice.total_amount)
        ctx['due_amount'] = _fmt_money(invoice.balance)
        ctx['balance'] = _fmt_money(invoice.balance)

    if payment is not None:
        ctx['paid_amount'] = _fmt_money(payment.amount)
        ctx['receipt_no'] = payment.book_receipt_no or f"R{payment.id}"
        ctx['transaction_id'] = payment.gateway_transaction_id or ''

    if extra:
        for k, v in extra.items():
            if isinstance(v, (date, datetime)):
                v = _fmt_date(v)
            elif isinstance(v, (Decimal, float, int)):
                v = _fmt_money(v)
            ctx[k] = '' if v is None else str(v)

    return ctx


def render(body: str, context: dict) -> str:
    """Substitute {{placeholders}}. Unknown placeholders render as empty."""
    if not body:
        return ''
    return _PLACEHOLDER_RE.sub(lambda m: str(context.get(m.group(1), '')), body)


def render_template_type(template_type: str, context: dict) -> str | None:
    """Look up an active MessageTemplate by type and render it."""
    try:
        from models import MessageTemplate
        tpl = MessageTemplate.query.filter_by(
            template_type=template_type, is_active=True).first()
    except Exception:
        tpl = None
    if not tpl:
        return None
    return render(tpl.body, context)


# --------------------------------------------------------------------------- #
#  Transport
# --------------------------------------------------------------------------- #
class SendResult:
    __slots__ = ('ok', 'status', 'detail', 'phone', 'body')

    def __init__(self, ok, status, detail='', phone='', body=''):
        self.ok = ok
        self.status = status          # sent | failed | skipped | dry-run
        self.detail = (detail or '')[:500]
        self.phone = phone
        self.body = body

    def __bool__(self):
        return bool(self.ok)

    def __repr__(self):
        return f"<SendResult {self.status} {self.phone}: {self.detail[:60]}>"


def _substitute_transport(template: str, phone: str, message: str) -> str:
    """Fill {phone}/{message}/{token}/{instance_id}/{sender} in a URL or body."""
    return (template
            .replace('{phone}', phone)
            .replace('{message}', message)
            .replace('{token}', _setting('wa_api_token'))
            .replace('{instance_id}', _setting('wa_instance_id'))
            .replace('{sender}', _setting('wa_sender')))


def _log(customer_id, phone, body, channel, result, template_type=None):
    """Persist the attempt to message_logs. Never raises."""
    try:
        from models import db, MessageLog
        db.session.add(MessageLog(
            customer_id=customer_id,
            phone=phone or '',
            channel=channel,
            template_type=template_type,
            body=body or '',
            status=result.status,
            error=('' if result.ok else result.detail)[:500],
        ))
        db.session.commit()
    except Exception:
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass


def send_whatsapp(phone, message, customer_id=None, template_type=None,
                  channel='whatsapp') -> SendResult:
    """
    Send one WhatsApp message. Always returns a SendResult; never raises.
    Supports generic gateways (via payload template) and WebAssist.com natively.
    """
    msisdn = normalize_phone(phone)
    if not msisdn:
        res = SendResult(False, 'skipped', 'No valid mobile number', phone or '', message)
        _log(customer_id, phone, message, channel, res, template_type)
        return res

    if not is_configured():
        print(f"[WhatsApp dry-run] -> {msisdn}\n{message}\n")
        res = SendResult(True, 'dry-run', 'Gateway not configured', msisdn, message)
        _log(customer_id, msisdn, message, channel, res, template_type)
        return res

    if requests is None:
        res = SendResult(False, 'failed', "'requests' library is not installed", msisdn, message)
        _log(customer_id, msisdn, message, channel, res, template_type)
        return res

    provider = _setting('wa_provider', 'generic').lower()
    api_url = _setting('wa_api_url')
    token = _setting('wa_api_token')
    headers = {'Accept': 'application/json'}

    try:
        if provider == 'webassist':
            # WebAssist.com specific API integration
            url = "https://wabassist.com/api/send"  # Update if API endpoint changes
            payload = {
                "api_key": token,
                "number": msisdn,
                "message": message
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
        else:
            # Generic gateway logic (Gupshup, Ultramsg, custom)
            url = _substitute_transport(api_url, msisdn, message)
            method = (_setting('wa_http_method') or 'POST').upper()
            payload_tpl = _setting('wa_payload_template') or ''

            if token and '{token}' not in (api_url + payload_tpl):
                headers['Authorization'] = f"Bearer {token}"

            if method == 'GET':
                resp = requests.get(url, headers=headers, timeout=15)
            else:
                raw = _substitute_transport(payload_tpl, msisdn, _json_escape(message))
                try:
                    data = json.loads(raw) if raw.strip() else {}
                    headers['Content-Type'] = 'application/json'
                    resp = requests.post(url, json=data, headers=headers, timeout=15)
                except json.JSONDecodeError:
                    resp = requests.post(url, data=_parse_form(raw), headers=headers, timeout=15)

        ok = 200 <= resp.status_code < 300
        detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
        res = SendResult(ok, 'sent' if ok else 'failed', detail, msisdn, message)
    except Exception as exc:
        res = SendResult(False, 'failed', f"{type(exc).__name__}: {exc}", msisdn, message)

    _log(customer_id, msisdn, message, channel, res, template_type)
    return res


def _json_escape(s: str) -> str:
    """Escape a string so it can be dropped inside a JSON string literal."""
    return json.dumps(s)[1:-1]


def _parse_form(raw: str) -> dict:
    out = {}
    for pair in raw.split('&'):
        if '=' in pair:
            k, v = pair.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def send_sms(phone, message, customer_id=None, template_type=None) -> SendResult:
    """SMS shares the WhatsApp transport by default (most Indian gateways do)."""
    return send_whatsapp(phone, message, customer_id, template_type, channel='sms')


# --------------------------------------------------------------------------- #
#  High-level helper used everywhere in app.py
# --------------------------------------------------------------------------- #
def send_template(customer, template_type, *, plan=None, customer_plan=None,
                  invoice=None, payment=None, extra=None) -> SendResult:
    """
    Render `template_type` for `customer` and deliver it over WhatsApp.
    """
    if customer is None:
        return SendResult(False, 'skipped', 'No customer')

    ctx = build_context(customer=customer, plan=plan, customer_plan=customer_plan,
                        invoice=invoice, payment=payment, extra=extra)
    body = render_template_type(template_type, ctx)
    if not body:
        return SendResult(False, 'skipped',
                          f"No active template of type '{template_type}'",
                          customer.mobile or '')
    return send_whatsapp(customer.mobile, body,
                         customer_id=customer.id, template_type=template_type)


# --------------------------------------------------------------------------- #
#  Default template bodies (seeded on first boot)
#  INCLUDES the new Summary and Detailed bill templates!
# --------------------------------------------------------------------------- #
DEFAULT_TEMPLATES = [
    dict(
        name='Plan Renewed',
        template_type='renewal',
        body=(
            "Plan Renewed !!\n\n"
            "Dear {{customer_name}}, Your Broadband Connection Renewed and the "
            "Invoice is Generated, the amount due is Rs.{{amount}}.\n\n"
            "Kindly make the payment at the earliest to avoid disconnection.\n\n"
            "For Online Payment\n\n"
            "App {{app_link}}\n\n"
            "Web: {{web_link}}\n\n"
            "Thank you for choosing\n"
            "\u260e\ufe0f{{company_phone}}.\n\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Plan Expiring - 3 Days',
        template_type='expiry_3d',
        body=(
            "\u26a0\ufe0f Plan Expiring !!\n\n"
            "Dear {{username}} Internet Connection will expire in Next 3 Days.\n\n"
            "Kindly renew it now\n\n"
            "For Online Payment\n\n"
            "App {{app_link}}\n\n"
            "Web: {{web_link}}\n\n"
            "Thank you for choosing us\n\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Plan Expiring - 2 Days',
        template_type='expiry_2d',
        body=(
            "\u26a0\ufe0f Plan Expiring in 2 Days !!\n\n"
            "Your Internet Connection will expire in next 2 Days .\n\n"
            "Kindly renew it now\n\n"
            "For Online Payment\n\n"
            "App {{app_link}}\n\n"
            "Web: {{web_link}}\n\n"
            "Thank you for choosing us\n\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Plan Expired',
        template_type='expired',
        body=(
            "\u26a0\ufe0f Plan Expired !!\n\n"
            "Dear {{username}} Your Internet Connection Has Been Expired.\n\n"
            "Kindly renew it now\n\n"
            "For Online Payment\n\n"
            "App {{app_link}}\n\n"
            "Web: {{web_link}}\n\n"
            "Thank you for choosing us\n\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Payment Received',
        template_type='payment_received',
        body=(
            "Payment Received \u2705\n\n"
            "Thank you {{customer_name}} for making payment\n\n"
            "Paid Rs.{{paid_amount}},\n"
            "Balance is Rs.{{balance}},\n\n"
            "For Online Payment\n\n"
            "App {{app_link}}\n\n"
            "Web: {{web_link}}\n\n"
            "Thank you for choosing us\n\n"
            "{{company_phone}}\n\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Due Reminder',
        template_type='due_reminder',
        body=(
            "\U0001f514 Payment Reminder\n\n"
            "Dear {{customer_name}}, an amount of Rs.{{due_amount}} is pending "
            "on your account (Invoice {{invoice_no}}).\n\n"
            "Kindly make the payment at the earliest to avoid disconnection.\n\n"
            "For Online Payment\n\n"
            "App {{app_link}}\n\n"
            "Web: {{web_link}}\n\n"
            "Thank you for choosing us\n\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Invoice / Bill',
        template_type='bill',
        body=(
            "\U0001f9fe Invoice {{invoice_no}}\n\n"
            "Dear {{customer_name}}, your invoice for {{plan_name}} is ready.\n\n"
            "Invoice Amount : Rs.{{amount}}\n"
            "Amount Due     : Rs.{{due_amount}}\n\n"
            "For Online Payment\n\n"
            "App {{app_link}}\n\n"
            "Web: {{web_link}}\n\n"
            "Thank you for choosing us\n\n"
            "{{company_name}}"
        ),
    ),
    # --- NEW TEMPLATES ADDED FOR UI BUTTONS ---
    dict(
        name='Summary Bill',
        template_type='summary_bill',
        body=(
            "\U0001f4dc Summary Invoice {{invoice_no}}\n\n"
            "Dear {{customer_name}}, here is your summary invoice:\n\n"
            "Amount : Rs.{{amount}}\n"
            "Due    : Rs.{{due_amount}}\n\n"
            "For Online Payment\n\n"
            "App {{app_link}}\n\n"
            "Web: {{web_link}}\n\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Detailed Bill',
        template_type='detailed_bill',
        body=(
            "\U0001f4dc Detailed Invoice {{invoice_no}}\n\n"
            "Dear {{customer_name}}, please find the detailed breakdown of your invoice below.\n\n"
            "Total Amount : Rs.{{amount}}\n"
            "Amount Due   : Rs.{{due_amount}}\n\n"
            "Thank you for choosing us.\n\n"
            "{{company_name}}"
        ),
    ),
]
def seed_default_templates():
    """Insert the standard message templates once, on first boot."""
    from models import db, MessageTemplate
    created = 0
    for spec in DEFAULT_TEMPLATES:
        exists = MessageTemplate.query.filter_by(
            template_type=spec['template_type']).first()
        if not exists:
            db.session.add(MessageTemplate(is_active=True, **spec))
            created += 1
    if created:
        db.session.commit()
    return created