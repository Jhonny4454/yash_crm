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
import sys
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


def _all_settings():
    """Every setting row, read once per request instead of once per lookup.

    ``_setting()`` used to issue its own SELECT. That is invisible on a single
    call and ruinous in a loop: sending one WhatsApp message asks for the
    provider, the endpoint, the token, the instance id and the country code -
    eleven queries per message, measured. A bulk run of 500 reminders was
    therefore issuing thousands of round trips before a single message left
    the building, and on a database in another datacentre each of those is
    tens of milliseconds. The whole panel felt broken; the cause was here.

    Cached on ``g``, so the lifetime is one request or one scheduler job -
    never longer. A setting changed in the admin UI is live on the next
    request either way, and :func:`invalidate_settings_cache` covers the one
    request that does the changing.
    """
    try:
        from flask import g, has_app_context
        if not has_app_context():
            return None
    except Exception:
        return None

    cached = getattr(g, '_settings_cache', None)
    if cached is None:
        try:
            from models_ext import Setting, ENCRYPTED_SETTINGS, decrypt_setting_value
            cached = {}
            for row in Setting.query.all():
                val = row.value
                if row.key in ENCRYPTED_SETTINGS and val:
                    try:
                        val = decrypt_setting_value(val)
                    except Exception:
                        pass
                cached[row.key] = val
        except Exception:
            # No table yet, or the schema is behind. Fall through to env and
            # defaults rather than failing every send.
            cached = {}
        g._settings_cache = cached
    return cached


def invalidate_settings_cache():
    """Forget the per-request cache after a setting is written."""
    try:
        from flask import g, has_app_context
        if has_app_context() and hasattr(g, '_settings_cache'):
            del g._settings_cache
    except Exception:
        pass


def _setting(key, default=''):
    """Read a setting from the DB, falling back to env, then to `default`."""
    value = None
    rows = _all_settings()
    if rows is not None:
        value = rows.get(key)
        if value is not None and (value or '').strip() != '':
            pass
        else:
            value = None
    else:
        # Outside an application context - a script, or a worker thread that
        # has not pushed one. Ask the database directly.
        try:
            from models_ext import Setting
            row = Setting.query.filter_by(key=key).first()
            if row is not None and (row.value or '').strip() != '':
                value = row.value
        except Exception:
            pass

    if value:
        try:
            from models_ext import ENCRYPTED_SETTINGS, decrypt_setting_value
            if key in ENCRYPTED_SETTINGS:
                value = decrypt_setting_value(value)
        except Exception:
            pass
        return value

    env = os.environ.get(key.upper())
    if env:
        return env
    return DEFAULTS.get(key, default)


#: Providers that ship with their own endpoint, so `wa_api_url` is optional.
#: The value is only a starting point - whatever is set in Settings wins, which
#: is how an operator corrects it without waiting on a code change.
#: How long an outbound gateway call may block a request.
#:
#: This was 15 seconds. The Flask development server handles a small number of
#: requests at a time, so a gateway that accepts the connection and then goes
#: quiet holds a worker for the whole 15s - long enough for Vite's proxy to
#: give up and answer the browser 502, which looks like "the backend is down"
#: rather than "the WhatsApp gateway is slow".
#:
#: (connect, read): fail fast if the host is unreachable, allow a little longer
#: for it to answer once connected.
SEND_TIMEOUT = (
    float(os.environ.get('WA_CONNECT_TIMEOUT', 4)),
    float(os.environ.get('WA_READ_TIMEOUT', 8)),
)

#: The Settings test button is an explicit, deliberate action by an operator
#: who is watching, so it may wait a little longer than a background send.
TEST_TIMEOUT = (
    float(os.environ.get('WA_CONNECT_TIMEOUT', 4)),
    float(os.environ.get('WA_TEST_READ_TIMEOUT', 12)),
)

#: Meta's Cloud API version. Pinned rather than floating: Meta deprecates
#: versions on a schedule, and a silently-moving target is not something a
#: billing system should depend on.
META_GRAPH_VERSION = os.environ.get('META_GRAPH_VERSION', 'v21.0')

PROVIDER_ENDPOINTS = {
    # Taken from WabAssist's own "Send Text Message" documentation. The value
    # here before was a guess and wrong in three separate ways: wrong host,
    # wrong path, and the key sent in the body instead of a Bearer header.
    # It answered 404 for every send.
    'webassist': 'https://api.wabassist.com/api/v1/messages/text',
    # Meta needs the phone number id in the path, so the address is built per
    # send rather than being a constant. See _meta_endpoint().
    'meta_cloud': '',
}


def _meta_endpoint():
    """``https://graph.facebook.com/<ver>/<phone_number_id>/messages``.

    The phone number id lives in `wa_instance_id` - a setting that already
    existed for gateways that call the same thing an instance.
    """
    configured = (_setting('wa_api_url') or '').strip()
    if configured:
        return configured
    phone_id = (_setting('wa_instance_id') or '').strip()
    if not phone_id:
        return ''
    return (f'https://graph.facebook.com/{META_GRAPH_VERSION}'
            f'/{phone_id}/messages')


def provider_endpoint(provider=None):
    """The URL a send will actually go to, for this provider."""
    provider = (provider or _setting('wa_provider', 'generic')).lower()
    if provider == 'meta_cloud':
        return _meta_endpoint()
    configured = (_setting('wa_api_url') or '').strip()
    return configured or PROVIDER_ENDPOINTS.get(provider, '')


def is_configured() -> bool:
    """True when the gateway has enough config to attempt a real send.

    A provider with a built-in endpoint does not also need `wa_api_url` set.
    Requiring it meant the WebAssist option could only be switched on by
    entering a URL that the send path then ignored - the setting looked
    accepted and changed nothing.
    """
    if _setting('wa_enabled') not in ('1', 'true', 'True', 'yes', 'on'):
        return False
    return bool(provider_endpoint())


# --------------------------------------------------------------------------- #
#  Phone normalisation
# --------------------------------------------------------------------------- #
#: How many digits a subscriber number has, once the country code is off.
#: Only needed for the country codes this deployment actually sends to; the
#: default covers the rest well enough to keep the old behaviour.
_NATIONAL_DIGITS = {
    '91': 10,    # India
    '1': 10,     # US / Canada
    '44': 10,    # UK
    '61': 9,     # Australia
    '971': 9,    # UAE
    '977': 10,   # Nepal
    '880': 10,   # Bangladesh
    '94': 9,     # Sri Lanka
}
_DEFAULT_NATIONAL_DIGITS = 10


