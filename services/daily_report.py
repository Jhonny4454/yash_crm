"""
services/daily_report.py
========================
The admin daily summary — the one template in DEFAULT_TEMPLATES that nothing
ever sent.

``daily_report`` has been sitting in ``services/messaging.py`` since the
templates were seeded, fully written, with fourteen placeholders and no code
anywhere that fills them in. This module fills them in and hands the result to
the existing WhatsApp transport.

Check the numbers before you trust them
---------------------------------------
Every figure here is an interpretation of your schema, and a summary that is
quietly wrong is worse than no summary — nobody double-checks a number that
arrives every night. So look at it first:

    python -c "from services.daily_report import preview; preview()"

That prints each figure with the rule used to derive it, and sends nothing.
Adjust anything that disagrees with how you actually count, then wire the
scheduler job.

Each figure is computed independently. One bad column name shows up as a
single ``?`` rather than killing the whole report — a partial summary still
tells the operator most of what they need.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

log = logging.getLogger(__name__)

#: How far ahead "Plan Expiring" looks. Matches the 3-day reminder in
#: app.py's send_expiry_reminders so the two agree.
EXPIRING_WINDOW_DAYS = 3

#: Shown when a figure could not be computed. Deliberately not '0' -- a zero
#: reads as "nothing happened today", which is a different claim from
#: "this did not work".
UNKNOWN = '?'


def _safe(label, fn):
    """Run one aggregation. A failure costs that figure, not the report."""
    try:
        value = fn()
        return UNKNOWN if value is None else value
    except Exception as exc:
        log.warning('daily_report: %s failed: %s: %s', label, type(exc).__name__, exc)
        return UNKNOWN


def _money(value) -> str:
    try:
        return f'{Decimal(str(value or 0)):,.2f}'
    except Exception:
        return str(value)


def collect(day: date | None = None) -> dict:
    """The fourteen figures the daily_report template expects."""
    from models import db, Customer, CustomerPlan, Invoice, Payment, ServiceRequest
    from sqlalchemy import func

    day = day or date.today()
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)

    OPEN_STATES = ('open', 'in_progress')
    DONE_STATES = ('resolved', 'closed')

    def new_complaints():
        return (ServiceRequest.query
                .filter(ServiceRequest.created_at >= start,
                        ServiceRequest.created_at < end).count())

    def open_complaints():
        return ServiceRequest.query.filter(
            ServiceRequest.status.in_(OPEN_STATES)).count()

    def old_complaints():
        # Still open, and not raised today -- the backlog, which is the number
        # worth reacting to.
        return (ServiceRequest.query
                .filter(ServiceRequest.status.in_(OPEN_STATES),
                        ServiceRequest.created_at < start).count())

    def closed_complaints():
        return (ServiceRequest.query
                .filter(ServiceRequest.status.in_(DONE_STATES),
                        ServiceRequest.resolved_at >= start,
                        ServiceRequest.resolved_at < end).count())

    def new_connections():
        col = getattr(Customer, 'created_at', None)
        if col is None:
            return UNKNOWN
        return Customer.query.filter(col >= start, col < end).count()

    def expiring_count():
        return (CustomerPlan.query
                .filter(CustomerPlan.status == 'active',
                        CustomerPlan.end_date > day,
                        CustomerPlan.end_date <= day + timedelta(days=EXPIRING_WINDOW_DAYS))
                .count())

    def expired_count():
        return CustomerPlan.query.filter(CustomerPlan.end_date == day).count()

    def renewed_count():
        # A renewal starts a plan period today.
        return CustomerPlan.query.filter(CustomerPlan.start_date == day).count()

    def total_collected():
        total = (db.session.query(func.coalesce(func.sum(Payment.amount), 0))
                 .filter(Payment.payment_date == day,
                         Payment.status == 'approved').scalar())
        return _money(total)

    def collection_details():
        """Today's takings split by payment mode — the useful half of the
        number above, because 'cash' and 'gateway' need different follow-up."""
        rows = (db.session.query(Payment.payment_mode,
                                 func.coalesce(func.sum(Payment.amount), 0),
                                 func.count(Payment.id))
                .filter(Payment.payment_date == day,
                        Payment.status == 'approved')
                .group_by(Payment.payment_mode).all())
        if not rows:
            return 'No collections today'
        return '\n'.join(
            f'{(mode or "other").title()}: ₹{_money(amount)} ({count})'
            for mode, amount, count in rows)

    def today_outstanding():
        """Owed on invoices that came due today."""
        from services.outstanding import _paid_per_invoice, _balance_expression
        paid = _paid_per_invoice()
        balance = _balance_expression(paid)
        total = (db.session.query(func.coalesce(func.sum(balance), 0))
                 .select_from(Invoice)
                 .outerjoin(paid, paid.c.invoice_id == Invoice.id)
                 .filter(Invoice.due_date == day,
                         Invoice.status.notin_(('paid', 'cancelled')))
                 .scalar())
        return _money(total)

    def total_outstanding():
        from services.outstanding import total_outstanding as _total
        return _money(_total())

    def new_leads():
        # There is no Lead model in this schema. If leads start being tracked,
        # return the count here and the report picks it up with no other change.
        return UNKNOWN

    return {
        'today':             day.strftime('%d %b %Y'),
        'new_complaints':    _safe('new_complaints', new_complaints),
        'open_complaints':   _safe('open_complaints', open_complaints),
        'old_complaints':    _safe('old_complaints', old_complaints),
        'closed_complaints': _safe('closed_complaints', closed_complaints),
        'new_leads':         _safe('new_leads', new_leads),
        'new_connections':   _safe('new_connections', new_connections),
        'expiring_count':    _safe('expiring_count', expiring_count),
        'expired_count':     _safe('expired_count', expired_count),
        'renewed_count':     _safe('renewed_count', renewed_count),
        'today_outstanding': _safe('today_outstanding', today_outstanding),
        'total_outstanding': _safe('total_outstanding', total_outstanding),
        'collection_details': _safe('collection_details', collection_details),
        'total_collected':   _safe('total_collected', total_collected),
    }


RULES = {
    'new_complaints':    'service_requests created today',
    'open_complaints':   "status in ('open','in_progress'), any age",
    'old_complaints':    'still open and raised before today (the backlog)',
    'closed_complaints': 'resolved_at falls today',
    'new_leads':         'NO LEAD MODEL IN SCHEMA -- always ?',
    'new_connections':   'customers created today',
    'expiring_count':    f'active plans ending within {EXPIRING_WINDOW_DAYS} days',
    'expired_count':     'plans whose end_date is today',
    'renewed_count':     'plans whose start_date is today',
    'today_outstanding': 'unpaid balance on invoices due today',
    'total_outstanding': 'services.outstanding.total_outstanding()',
    'collection_details': "today's approved payments grouped by mode",
    'total_collected':   "sum of today's approved payments",
}


def recipients() -> list[str]:
    """Who gets it. Settings key first, company phone as a fallback."""
    from services.messaging import _setting
    raw = (_setting('daily_report_recipients') or '').strip()
    if not raw:
        raw = (_setting('company_phone') or '').strip()
    return [n.strip() for n in raw.replace(';', ',').split(',') if n.strip()]


def preview(day: date | None = None) -> dict:
    """Print the figures and the rendered message. Sends nothing."""
    from app import app
    with app.app_context():
        ctx = collect(day)
        width = max(len(k) for k in ctx)
        print(f"\n  Daily report for {ctx['today']}\n  " + '-' * 68)
        for key, value in ctx.items():
            if key in ('today', 'collection_details'):
                continue
            rule = RULES.get(key, '')
            print(f'  {key:<{width}}  {str(value):>12}   {rule}')
        print('  ' + '-' * 68)
        print('  collection_details:')
        for line in str(ctx['collection_details']).splitlines():
            print(f'    {line}')

        if UNKNOWN in [str(v) for v in ctx.values()]:
            print(f"\n  '{UNKNOWN}' means that figure could not be computed -- "
                  f"check the log line for the rule that failed.")

        from services.messaging import render_template_type
        body = render_template_type('daily_report', ctx)
        print('\n  Rendered message\n  ' + '-' * 68)
        for line in (body or '(template inactive or missing)').splitlines():
            print(f'  {line}')
        print(f'\n  Would send to: {recipients() or "(nobody -- set daily_report_recipients)"}\n')
        return ctx


def send(day: date | None = None) -> list:
    """Render and deliver to every admin number."""
    from services.messaging import render_template_type, send_whatsapp

    ctx = collect(day)
    body = render_template_type('daily_report', ctx)
    if not (body or '').strip():
        log.warning("daily_report: template inactive or missing; nothing sent")
        return []

    targets = recipients()
    if not targets:
        log.warning('daily_report: no recipients configured '
                    '(Settings -> daily_report_recipients)')
        return []

    results = []
    for phone in targets:
        results.append(send_whatsapp(phone, body, template_type='daily_report'))
    log.info('daily_report: sent to %s recipient(s)', len(results))
    return results


def run():
    """Scheduler entry point."""
    from app import app
    with app.app_context():
        try:
            send()
        except Exception:
            log.exception('daily_report job failed')
