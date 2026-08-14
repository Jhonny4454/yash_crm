"""
blueprints/api/billing_run.py
=============================

The monthly billing run - Customers > Generate Invoice.

This is the highest-consequence screen in the system: it writes money owed
across hundreds of accounts in one press. So the design is deliberately
cautious in three ways.

**It shows before it writes.** The preview endpoint returns exactly what would
be billed, per customer, including the reason a customer is being skipped. The
operator commits to a list they have seen, not to a filter they hope is right.

**It refuses to double-bill.** A customer who already has an invoice covering
the period is excluded, and the exclusion survives a re-run - so pressing
Generate twice, or two operators pressing it at once, cannot produce two bills
for one month. This is the failure that costs an ISP its customers' trust, and
a confirmation dialog is not a defence against it.

**It reports per customer.** The response says what happened to every id it was
given. "247 invoices generated" with no detail is unauditable when the operator
selected 250.
"""
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, request
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from models import Customer, CustomerPlan, Invoice, Plan, db

from .utils import (admin_required, body, fail, iso, money, ok, paginate,
                    staff_required)

bp = Blueprint('api_billing_run', __name__)

#: How far past its end date a plan can be and still be picked up by a run.
#: Beyond this the account is a collections problem, not a billing one, and
#: sweeping it into a routine run buries it.
STALE_AFTER_DAYS = 90


def _setting(key, default=''):
    try:
        from models_ext import Setting
        value = Setting.get(key)
        if value is not None and str(value).strip() != '':
            return value
    except Exception:
        pass
    return default


def _period_for(plan_row, issue_date):
    """The service window an invoice raised today would cover.

    Billing runs forward from where the customer's cover currently ends, not
    from today - otherwise a run done three days late silently gifts the
    customer three days.
    """
    start = plan_row.end_date or issue_date
    if start < issue_date - timedelta(days=STALE_AFTER_DAYS):
        start = issue_date

    days = 30
    plan = plan_row.plan
    if plan is not None and getattr(plan, 'validity_days', None):
        days = int(plan.validity_days) or 30

    return start, start + timedelta(days=days)


def _price_for(plan_row):
    """What to charge, honouring the Customer-vs-Master price setting."""
    source = str(_setting('invoice_package_price', 'Customer')).lower()

    customer_price = getattr(plan_row, 'price', None)
    master_price = getattr(plan_row.plan, 'price_monthly', None) if plan_row.plan else None

    if source.startswith('master'):
        chosen = master_price if master_price is not None else customer_price
    else:
        chosen = customer_price if customer_price is not None else master_price

    return Decimal(str(chosen or 0))


def _discount_for(customer, base):
    """The customer's standing discount, as an amount off this bill."""
    percent = Decimal(str(getattr(customer, 'discount_percent', 0) or 0))
    amount = Decimal(str(getattr(customer, 'discount_amount', 0) or 0))

    if percent > 0:
        return (base * percent / Decimal('100')).quantize(Decimal('0.01'))
    if amount > 0:
        return min(amount, base)
    return Decimal('0.00')


def _existing_invoice(customer_id, period_start, period_end, today):
    """An invoice that already covers this customer, if there is one.

    Two separate guards, because one is not enough:

    1. *Overlap* - an invoice whose service window overlaps the one about to
       be raised. Matched on the period rather than the issue date, so a run
       done three days late still recognises the month it was for.

    2. *Already covered ahead* - any live invoice whose period has not run out
       yet. This is the guard that matters. Overlap alone is defeated by
       anything that moves the plan's end date forward between two runs: the
       next period computed is then merely *adjacent* to the last one, not
       overlapping, and the customer is billed twice in a sitting. Stated in
       words the rule is simply "you are already billed for cover that has not
       expired", which is also how an operator would explain it.
    """
    overlapping = (Invoice.query
                   .filter(Invoice.customer_id == customer_id,
                           Invoice.status != 'cancelled',
                           Invoice.period_start.isnot(None),
                           Invoice.period_start < period_end,
                           Invoice.period_end > period_start)
                   .first())
    if overlapping is not None:
        return overlapping

    return (Invoice.query
            .filter(Invoice.customer_id == customer_id,
                    Invoice.status != 'cancelled',
                    Invoice.period_end.isnot(None),
                    Invoice.period_end > today)
            .order_by(Invoice.period_end.desc())
            .first())


def _fallback_existing(customer_id, issue_date):
    """Older invoices predate the period columns; fall back to the month."""
    month_start = issue_date.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    return (Invoice.query
            .filter(Invoice.customer_id == customer_id,
                    Invoice.status != 'cancelled',
                    Invoice.period_start.is_(None),
                    Invoice.issue_date >= month_start,
                    Invoice.issue_date < next_month)
            .first())


def _candidate_query():
    """Active plans, with their customer and plan already loaded."""
    return (CustomerPlan.query
            .options(joinedload(CustomerPlan.plan))
            .join(Customer, Customer.id == CustomerPlan.customer_id)
            .filter(CustomerPlan.status == 'active'))