def normalize_phone(raw: str, country_code: str | None = None) -> str | None:
    """
    Turn whatever the operator typed into a bare international MSISDN.

    The country code is decided by LENGTH, not by prefix.

    "Does it already start with 91?" is the obvious test and it is wrong for
    India, because a perfectly ordinary ten-digit mobile can itself begin 91 -
    9187654321 is a real number shape. The old test saw the leading 91, decided
    the number was already international, and sent a ten-digit MSISDN to the
    gateway. WhatsApp cannot route that, so it answers **131026 Message
    undeliverable** - and it does so only for that slice of customers, which is
    why it reads as "WhatsApp works, except for some people".

    Length settles it: ten digits is a local number and always needs the code
    in front; twelve digits starting with 91 is already international. Anything
    of an unexpected length falls back to the old prefix test rather than being
    thrown away, so nothing that used to send stops sending.
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

    national = _NATIONAL_DIGITS.get(cc, _DEFAULT_NATIONAL_DIGITS)

    if len(digits) == national:
        # A local subscriber number, whatever it happens to start with.
        return cc + digits
    if len(digits) == len(cc) + national and digits.startswith(cc):
        # Already carries the country code.
        return digits
    if not digits.startswith(cc):
        digits = cc + digits
    return digits


# --------------------------------------------------------------------------- #
#  Template rendering
# --------------------------------------------------------------------------- #
_PLACEHOLDER_RE = re.compile(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}')


def _fmt_money(v):
    """Whole rupees in every message, matching what the screens now show.

    A bill that says Rs.3050.86 on WhatsApp and Rs.3,051 in the CRM is a phone
    call, so the two have to round the same way.
    """
    if v is None:
        return '0'
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return str(v)


def _fmt_date(v):
    if not v:
        return ''
    if isinstance(v, (date, datetime)):
        return v.strftime('%d-%b-%Y')
    return str(v)


def _company():
    """The one Company row, once per request rather than once per message."""
    try:
        from flask import g, has_app_context
        from models import Company
    except Exception:
        return None
    if not has_app_context():
        try:
            return Company.query.first()
        except Exception:
            return None
    if not hasattr(g, '_company_row'):
        try:
            g._company_row = Company.query.first()
        except Exception:
            g._company_row = None
    return g._company_row


def build_context(customer=None, plan=None, customer_plan=None,
                  invoice=None, payment=None, extra=None) -> dict:
    """Assemble the placeholder dictionary for a template render."""
    ctx = {
        'today':         _fmt_date(date.today()),
        # Filled in below when there is an invoice: a signed, expiring link to
        # the actual PDF. Without it the bill message announces an invoice the
        # customer has no way to read.
        'bill_link':     '',
        'receipt_link':  '',
        'app_link':      _setting('app_link', 'https://bit.ly/4bBo8kd'),
        'web_link':      _setting('web_link', 'https://yashinternetservices.in'),
        'company_name':  'YASH INTERNET SERVICES',
        'company_phone': '9029508777',
    }

    try:
        company = _company()
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

    # Callers that only have an invoice (addon charges, "resend bill", the
    # gateway callback) used to leave {{plan_name}} / {{expiry_date}} blank.
    # Derive the plan from the invoice, then from the customer's active plan,
    # so every caller gets a complete context without having to pass it.
    if cp is None and invoice is not None:
        cp = getattr(invoice, 'customer_plan', None)
    if cp is None and customer is not None:
        try:
            from models import CustomerPlan
            cp = (CustomerPlan.query
                  .filter_by(customer_id=customer.id, status='active')
                  .order_by(CustomerPlan.end_date.desc())
                  .first())
        except Exception:
            cp = None

    if cp is not None:
        ctx['expiry_date'] = _fmt_date(cp.end_date)
        ctx['renew_date'] = _fmt_date(cp.start_date)
        if cp.end_date:
            ctx['days'] = str(max((cp.end_date - date.today()).days, 0))
        plan = plan or cp.plan

    if plan is not None:
        ctx['plan_name'] = plan.name or ''
        ctx['speed'] = str(plan.speed_mbps or '')
        # Renewal templates must quote the price the customer actually pays,
        # not the shared plan's list price. Invoice context below still wins
        # when a concrete invoice is available.
        quoted_price = (cp.effective_price if cp is not None
                        and cp.plan_id == plan.id else plan.price_monthly)
        ctx.setdefault('amount', _fmt_money(quoted_price))

    if invoice is not None:
        # A link to the real PDF, signed and time-limited. Wrapped because it
        # needs an app context and a base URL, and a bill message that reaches
        # the customer without a link still beats one that does not send.
        try:
            from services.signed_links import invoice_pdf_link
            ctx['bill_link'] = invoice_pdf_link(invoice.id)
        except Exception:
            ctx['bill_link'] = ''

        ctx['invoice_no'] = invoice.invoice_no or ''
        ctx['amount'] = _fmt_money(invoice.total_amount)
        ctx['due_amount'] = _fmt_money(invoice.balance)
        ctx['balance'] = _fmt_money(invoice.balance)
        # An addon / other invoice has no plan of its own - bill it under its
        # own caption rather than sending "your invoice for  is ready".
        if (invoice.invoice_type or 'plan') != 'plan' or not ctx.get('plan_name'):
            caption = (invoice.caption
                       or getattr(invoice, 'display_caption', None) or '').strip()
            if caption and caption != '-':
                ctx['plan_name'] = caption
        ctx.setdefault('plan_name', '')

    if payment is not None:
        ctx['paid_amount'] = _fmt_money(payment.amount)
        ctx['receipt_no'] = payment.receipt_no
        # The receipt PDF, as a link Meta can fetch for a document header.
        try:
            from services.signed_links import receipt_pdf_link
            ctx['receipt_link'] = receipt_pdf_link(payment.id)
        except Exception:
            ctx['receipt_link'] = ''
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


def active_template(template_type: str):
    """The active template row for one type, looked up once per request.

    Two places want this row for every message - the body to render, and the
    Meta template mapping - so a bulk run asked the database for the same row
    twice per customer. It cannot change mid-request, so it is fetched once
    and kept on ``g`` for the life of the request or scheduler job.
    """
    try:
        from flask import g, has_app_context
        from models import MessageTemplate
    except Exception:
        return None

    if not has_app_context():
        try:
            from models import MessageTemplate as MT
            return MT.query.filter_by(template_type=template_type,
                                      is_active=True).first()
        except Exception:
            return None

    cache = getattr(g, '_template_cache', None)
    if cache is None:
        cache = {}
        g._template_cache = cache
    if template_type not in cache:
        try:
            cache[template_type] = MessageTemplate.query.filter_by(
                template_type=template_type, is_active=True).first()
        except Exception:
            cache[template_type] = None
    return cache[template_type]


def render_template_type(template_type: str, context: dict) -> str | None:
    """Look up an active MessageTemplate by type and render it."""
    tpl = active_template(template_type)
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


def _print_dry_run(msisdn, message):
    """Write a gateway preview without letting console encoding break a job.

    Windows terminals commonly use cp1252, which cannot display many emoji in
    message templates.  Dry-run output is diagnostic only, so it must never
    prevent a scheduled reminder from completing.
    """
    preview = f"[WhatsApp dry-run] -> {msisdn}\n{message}\n"
    encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    safe_preview = preview.encode(encoding, errors='backslashreplace').decode(encoding)
    print(safe_preview)


def send_whatsapp(phone, message, customer_id=None, template_type=None,
                  channel='whatsapp', meta_template=None) -> SendResult:
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
        _print_dry_run(msisdn, message)
        res = SendResult(True, 'dry-run', 'Gateway not configured', msisdn, message)
        _log(customer_id, msisdn, message, channel, res, template_type)
        return res

    if requests is None:
        res = SendResult(False, 'failed', "'requests' library is not installed", msisdn, message)
        _log(customer_id, msisdn, message, channel, res, template_type)
        return res

    try:
        method, url, kwargs, _described = build_request(msisdn, message,
                                                        meta_template)
        resp = requests.request(method, url, timeout=SEND_TIMEOUT, **kwargs)
        ok, status, detail = interpret_response(resp.status_code, resp.text)

        # The caller asked for an approved template and this gateway sent
        # plain text instead. The gateway will still answer 200/QUEUED, so
        # without this line the log records a success for a message that Meta
        # will drop for every customer outside the 24-hour window - which is
        # all of them, for a bill.
        if meta_template and not provider_sends_templates():
            detail = f'{detail}\n\n{NO_TEMPLATE_SUPPORT}'

        res = SendResult(ok, status, detail, msisdn, message)
    except Exception as exc:
        res = SendResult(False, 'failed', f"{type(exc).__name__}: {exc}", msisdn, message)

    _log(customer_id, msisdn, message, channel, res, template_type)
    return res


#: The explanation for "the gateway said success and nothing arrived".
#:
#: Written out once, here, because it is the answer to the single most
#: expensive question this system has produced - and because an operator
#: reading it needs a way to CHECK it, not just be told it.
THE_24_HOUR_RULE = (
    'If the gateway accepted it but nothing reaches the handset, the usual '
    'cause is not a fault - it is Meta policy. A business may only send free '
    'text to someone who has messaged it in the last 24 hours. Outside that '
    'window WhatsApp accepts the message, reports success, and delivers '
    'nothing unless it is a TEMPLATE approved by Meta.\n\n'
    'Two-minute proof: from the handset you are testing, send any message '
    '("hi") to your WhatsApp Business number, then press Send test again '
    'within a few minutes. If it arrives that way and not otherwise, the '
    '24-hour rule is the whole problem - and every bill and reminder has to '
    'go out as an approved template.'
)

#: Statuses that mean "this left the building": the gateway has the message
#: ('sent', 'queued'), or sending is deliberately switched off ('dry-run').
#: Callers check membership here rather than writing their own tuple - when
#: 'queued' was introduced, five separate ``status in ('sent', 'dry-run')``
#: tests would silently have started reporting successful sends as failures.
DELIVERABLE_STATUSES = ('sent', 'queued', 'dry-run')

#: What a gateway says when it has TAKEN a message but not delivered it.
#: Meta's numeric error codes, in words an operator can act on.
#:
#: The gateway passes these straight through, and on their own they are
#: useless: "131026 Message undeliverable" has eight unrelated causes and the
#: message names none of them. Every one of these cost a support conversation
#: to identify once; naming them here means it costs nothing the next time.
WHATSAPP_ERROR_HINTS = {
    '131026': (
        "WhatsApp refused to deliver to THAT NUMBER - this is about the "
        "recipient, not about your templates or your gateway. In order of "
        "likelihood: (1) you are sending to your own WhatsApp Business "
        "number, which is not allowed - one API account cannot message "
        "another, so test with an ordinary personal phone instead; (2) the "
        "number has no WhatsApp account; (3) they have blocked your business "
        "number; (4) their WhatsApp is too old, or they have not accepted "
        "Meta's current terms."),
    '131047': (
        "Outside the 24-hour window and sent as free text. Only an approved "
        "template reaches somebody who has not messaged you today."),
    '131049': (
        "Meta held this back to protect the user experience - usually too "
        "many MARKETING messages to that person recently. Bills and reminders "
        "should be categorised UTILITY in WhatsApp Manager, not Marketing."),
    '132000': (
        "The number of variables sent does not match the approved template. "
        "Check the mapping in link_meta_templates.py against the template in "
        "WhatsApp Manager."),
    '132001': (
        "No approved template with that name and language. The language code "
        "matters: 'en' and 'en_US' are different templates to Meta."),
    '132015': "That template is paused by Meta for poor quality.",
    '132016': "That template was disabled by Meta.",
    '133010': "The sending number is not registered with the Cloud API.",
    '130429': (
        "Rate limited. The messaging limit counts unique recipients across "
        "the whole business portfolio in a rolling 24 hours - any other "
        "system sending on this account uses the same allowance."),
    '131048': (
        "Blocked for quality reasons: your number's rating has dropped far "
        "enough that Meta is limiting sends."),
}


def explain_gateway_error(text):
    """The plain-English cause behind a numeric code in a gateway reply."""
    body = str(text or '')
    for code, hint in WHATSAPP_ERROR_HINTS.items():
        if code in body:
            return f'[{code}] {hint}'
    return ''


_ACCEPTED_WORDS = ('queued', 'accepted', 'pending', 'submitted', 'scheduled',
                   'processing')

#: ...and when it has decided not to.
_REFUSED_WORDS = ('failed', 'error', 'rejected', 'undelivered', 'invalid',
                  'blocked')


def interpret_response(status_code, text):
    """``(ok, status, detail)`` - what the gateway SAID, not that it answered.

    This used to be ``ok = 200 <= status_code < 300``, and the row was written
    as 'sent'. WabAssist answers ``200 {"status":"QUEUED","success":true}``,
    which means it has taken custody of the message - not that WhatsApp has
    carried it to anybody. Recording that as 'sent' is how the CRM came to
    report success for messages that were never delivered, which sent us
    looking for a bug in the CRM when the message had not left the gateway.

    'queued' is deliberately a distinct status rather than a flavour of
    failure. Most queued messages do arrive; the point is that nobody should
    read "sent" on this screen and conclude the customer has been told.
    """
    body = (text or '')[:600]
    payload = {}
    if body.strip().startswith('{'):
        try:
            payload = json.loads(body) or {}
        except Exception:
            payload = {}

    if not 200 <= status_code < 300:
        hint = explain_gateway_error(body)
        return False, 'failed', (f'HTTP {status_code}: {body[:200]}'
                                 + (f'\n\n{hint}' if hint else ''))

    state = str(payload.get('status') or payload.get('state')
                or payload.get('message_status') or '').strip().lower()

    # A 2xx that says it failed. Gateways do this: the HTTP call succeeded and
    # the send did not, so the status code is not the answer.
    if payload.get('success') is False or any(w in state for w in _REFUSED_WORDS):
        reason = (payload.get('message') or payload.get('error')
                  or payload.get('detail') or state or body[:200])
        hint = explain_gateway_error(body)
        return False, 'failed', (f'The gateway refused it: {reason}'
                                 + (f'\n\n{hint}' if hint else ''))

    if any(word in state for word in _ACCEPTED_WORDS):
        return True, 'queued', (
            f'HTTP {status_code} {state.upper()} - the gateway has taken the '
            f'message but has not confirmed that WhatsApp delivered it.')

    return True, 'sent', f'HTTP {status_code}: {body[:200]}'


def _redact(value):
    """Enough of a token to recognise, not enough to use."""
    text = str(value or '')
    if len(text) <= 8:
        return '*' * len(text)
    return f'{text[:4]}{"*" * (len(text) - 8)}{text[-4:]}'


#: Providers this application can send an APPROVED TEMPLATE through.
#:
#: This is the whole ball game for a billing system. Meta only carries free
#: text to somebody who has messaged the business in the last 24 hours, and
#: nobody messages their ISP before a bill arrives - so every bill, renewal
#: and expiry reminder has to go as a template or it is accepted by the
#: gateway and delivered to nobody.
#:
#: Meta Cloud and WabAssist both build a real template payload - WabAssist
#: takes Meta's own `components` array on a second endpoint
#: (/messages/template rather than /messages/text), so the same builder
#: serves both.
#:
#: The GENERIC path still posts free text whatever it is given, because a
#: custom gateway's payload template has nowhere to put positional template
#: arguments. On that provider the mapping in link_meta_templates.py is
#: computed and thrown away, which is how a send reports HTTP 200 QUEUED and
#: never reaches a handset.
TEMPLATE_CAPABLE_PROVIDERS = {'meta_cloud', 'webassist'}


def provider_sends_templates(provider=None):
    provider = (provider or _setting('wa_provider', 'generic') or '').lower()
    return provider in TEMPLATE_CAPABLE_PROVIDERS


#: Said in one place so the tester, the message log and the send result all
#: give the operator the same sentence.
NO_TEMPLATE_SUPPORT = (
    'This message was mapped to a Meta-approved template, but the current '
    'gateway can only send free text - so it will only reach customers who '
    'have messaged you in the last 24 hours. Switch the gateway to WabAssist '
    'or "Meta Cloud API (direct)" in Settings to send it as the approved '
    'template.')


def template_components(meta_template):
    """Meta's `components` array for one approved template.

    Shared by every provider that speaks Meta's component shape - which is
    both of the ones here, because a reseller that fronts the Cloud API has no
    reason to invent its own. Keeping one builder means the header and the
    body cannot drift apart between transports; two copies of positional
    argument-ordering is a bug waiting to be written twice.
    """
    components = []

    # The header comes FIRST. Meta matches components positionally against
    # the approved template, and a document header supplied after the body is
    # rejected as a structure mismatch.
    document = (meta_template or {}).get('document')
    if document:
        components.append({
            'type': 'header',
            'parameters': [{
                'type': 'document',
                'document': {'link': document['link'],
                             'filename': document['filename']},
            }],
        })

    # A template with no variables must omit `components` entirely - sending
    # an empty body component is rejected.
    if (meta_template or {}).get('parameters'):
        components.append({
            'type': 'body',
            'parameters': [{'type': 'text', 'text': value}
                           for value in meta_template['parameters']],
        })

    return components


def webassist_template_endpoint():
    """Their template endpoint, derived from whatever the text one is.

    WabAssist publishes two addresses that differ only in the last segment:

        POST /api/v1/messages/text        {to, text}
        POST /api/v1/messages/template    {to, template_name, language_code,
                                           components}

    `wa_api_url` holds the text one, because that is what a send used to be.
    Deriving the template address from it rather than hardcoding a second
    constant means a corrected host - a staging instance, a custom domain -
    keeps working for both, which was the whole point of making the text URL
    configurable in the first place.
    """
    base = (provider_endpoint('webassist') or '').strip().rstrip('/')
    if not base:
        return ''
    if base.endswith('/text'):
        return base[:-len('/text')] + '/template'
    if base.endswith('/template'):
        return base
    return f'{base}/template'


def build_request(msisdn, message, meta_template=None):
    """The exact HTTP call a send will make.

    Returns ``(method, url, requests_kwargs, described)`` where ``described``
    is a token-redacted copy suitable for showing an operator.

    Split out of send_whatsapp so the Test Message button exercises *this*
    function rather than a second copy of the same logic. A test that builds
    its own request can pass while real sends fail, which makes it worse than
    no test at all.
    """
    provider = _setting('wa_provider', 'generic').lower()
    api_url = _setting('wa_api_url')
    token = _setting('wa_api_token')
    headers = {'Accept': 'application/json'}

    if provider == 'webassist':
        # The endpoint is a *default*, not a constant: whatever is in
        # `wa_api_url` wins. It used to be hardcoded here, so a corrected URL
        # entered in Settings was silently discarded and every send still went
        # to the built-in address.
        url = provider_endpoint('webassist')

        # Bearer header, per their docs - not the api_key-in-the-body shape
        # this used to send.
        headers['Authorization'] = f'Bearer {token}'

        # Their examples use E.164 WITH the leading plus (+14155552671).
        # normalize_phone deliberately returns bare digits, because most Indian
        # gateways reject the plus - so it is added back here, for this
        # provider only, rather than changing what every other one receives.
        if meta_template:
            payload = {
                'to': f'+{msisdn}',
                'template_name': meta_template['name'],
                'language_code': meta_template['language'],
                'components': template_components(meta_template),
            }
            url = webassist_template_endpoint()
        else:
            payload = {'to': f'+{msisdn}', 'text': message}

        described = {
            'provider': 'webassist', 'method': 'POST', 'url': url,
            'body': payload, 'auth': f'Bearer {_redact(token)}',
        }
        return 'POST', url, {'json': payload, 'headers': headers}, described

    if provider == 'meta_cloud':
        # Meta's Cloud API, called directly. Documented and stable, and it
        # needs nothing from a reseller in between - useful when the reseller
        # has no send API of its own.
        url = _meta_endpoint()
        headers['Authorization'] = f'Bearer {token}'

        if meta_template:
            payload = {
                'messaging_product': 'whatsapp',
                'recipient_type': 'individual',
                'to': msisdn,
                'type': 'template',
                'template': {
                    'name': meta_template['name'],
                    'language': {'code': meta_template['language']},
                },
            }
            components = template_components(meta_template)
            if components:
                payload['template']['components'] = components
            described = {
                'provider': 'meta_cloud', 'method': 'POST', 'url': url,
                'body': payload, 'auth': f'Bearer {_redact(token)}',
            }
            return 'POST', url, {'json': payload, 'headers': headers}, described

        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': msisdn,
            'type': 'text',
            'text': {'preview_url': False, 'body': message},
        }
        described = {
            'provider': 'meta_cloud', 'method': 'POST', 'url': url,
            'body': payload,
            'auth': f'Bearer {_redact(token)}',
        }
        return 'POST', url, {'json': payload, 'headers': headers}, described

    # Generic gateway (Gupshup, Ultramsg, anything with a URL template).
    url = _substitute_transport(api_url, msisdn, message)
    method = (_setting('wa_http_method') or 'POST').upper()
    payload_tpl = _setting('wa_payload_template') or ''

    if token and '{token}' not in (api_url + payload_tpl):
        headers['Authorization'] = f'Bearer {token}'

    if method == 'GET':
        described = {'provider': provider, 'method': 'GET',
                     'url': _substitute_transport(api_url, msisdn, message),
                     'body': None}
        return 'GET', url, {'headers': headers}, described

    raw = _substitute_transport(payload_tpl, msisdn, _json_escape(message))
    try:
        data = json.loads(raw) if raw.strip() else {}
        headers['Content-Type'] = 'application/json'
        described = {'provider': provider, 'method': 'POST', 'url': url,
                     'body': _redact_body(data, token)}
        return 'POST', url, {'json': data, 'headers': headers}, described
    except json.JSONDecodeError:
        form = _parse_form(raw)
        described = {'provider': provider, 'method': 'POST', 'url': url,
                     'body': _redact_body(form, token)}
        return 'POST', url, {'data': form, 'headers': headers}, described


def _redact_body(body, token):
    if not isinstance(body, dict):
        return body
    secret = str(token or '')
    out = {}
    for key, value in body.items():
        if secret and str(value) == secret:
            out[key] = _redact(secret)
        elif any(word in key.lower() for word in ('token', 'key', 'secret', 'password')):
            out[key] = _redact(value)
        else:
            out[key] = value
    return out


#: Path fragments that mean "this address RECEIVES messages" rather than
#: sends them.
_WEBHOOK_MARKERS = ('/webhook', '/webhooks', '/callback', '/incoming',
                    '/receive', '/inbound', '/notify')


def _looks_like_a_webhook(url):
    """Is this a receive endpoint being used as a send endpoint?

    A real mistake, and an expensive one: a webhook receiver answers 200 with
    a cheerful body to almost any POST, because Meta requires it to. So the
    test reports success, the operator believes sending works, and not one
    message ever reaches a customer. Worth naming explicitly rather than
    leaving as a silent false positive.
    """
    lowered = (url or '').lower()
    return any(marker in lowered for marker in _WEBHOOK_MARKERS)


def test_template_context():
    """Obvious placeholder values for a template test.

    Every approved template gets its parameters filled from this, so a test
    exercises the real positional mapping without needing a real customer,
    invoice or payment to exist. The values are deliberately recognisable in
    a chat window - if {{2}} arrives showing the phone number, the ORDER is
    wrong and you can see it at a glance.
    """
    from datetime import date, timedelta
    company = _setting('company_name') or 'Yash Internet Services'
    return {
        'customer_name': 'TEST CUSTOMER',
        'username': 'test.user',
        'plan_name': 'TEST PLAN',
        'expiry_date': (date.today() + timedelta(days=3)).strftime('%d-%b-%Y'),
        'due_date': (date.today() + timedelta(days=3)).strftime('%d-%b-%Y'),
        'amount': '1', 'due_amount': '1', 'paid_amount': '1', 'balance': '0',
        'invoice_no': 'TEST-0001', 'receipt_no': 'TEST-R001',
        'company_name': company,
        'company_phone': _setting('company_phone') or '0000000000',
        'app_link': _setting('app_link') or '', 'web_link': _setting('web_link') or '',
    }


def send_test_template(phone, template_type):
    """Send one APPROVED TEMPLATE, to prove templates reach a cold number.

    The plain test message is free text, so it only ever proves the gateway
    answers - it arrives for anyone who has messaged the business today and
    for nobody else, which is precisely the case that was already working.
    Templates are the half that matters for bills, and until now there was no
    way to try one without waiting for a real bill run.

    Send this to a number that has NOT messaged your business. If it arrives,
    templates work; if it does not, the reason is in `detail`.
    """
    result = {
        'enabled': _setting('wa_enabled') in ('1', 'true', 'True', 'yes', 'on'),
        'provider': _setting('wa_provider', 'generic').lower(),
        'configured': is_configured(),
        'template_type': template_type,
        'mode': 'template',
    }

    msisdn = normalize_phone(phone)
    result['to'] = msisdn or ''
    if not msisdn:
        result.update(status='invalid_number',
                      detail='That does not look like a mobile number.')
        return result

    if not provider_sends_templates():
        result.update(status='not_configured', detail=NO_TEMPLATE_SUPPORT)
        return result

    meta = meta_template_for(template_type, test_template_context())
    if not meta:
        result.update(
            status='not_configured',
            detail=f"'{template_type}' is not linked to an approved Meta "
                   f"template, so it can only go as free text. Run "
                   f"'python link_meta_templates.py' to link the ones that "
                   f"have an approved equivalent.")
        return result

    result['endpoint'] = (webassist_template_endpoint()
                          if result['provider'] == 'webassist'
                          else provider_endpoint())

    # An approved template declared WITH a document header must be sent with
    # one. Sent without, Meta rejects the whole message for a structure
    # mismatch - an error that names nothing useful. Say the real reason.
    if meta['name'] in DOCUMENT_HEADER_TEMPLATE_NAMES and not meta.get('document'):
        result.update(
            status='no_document',
            detail=f"'{meta['name']}' was approved with a PDF header, so it "
                   f"cannot be sent without one. The link is built from "
                   f"PUBLIC_BASE_URL, which is not set - so there is no public "
                   f"address for Meta to fetch the file from. Set it in .env "
                   f"and run 'python doctor.py' to confirm. Every template "
                   f"WITHOUT a PDF works regardless.")
        return result

    body_text = render_template_type(template_type, test_template_context()) or ''
    res = send_whatsapp(phone, body_text or 'template test',
                        template_type=template_type, meta_template=meta)
    result.update(status=res.status, detail=res.detail,
                  sent_as=meta['name'], language=meta['language'],
                  parameters=meta['parameters'])
    return result


def send_test_message(phone, message=None):
    """Prove the gateway works, and say exactly what happened either way.

    Returns a dict the Settings screen renders as-is. Deliberately verbose:
    the failure mode this replaces was a UI that said "sent" whether or not
    anything left the building.
    """
    result = {
        'enabled': _setting('wa_enabled') in ('1', 'true', 'True', 'yes', 'on'),
        'provider': _setting('wa_provider', 'generic').lower(),
        'endpoint': provider_endpoint(),
        'configured': is_configured(),
    }

    msisdn = normalize_phone(phone)
    result['to'] = msisdn or ''

    if not msisdn:
        result.update(status='invalid_number',
                      detail='That does not look like a mobile number. '
                             'Enter it with or without the country code.')
        return result

    if not result['configured']:
        missing = []
        if not result['enabled']:
            missing.append('WhatsApp is switched off (wa_enabled)')
        if not result['endpoint']:
            missing.append('no API URL, and this provider has no built-in one')
        result.update(status='not_configured', detail='; '.join(missing))
        return result

    if requests is None:
        result.update(status='failed',
                      detail="The 'requests' library is not installed in this "
                             "environment, so nothing can be sent.")
        return result

    text = message or ('Test message from your CRM. If you can read this, '
                       'WhatsApp sending is working.')

    try:
        method, url, kwargs, described = build_request(msisdn, text)
    except Exception as exc:
        result.update(status='failed',
                      detail=f'The request could not be built: {exc}')
        return result

    result['request'] = described

    try:
        resp = requests.request(method, url, timeout=TEST_TIMEOUT, **kwargs)
    except Exception as exc:
        result.update(status='failed',
                      detail=f'{type(exc).__name__}: {exc}',
                      hint='The gateway could not be reached. Check the API '
                           'URL and that this server has outbound internet '
                           'access.')
        return result

    body = (resp.text or '')[:600]
    result['response'] = {'http_status': resp.status_code, 'body': body}

    if 200 <= resp.status_code < 300:
        looks_like_webhook = _looks_like_a_webhook(url)
        if looks_like_webhook:
            result.update(
                status='warning',
                detail='That address looks like a WEBHOOK RECEIVER, not a send API.',
                hint='A webhook URL is where Meta delivers messages INTO your '
                     'provider. Receivers answer 200 to almost anything - that '
                     'is required of them - so a success here means nothing was '
                     'sent. Use your provider\'s send endpoint, or switch the '
                     'provider to Meta Cloud API and send directly.')
            return result

        _ok, state, detail = interpret_response(resp.status_code, resp.text)

        if state == 'failed':
            result.update(status='failed', detail=detail,
                          hint='The HTTP call worked; the send did not. The '
                               'gateway\'s own reason is in the response below.')
            return result

        # 'queued' is the case that wasted a day: the gateway answers
        # success, the CRM says sent, and no message ever reaches the phone.
        # Naming it, and saying how to prove it in two minutes, is the whole
        # point of this panel.
        result.update(
            status=state,
            detail=(detail if state == 'queued' else
                    f'The gateway accepted the message (HTTP {resp.status_code}).'),
            hint=THE_24_HOUR_RULE)
        return result

    hint = ('401 or 403 usually means the API key is wrong. '
            '404 means the API URL is wrong.')

    # Meta answers with a numbered code. The 24-hour rule in particular looks
    # like a broken integration when it is really a policy limit, so name it.
    if '131047' in body:
        hint = ("Meta's 24-hour rule: you can only send free text within 24 "
                "hours of the customer last messaging you. Outside that "
                "window it has to be a template approved by Meta.")
    elif '132000' in body or '132001' in body:
        hint = ('Meta does not recognise that template name, or its variables '
                'do not match what was approved.')
    elif '190' in body and 'access token' in body.lower():
        hint = ('The access token has expired or been revoked. Generate a '
                'permanent one in Meta Business > System Users.')
    elif '133010' in body or 'not registered' in body.lower():
        hint = ('That phone number is not registered on the WhatsApp Business '
                'account. Check the Phone Number ID.')

    result.update(status='failed',
                  detail=f'The gateway rejected it (HTTP {resp.status_code}).',
                  hint=hint)
    return result


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
#: Message types that are ABOUT a particular invoice, so a link to that
#: invoice's PDF belongs in them.
BILL_LINK_TYPES = ('bill', 'summary_bill', 'detailed_bill', 'due_reminder')


def with_bill_link(body, template_type, ctx):
    """Append the bill link to a bill message that does not already carry one.

    The message bodies live in the database and the operator edits them, so
    the ones already in a running system predate this feature and will never
    grow a ``{{bill_link}}`` placeholder on their own - restore-defaults
    deliberately does not overwrite a body somebody may have customised.
    Appending it here means every existing installation starts sending the
    actual bill without anyone editing a template, while an operator who HAS
    placed ``{{bill_link}}`` keeps control of where in the message it sits.

    Silently does nothing when there is no link to add - which is the case
    until PUBLIC_BASE_URL points at an address the customer's phone can
    reach. A bill message with no link still beats no bill message.
    """
    link = (ctx.get('bill_link') or '').strip()
    if not link or template_type not in BILL_LINK_TYPES:
        return body
    if link in (body or ''):
        return body                      # the template placed it itself
    return (body or '').rstrip() + (
        f'\n\n\U0001f4c4 View / download your bill:\n{link}')


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
    # `.strip()`, not just falsiness. A template whose body is blank or all
    # whitespace renders to whitespace, which is truthy - so the send went
    # ahead and the customer received an EMPTY WhatsApp message. Failing is
    # the better outcome: it is visible, and it does not confuse the customer.
    if not (body or '').strip():
        # Actionable, because "no active template" tells an operator nothing
        # about what to do next. The templates are seeded on first boot, so
        # the usual causes are a database restored without them or someone
        # having deactivated the row.
        return SendResult(False, 'skipped',
                          f"No active '{template_type}' message template. "
                          f"Add or re-enable it under Settings > Notification "
                          f"templates, or run 'python upgrade_schema.py' to "
                          f"restore the defaults.",
                          customer.mobile or '')
    # If this row is mapped to a Meta-approved template and the transport can
    # use one, send it that way - that is the only form WhatsApp will carry to
    # somebody who has not messaged us in the last 24 hours, which is every
    # customer a bill goes to.
    meta = meta_template_for(template_type, ctx)

    # A template approved WITH a PDF header cannot be sent without the PDF -
    # Meta rejects the whole message for a structure mismatch and names
    # nothing useful. That happens whenever PUBLIC_BASE_URL is unset, because
    # the link the header carries is built from it.
    #
    # Falling back to free text here would be worse than failing: it looks
    # like a delivery, and outside the 24-hour window it is not one. So the
    # send stops, and the message log carries the one setting that fixes it.
    if meta and meta['name'] in DOCUMENT_HEADER_TEMPLATE_NAMES \
            and not meta.get('document'):
        res = SendResult(
            False, 'failed',
            f"'{meta['name']}' is approved with a PDF header and no public "
            f"link could be built for the file. Set PUBLIC_BASE_URL in .env "
            f"to the https address this API answers on, then run "
            f"'python doctor.py'. Messages without a PDF are unaffected.",
            customer.mobile or '', body)
        _log(customer.id, customer.mobile or '', body, 'whatsapp', res,
             template_type)
        return res

    body = with_bill_link(body, template_type, ctx)
    return send_whatsapp(customer.mobile, body, customer_id=customer.id,
                         template_type=template_type, meta_template=meta)


#: Approved templates whose header is a DOCUMENT rather than text.
#:
#: Meta fetches that document itself, from a public URL, so the value has to
#: be a link anybody can open - which is exactly what services.signed_links
#: produces. A template declared with a document header and sent WITHOUT one
#: is rejected outright, so this mapping is what makes the bill actually
#: arrive as a PDF instead of as a line of text about a PDF.
#:
#: name -> (context key holding the link, context key for the filename)
META_DOCUMENT_HEADERS = {
    'invoice_attachment': ('bill_link', 'invoice_no'),
    'receipt_attachment': ('receipt_link', 'receipt_no'),
}

#: The same names, for the checks that only need to ask "does this one carry
#: a PDF?" - a template approved WITH a document header is rejected outright
#: when sent without one.
DOCUMENT_HEADER_TEMPLATE_NAMES = frozenset(META_DOCUMENT_HEADERS)


def meta_document_header(meta_name, ctx):
    """``{'link', 'filename'}`` for a document-header template, or None."""
    spec = META_DOCUMENT_HEADERS.get((meta_name or '').strip())
    if not spec:
        return None
    link_key, name_key = spec
    link = (ctx.get(link_key) or '').strip()
    if not link:
        return None
    stem = str(ctx.get(name_key) or 'document').strip() or 'document'
    # Meta rejects a filename with a path separator or no extension.
    stem = stem.replace('/', '-').replace('\\', '-')[:60]
    return {'link': link, 'filename': f'{stem}.pdf'}


def meta_template_for(template_type, ctx):
    """``{'name', 'language', 'parameters'}`` for an approved Meta template.

    Returns None when this row has no Meta mapping, in which case the caller
    sends the rendered text instead.

    The parameter ORDER is the entire contract. Meta's templates are
    positional - {{1}}, {{2}} - and carry no names, so a mapping stored as
    "customer_name,amount,due_date" has to produce exactly that order every
    time. Storing it as an ordered list rather than reusing the {{...}}
    placeholders found in the body is deliberate: the body's wording is ours
    to change, the approved template's is not, and the two drift.
    """
    row = active_template(template_type)

    if row is None or not (getattr(row, 'meta_template_name', '') or '').strip():
        return None

    keys = [k.strip() for k in (row.meta_variables or '').split(',') if k.strip()]
    parameters = []
    for key in keys:
        value = ctx.get(key, '')
        # Meta rejects a parameter containing a newline or a tab, and an empty
        # one, with an unhelpful generic error. Normalise here rather than
        # letting the send fail with a code nobody can read.
        text = ' '.join(str(value if value not in (None, '') else '-').split())
        parameters.append(text)

    name = row.meta_template_name.strip()
    return {
        'name': name,
        'language': (row.meta_language or 'en').strip() or 'en',
        'parameters': parameters,
        # The PDF itself, when the approved template has a document header.
        'document': meta_document_header(name, ctx),
    }


# --------------------------------------------------------------------------- #
#  Default template bodies (seeded on first boot)
#  INCLUDES the new Summary and Detailed bill templates!
# --------------------------------------------------------------------------- #
#: The standard messages, worded to MATCH the Meta-approved templates.
#:
#: This matters more than it looks. When a type is linked to an approved
#: template (see link_meta_templates.py) the text WhatsApp actually delivers
#: is Meta's, not ours - ours is only what gets written to the message log and
#: shown in the CRM. If the two say different things, the SMS Log shows an
#: operator one message and the customer received another, and the first time
#: anybody notices is on a call about a bill.
#:
#: So each body below is the approved wording with our placeholder names
#: substituted for Meta's {{1}}, {{2}}. Change one and change the other, or
#: they drift apart again.
DEFAULT_TEMPLATES = [
    dict(
        name='Plan Renewed',
        template_type='renewal',
        description='Sent when a plan is renewed and the invoice is raised.',
        body=(
            "Plan Renewed\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your broadband connection has been renewed and the invoice has "
            "been generated.\n"
            "\n"
            "Amount Due: Rs. {{due_amount}}\n"
            "\n"
            "Please make the payment to avoid service interruption.\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "{{company_name}}\n"
            "Thank You\n"
            "\n"
            "Visit Account\n"
            "{{app_link}}"
        ),
    ),
    dict(
        name='Internet Plan Expiring (3 days)',
        template_type='expiry_3d',
        description='Three days before the plan ends.',
        body=(
            "Internet Plan Expiring\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your Internet Plan will expire {{expiry_date}}.\n"
            "\n"
            "Please renew your plan to avoid any interruption\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "{{company_name}}\n"
            "Thank You\n"
            "\n"
            "Visit Account\n"
            "Customer App\n"
            "{{app_link}}"
        ),
    ),
    dict(
        name='Internet Plan Expiring (2 days)',
        template_type='expiry_2d',
        description='Two days before the plan ends.',
        body=(
            "Internet Plan Expiring\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your Internet Plan will expire {{expiry_date}}.\n"
            "\n"
            "Please renew your plan to avoid any interruption\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "{{company_name}}\n"
            "Thank You\n"
            "\n"
            "Visit Account\n"
            "Customer App\n"
            "{{app_link}}"
        ),
    ),
    dict(
        name='Internet Plan Expiring (1 day)',
        template_type='expiry_1d',
        description='One day before the plan ends.',
        body=(
            "Internet Plan Expiring\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your Internet Plan will expire {{expiry_date}}.\n"
            "\n"
            "Please renew your plan to avoid any interruption\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "{{company_name}}\n"
            "Thank You\n"
            "\n"
            "Visit Account\n"
            "Customer App\n"
            "{{app_link}}"
        ),
    ),
    dict(
        name='Plan Expired',
        template_type='expired',
        description='The day the connection stops.',
        body=(
            "Plan Expired\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your Internet Plan has expired.\n"
            "Please renew it now to avoid any interruption\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "{{company_name}}\n"
            "Thank You\n"
            "\n"
            "Visit Account\n"
            "Customer App\n"
            "{{app_link}}"
        ),
    ),
    dict(
        name='Payment Received',
        template_type='payment_received',
        description='Confirmation the money arrived.',
        body=(
            "Payment Received\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your payment of Rs.{{paid_amount}} has been received.\n"
            "Your outstanding balance is Rs.{{balance}}\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "{{company_name}}\n"
            "Thank You"
        ),
    ),
    dict(
        name='Payment Due Reminder',
        template_type='due_reminder',
        description='Chases what the account owes.',
        body=(
            "Payment Due Reminder\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your payment of \u20b9{{due_amount}} is due.\n"
            "Please make the payment to avoid disconnection of "
            "your internet services.\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "{{company_name}}\n"
            "Thank You"
        ),
    ),
    dict(
        name='Invoice / Bill',
        template_type='bill',
        description='The bill, with the PDF attached.',
        body=(
            "Invoice No: {{invoice_no}}\n"
            "Amount: {{amount}}\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Summary Bill',
        template_type='summary_bill',
        description='Same approved template as the bill.',
        body=(
            "Invoice No: {{invoice_no}}\n"
            "Amount: {{amount}}\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Detailed Bill',
        template_type='detailed_bill',
        description='Same approved template as the bill.',
        body=(
            "Invoice No: {{invoice_no}}\n"
            "Amount: {{amount}}\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Receipt',
        template_type='payment_approved',
        description='The receipt, with the PDF attached.',
        body=(
            "Receipt No:{{receipt_no}}\n"
            "Paid Amount: {{paid_amount}}\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Welcome / New Account',
        template_type='welcome',
        description='Sent when a new connection is activated.',
        body=(
            "Welcome\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your username: {{username}}\n"
            "Amount payable: Rs.{{amount}}\n"
            "\n"
            "For support: \u260e\ufe0f {{company_phone}}\n"
            "\n"
            "{{company_name}}\n"
            "Thank You\n"
            "\n"
            "Visit Account\n"
            "{{app_link}}"
        ),
    ),
    dict(
        name='Daily Report',
        template_type='daily_report',
        description='Daily summary sent to admin.',
        body=(
            "\ud83d\udd30 Daily Report - {{today}}\n"
            "\n"
            "\ud83d\udcdd Complaint Report\n"
            "\ud83d\udcdd New Complaint: {{new_complaints}}\n"
            "\u26a0\ufe0f Open Complaint: {{open_complaints}}\n"
            "\ud83e\udeab Old Complaint: {{old_complaints}}\n"
            "\u2705 Closed Complaint: {{closed_complaints}}\n"
            "\n"
            "\ud83d\udca5 Collection Report\n"
            "\u2728 New Leads: {{new_leads}}\n"
            "\ud83c\udfaf New Connection: {{new_connections}}\n"
            "\u2b07\ufe0f Plan Expiring: {{expiring_count}}\n"
            "\u274c Plan Expired: {{expired_count}}\n"
            "\u2705 Plan Renewed: {{renewed_count}}\n"
            "\u26a0\ufe0f Today's Outstanding: \u20b9{{today_outstanding}}\n"
            "\ud83d\udcc9 Total Outstanding: \u20b9{{total_outstanding}}\n"
            "\n"
            "\ud83d\udcb5 Collection Details\n"
            "{{collection_details}}\n"
            "Total Collected Amount: \u20b9{{total_collected}}\n"
            "Thank You"
        ),
    ),
    dict(
        name='Internet Down',
        template_type='internet_down',
        description='Area outage notification to customer.',
        body=(
            "Internet Service Down\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Our internet service is currently down in your area due to a "
            "technical issue. The service is expected to be restored within "
            "{{hours}} hours.\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "\n"
            "{{company_name}}\n"
            "Thank you for your patience and cooperation."
        ),
    ),
    dict(
        name='Internet Restored',
        template_type='internet_restored',
        description='Area restored notification to customer.',
        body=(
            "Internet Service Restored\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "The internet service in your area has now been restored.\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "\n"
            "{{company_name}}\n"
            "Thank you for your patience and continued support."
        ),
    ),
    dict(
        name='Complaint Registered',
        template_type='complaint_registered',
        description='Customer complaint confirmation.',
        body=(
            "Complaint Registered\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your complaint has been registered.\n"
            "\n"
            "Ticket No: {{ticket_no}}\n"
            "\n"
            "User id: {{username}}\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "{{company_name}}\n"
            "Thank you"
        ),
    ),
    dict(
        name='Issue Resolved',
        template_type='issue_resolved',
        description='Complaint resolved notification to customer.',
        body=(
            "Issue Resolved\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your complaint has been successfully resolved.\n"
            "\n"
            "Ticket no: {{ticket_no}}\n"
            "User id: {{username}}\n"
            "\n"
            "Support No: {{company_phone}}\n"
            "{{company_name}}\n"
            "Thank you"
        ),
    ),
    dict(
        name='New Complaint (Admin)',
        template_type='new_complaint',
        description='Admin notification of new complaint.',
        body=(
            "New Complaint Registered\n"
            "\n"
            "A new complaint has been registered with the following details:\n"
            "\n"
            "Complaint: {{complaint_type}}\n"
            "Name: {{customer_name}}\n"
            "User ID: {{username}}\n"
            "Ticket No: {{ticket_no}}\n"
            "Address: {{address}}\n"
            "\n"
            "{{company_name}}"
        ),
    ),

    # ---- No approved equivalent -------------------------------------- #
    # Raised by the portal. These were excused as "the customer just used the
    # portal, so the 24-hour window is open" - which is FALSE. That window
    # opens when somebody messages your number ON WHATSAPP; paying on your
    # website does not open it. Until a Utility template is approved for each,
    # these reach only customers who happen to have messaged you that day.
    # See UNMAPPED in link_meta_templates.py for the wording to submit.
    #
    # 'renewal_approved' used to be in this group and is now mapped to the
    # approved plan_renewed template.
    dict(
        name='Payment Submitted (portal)',
        template_type='payment_submitted',
        description='The customer submitted a payment from the portal.',
        body=(
            "Payment Submitted\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "We have received your payment request of Rs.{{paid_amount}}.\n"
            "It will be confirmed once verified.\n"
            "\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Payment Rejected',
        template_type='payment_rejected',
        description='A submitted payment could not be verified.',
        body=(
            "Payment Rejected\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "We could not verify your payment of Rs.{{paid_amount}}.\n"
            "Please contact us on {{company_phone}}.\n"
            "\n"
            "{{company_name}}"
        ),
    ),
    dict(
        name='Renewal Approved',
        template_type='renewal_approved',
        description='A portal renewal request was approved.',
        body=(
            "Renewal Approved\n"
            "\n"
            "Dear {{first_name}},\n"
            "\n"
            "Your renewal has been approved. Your plan now runs to "
            "{{expiry_date}}.\n"
            "\n"
            "{{company_name}}"
        ),
    ),
]


def seed_default_templates():
    """Insert the standard message templates once, on first boot.

    Also updates the body of any existing template whose body was changed
    in code, and re-activates templates that were deactivated.
    """
    from models import db, MessageTemplate
    created, updated, reactivated = 0, 0, 0
    for spec in DEFAULT_TEMPLATES:
        exists = MessageTemplate.query.filter_by(
            template_type=spec['template_type']).first()
        if not exists:
            db.session.add(MessageTemplate(is_active=True, **spec))
            created += 1
            continue
        if (exists.body or '').strip() != (spec['body'] or '').strip():
            exists.body = spec['body']
            updated += 1
        if not exists.is_active:
            exists.is_active = True
            reactivated += 1
    if created or updated or reactivated:
        db.session.commit()
    return created


def restore_default_templates(reactivate=True):
    """Put the standard templates back, and switch them on.

    Seeding alone is not enough to un-break sending. It skips any type that
    already has a row, so a template that exists but has been deactivated -
    or had its body emptied - stays broken, and every send of that type fails
    with "no active template" while the row sits there looking present.

    Returns what it actually did, so the screen can report it rather than
    claiming a vague success.
    """
    from models import db, MessageTemplate

    created, reactivated, refilled = [], [], []

    for spec in DEFAULT_TEMPLATES:
        kind = spec['template_type']
        row = MessageTemplate.query.filter_by(template_type=kind).first()

        if row is None:
            db.session.add(MessageTemplate(is_active=True, **spec))
            created.append(kind)
            continue

        # An empty body renders to nothing, which fails exactly the same way
        # as a missing row but is far harder to spot on the templates screen.
        if not (row.body or '').strip():
            row.body = spec['body']
            refilled.append(kind)

        if reactivate and not row.is_active:
            row.is_active = True
            reactivated.append(kind)

    if created or reactivated or refilled:
        db.session.commit()

    active = {t.template_type for t in
              MessageTemplate.query.filter_by(is_active=True).all()
              if (t.body or '').strip()}

    return {
        'created': created,
        'reactivated': reactivated,
        'refilled': refilled,
        'active_types': sorted(active),
        'missing_types': sorted({s['template_type'] for s in DEFAULT_TEMPLATES}
                                - active),
    }