def _apply_filters(query):
    args = request.args

    for field, column in (('zone', Customer.zone), ('area', Customer.area),
                          ('building', Customer.building),
                          ('locality', Customer.locality)):
        value = (args.get(field) or '').strip()
        if value:
            query = query.filter(column == value)

    plan_id = (args.get('plan_id') or '').strip()
    if plan_id.isdigit():
        query = query.filter(CustomerPlan.plan_id == int(plan_id))

    # The window is on the plan's END date: "who runs out between these dates".
    d_from = (args.get('from') or '').strip()
    d_to = (args.get('to') or '').strip()
    if d_from:
        query = query.filter(CustomerPlan.end_date >= d_from)
    if d_to:
        query = query.filter(CustomerPlan.end_date <= d_to)

    # Off by default. Filtering disabled customers out of the query made them
    # vanish from the list entirely, so an operator billing a zone of 40 saw 38
    # rows and no explanation. They are listed instead, greyed out, carrying
    # "The connection is disabled" - which is a fact worth seeing on a billing
    # screen, since a disabled line may be one nobody meant to disable.
    if (args.get('active_only') or '0') in ('1', 'true', 'yes'):
        query = query.filter(or_(Customer.is_active.is_(True),
                                 Customer.is_active.is_(None)))

    q = (args.get('q') or '').strip()
    if q:
        like = f'%{q}%'
        query = query.filter(or_(Customer.first_name.ilike(like),
                                 Customer.last_name.ilike(like),
                                 Customer.username.ilike(like),
                                 Customer.mobile.ilike(like)))
    return query


def _assess(plan_row, issue_date, due_days):
    """Work out what this customer would be billed, or why they would not be."""
    customer = db.session.get(Customer, plan_row.customer_id)
    if customer is None:
        return None

    period_start, period_end = _period_for(plan_row, issue_date)
    base = _price_for(plan_row)
    discount = _discount_for(customer, base)

    blocked = None
    existing = _existing_invoice(customer.id, period_start, period_end,
                                 issue_date) \
        or _fallback_existing(customer.id, issue_date)
    if existing is not None:
        covered = existing.period_end
        blocked = (f'Already invoiced ({existing.invoice_no}'
                   + (f', covered to {covered:%d-%m-%Y}' if covered else '')
                   + ').')
    elif base <= 0:
        blocked = 'No price is set on this plan.'
    elif getattr(customer, 'is_active', True) is False:
        blocked = 'The connection is disabled.'

    return {
        'customer_id': customer.id,
        'customer_plan_id': plan_row.id,
        'name': customer.full_name,
        'username': customer.username or '',
        'mobile': customer.mobile or '',
        'zone': customer.zone or '',
        'area': customer.area or '',
        'building': customer.building or '',
        'plan_name': plan_row.plan.name if plan_row.plan else '',
        'current_expiry': iso(plan_row.end_date),
        'period_start': iso(period_start),
        'period_end': iso(period_end),
        'amount': money(base),
        'discount': money(discount),
        'net_amount': money(base - discount),
        'due_date': iso(issue_date + timedelta(days=due_days)),
        'billable': blocked is None,
        'blocked_reason': blocked or '',
        'existing_invoice_no': existing.invoice_no if existing else '',
    }


@bp.get('/billing/run/preview')
@staff_required
def billing_preview():
    """Who a run would bill, what each would be charged, and who is excluded."""
    issue_date = date.today()
    raw_issue = (request.args.get('issue_date') or '').strip()
    if raw_issue:
        try:
            issue_date = date.fromisoformat(raw_issue)
        except ValueError:
            return fail('invalid_issue_date', 400,
                        detail='Use YYYY-MM-DD for the invoice date.')

    try:
        due_days = int(request.args.get('due_days')
                       or _setting('invoice_due_days', 15) or 15)
    except (TypeError, ValueError):
        due_days = 15

    query = _apply_filters(_candidate_query()).order_by(
        CustomerPlan.end_date.asc(), CustomerPlan.customer_id.asc())

    rows, meta = paginate(query, default_per_page=100)

    entries = [e for e in
               (_assess(row, issue_date, due_days) for row in rows)
               if e is not None]

    billable = [e for e in entries if e['billable']]
    return ok(entries, meta=meta, totals={
        'listed': len(entries),
        'billable': len(billable),
        'blocked': len(entries) - len(billable),
        'amount': round(sum(e['net_amount'] for e in billable), 2),
        'issue_date': iso(issue_date),
        'due_days': due_days,
    })


@bp.get('/billing/run/filters')
@staff_required
def billing_filters():
    """Options for the run's filter bar, from customers who have active plans."""
    rows = _candidate_query().all()
    ids = {r.customer_id for r in rows}

    zones, areas, buildings, localities = set(), set(), set(), set()
    if ids:
        for customer in Customer.query.filter(Customer.id.in_(ids)).all():
            for value, bucket in ((customer.zone, zones), (customer.area, areas),
                                  (customer.building, buildings),
                                  (customer.locality, localities)):
                if value:
                    bucket.add(value)

    plan_ids = {r.plan_id for r in rows if r.plan_id}
    plans = Plan.query.filter(Plan.id.in_(plan_ids)).all() if plan_ids else []

    return ok({
        'zones': sorted(zones), 'areas': sorted(areas),
        'buildings': sorted(buildings), 'localities': sorted(localities),
        'plans': [{'id': p.id, 'name': p.name} for p in
                  sorted(plans, key=lambda p: p.name or '')],
        'active_plans': len(rows),
        'default_due_days': int(_setting('invoice_due_days', 15) or 15),
    })


@bp.post('/billing/run/generate')
@admin_required
def billing_generate():
    """
    Raise invoices for the selected customers.

    Every id is answered for. A customer who was billable at preview time but
    has since been invoiced by someone else is re-checked here and skipped -
    the preview is a proposal, not a reservation.
    """
    data = body()
    ids = data.get('customer_plan_ids') or []
    if not isinstance(ids, list) or not ids:
        return fail('selection_required', 400,
                    detail='Select at least one customer to invoice.')
    if len(ids) > 1000:
        return fail('too_many', 400,
                    detail='Generate at most 1000 invoices in one run.')

    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return fail('invalid_selection', 400)

    issue_date = date.today()
    raw_issue = (data.get('issue_date') or '').strip()
    if raw_issue:
        try:
            issue_date = date.fromisoformat(raw_issue)
        except ValueError:
            return fail('invalid_issue_date', 400)

    try:
        due_days = int(data.get('due_days')
                       or _setting('invoice_due_days', 15) or 15)
    except (TypeError, ValueError):
        due_days = 15

    notify = bool(data.get('send_message'))

    from .customer_billing import _next_invoice_no

    rows = {r.id: r for r in CustomerPlan.query.filter(
        CustomerPlan.id.in_(ids)).all()}

    created, skipped = [], []

    for plan_id in ids:
        plan_row = rows.get(plan_id)
        if plan_row is None:
            skipped.append({'customer_plan_id': plan_id,
                            'reason': 'That plan no longer exists.'})
            continue

        assessment = _assess(plan_row, issue_date, due_days)
        if assessment is None:
            skipped.append({'customer_plan_id': plan_id,
                            'reason': 'That customer no longer exists.'})
            continue
        if not assessment['billable']:
            skipped.append({'customer_plan_id': plan_id,
                            'customer_id': assessment['customer_id'],
                            'name': assessment['name'],
                            'reason': assessment['blocked_reason']})
            continue

        base = Decimal(str(assessment['amount']))
        discount = Decimal(str(assessment['discount']))
        period_start = date.fromisoformat(assessment['period_start'])
        period_end = date.fromisoformat(assessment['period_end'])

        invoice = Invoice(
            customer_id=assessment['customer_id'],
            customer_plan_id=plan_row.id,
            invoice_no=_next_invoice_no(),
            issue_date=issue_date,
            due_date=issue_date + timedelta(days=due_days),
            period_start=period_start,
            period_end=period_end,
            total_amount=base,
            tax_amount=Decimal('0.00'),
            discount_amount=discount,
            caption=assessment['plan_name'] or 'Monthly plan',
            status='sent',
        )
        db.session.add(invoice)

        # Each invoice is committed on its own. One bad row in a 500-customer
        # run must not roll back the 499 that were fine, and a half-finished
        # run that is safe to repeat is better than an all-or-nothing one that
        # is not.
        try:
            db.session.flush()
            # Record that a bill went out, but do NOT move end_date - raising
            # an invoice is not the same as being paid for it.
            plan_row.last_invoice_date = issue_date
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            skipped.append({'customer_plan_id': plan_id,
                            'customer_id': assessment['customer_id'],
                            'name': assessment['name'],
                            'reason': f'Could not be saved: {str(exc)[:120]}'})
            continue

        entry = {
            'customer_plan_id': plan_id,
            'customer_id': assessment['customer_id'],
            'name': assessment['name'],
            'invoice_id': invoice.id,
            'invoice_no': invoice.invoice_no,
            'net_amount': assessment['net_amount'],
            'period_start': assessment['period_start'],
            'period_end': assessment['period_end'],
            'message_status': '',
        }

        if notify:
            entry['message_status'] = _notify(assessment['customer_id'], invoice)

        created.append(entry)

    try:
        from app import log_audit
        log_audit('Billing Run',
                  f'Generated {len(created)} invoice(s) dated {issue_date}, '
                  f'{len(skipped)} skipped')
    except Exception:
        pass

    return ok({
        'created': created,
        'created_count': len(created),
        'skipped': skipped,
        'skipped_count': len(skipped),
        'total_amount': round(sum(e['net_amount'] for e in created), 2),
        'issue_date': iso(issue_date),
    })


def _notify(customer_id, invoice):
    """Send the bill notification, reporting the gateway's own verdict."""
    try:
        from app import send_template_message
        customer = db.session.get(Customer, customer_id)
        result = send_template_message(customer, 'bill', invoice=invoice)
        return getattr(result, 'status', 'unknown')
    except Exception as exc:
        return f'failed: {str(exc)[:80]}'
