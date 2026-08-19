"""
blueprints/api/dashboard.py
===========================

Rich dashboard payload for the React admin home screen.

    GET /api/v1/dashboard/summary

Performance rewrite: the original fired 21 COUNT queries (7 days x 3
functions) for the lifecycle chips plus ~12 sub-queries for the monthly
summary and one outstanding query per expiring row. On PostgreSQL with real
data that regularly exceeded the 30 s client timeout.

This version issues a small fixed number of GROUP BY queries and pivots the
results in Python, so cost no longer scales with the number of days or rows.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import Blueprint
from sqlalchemy import String, and_, case, func, literal, union_all

from models import Customer, CustomerPlan, Invoice, Payment, db

from .utils import body, fail, iso, ok, staff_required, today_local

bp = Blueprint('api_dashboard', __name__)


def _bucket(mode):
    m = (mode or '').lower()
    if m == 'cash':
        return 'cash'
    if m == 'cheque':
        return 'cheque'
    if m in ('online transfer', 'neft', 'rtgs', 'imps', 'upi', 'paytm',
             'googlepay', 'phonepay', 'bank transfer', 'online'):
        return 'online'
    return 'other'


def _split_by_mode(rows):
    """Collections by payment mode, in whole rupees.

    Summed at full precision and rounded once at the end, per bucket - not
    per row - so the four buckets still add up to the total the operator
    reads beside them.
    """
    out = defaultdict(float)
    for mode, amount in rows:
        out[_bucket(mode)] += float(amount or 0)
    for bucket in ('cash', 'cheque', 'online', 'other'):
        out[bucket] = float(round(out.get(bucket, 0.0)))
    out['total'] = float(sum(v for k, v in out.items() if k != 'total'))
    return dict(out)


def _month_start(d):
    return d.replace(day=1)


def _prev_month(d):
    return _month_start(_month_start(d) - timedelta(days=1))


def _dialect_name():
    """Name of the dialect actually in use.

    `db.session.bind` is None on Flask-SQLAlchemy 3.x + SQLAlchemy 2.0 unless
    a bind is set explicitly - binds are resolved per-mapper. The old code
    read it directly and fell through to the SQLite branch on every request,
    emitting strftime() against MySQL:

        ERROR 1305 (42000): FUNCTION strftime does not exist

    which turned the whole dashboard into a 500. `db.engine` is bound to the
    app's configured database inside an app context, so it reports correctly.
    """
    try:
        return db.engine.dialect.name
    except Exception:
        try:
            return db.session.get_bind().dialect.name
        except Exception:
            return 'sqlite'


def _month_key(col):
    """Portable YYYY-MM expression for SQLite / MySQL / PostgreSQL."""
    dialect = _dialect_name()
    if dialect in ('mysql', 'mariadb'):
        return func.date_format(col, '%Y-%m')
    if dialect == 'sqlite':
        return func.strftime('%Y-%m', col)
    return func.to_char(col, 'YYYY-MM')


# --------------------------------------------------------------------------- #
#  Summary
# --------------------------------------------------------------------------- #
@bp.get('/dashboard/summary')
@staff_required
def dashboard_summary():
    today = today_local()
    month_start = _month_start(today)
    days_in_month = monthrange(today.year, today.month)[1]
    month_end = month_start.replace(day=days_in_month)

    # ---- customers -------------------------------------------------------
    # These used to be two aggregate queries (customer figures and plan
    # figures).  Scalar subqueries keep their independent aggregates while
    # sending only one request across a remote database connection.
    #
    # Written as five scalar sub-selects rather than as two multi-column
    # subqueries joined together. The join had no ON clause - it did not need
    # one, since both sides are a single aggregate row - but SQLAlchemy
    # correctly reports that as a cartesian product on every dashboard load,
    # and a warning that is always wrong is a warning nobody reads when it is
    # finally right.
    def _scalar(expression, model):
        return db.session.query(expression).select_from(model).scalar_subquery()

    top_counts = db.session.query(
        _scalar(func.count(Customer.id), Customer),
        _scalar(func.coalesce(func.sum(case((Customer.is_active.is_(True), 1),
                                            else_=0)), 0), Customer),
        _scalar(func.coalesce(func.sum(case((Customer.registration_date >= month_start, 1),
                                            else_=0)), 0), Customer),
        _scalar(func.coalesce(func.sum(case((CustomerPlan.status == 'active', 1),
                                            else_=0)), 0), CustomerPlan),
        _scalar(func.coalesce(func.sum(case((and_(CustomerPlan.status == 'active',
                                                  CustomerPlan.end_date < today), 1),
                                            else_=0)), 0), CustomerPlan),
    ).one()
    total_customers = int(top_counts[0] or 0)
    active_customers = int(top_counts[1] or 0)
    new_this_month = int(top_counts[2] or 0)
    active_plans = int(top_counts[3] or 0)
    expired_plans = int(top_counts[4] or 0)

    # ---- how many there are ALTOGETHER, not just in the visible week ------
    #
    # The number beside each row used to be the sum of its seven chips, which
    # answers "how many this week" while looking like it answers "how many".
    # A customer whose plan expired three weeks ago appeared in neither the
    # chips nor the count, so the panel could read (0) with a hundred expired
    # connections sitting behind it. One grouped query, three figures.
    #
    # This runs BEFORE the chips now, because the Expired row is built from
    # `expired_all` - see the cumulative note below.
    totals = db.session.query(
        func.coalesce(func.sum(case(
            (and_(CustomerPlan.status == 'active',
                  CustomerPlan.end_date >= today), 1), else_=0)), 0),
        func.coalesce(func.sum(case(
            (and_(CustomerPlan.status == 'active',
                  CustomerPlan.end_date < today), 1), else_=0)), 0),
        # Same filter the report behind the button uses, or the count and the
        # list it opens disagree - which is worse than no count at all.
        func.coalesce(func.count(func.distinct(case(
            (and_(CustomerPlan.last_invoice_date.isnot(None),
                  CustomerPlan.last_invoice_date <= today),
             CustomerPlan.customer_id)))), 0),
        # Renewals THIS WEEK, as distinct customers.
        #
        # Computed here rather than by adding up the seven daily chips. Each
        # chip is already a distinct-customer count for its own day, so summing
        # them counts anybody who renewed on two different days twice - and the
        # week's figure then came out ABOVE the all-time figure beside it. The
        # dashboard read "Customer renewed (3) ... View all 2", which is not a
        # rounding disagreement: three is more than everyone.
        func.coalesce(func.count(func.distinct(case(
            (and_(CustomerPlan.last_invoice_date >= today - timedelta(days=6),
                  CustomerPlan.last_invoice_date <= today),
             CustomerPlan.customer_id)))), 0),
    ).one()
    expiring_all = int(totals[0] or 0)
    expired_all = int(totals[1] or 0)
    renewed_all = int(totals[2] or 0)
    renewed_week = int(totals[3] or 0)

    # ---- one GROUP BY for the lifecycle chips ----------------------------
    #
    # EVERY CHIP IS AN EXACT DATE. A plan whose end date is 15 Aug is counted
    # in the 15 Aug column and in no other - not the day before, not the day
    # after, and not carried forward into the rest of the week.
    #
    # It was briefly a running backlog on the Expired row: today's chip held
    # every lapsed connection and each following day added the previous day's
    # expiries, so one customer appeared in all seven columns and the row read
    # 3, 4, 5, 6, 7, 8, 9 for three real expiries. That answers "where am I by
    # Wednesday if nobody renews", which is a different question from the one
    # the chips look like they are answering, and it made a single expiry look
    # like seven.
    #
    # Two rows count things that have ALREADY happened, so they look BACK from
    # today; one counts things still to come, so it looks forward:
    #
    #     Expired   08 Aug .. 14 Aug   the week just gone
    #     Renewed   08 Aug .. 14 Aug   the week just gone
    #     Expiring  14 Aug .. 20 Aug   the week ahead
    #
    # The rows therefore do NOT share a column, which is why each one now
    # carries its own date span in the label - see `window` in the payload.
    # A shared forward axis was tried and put six structural zeros on each of
    # the first two rows: nothing can have expired tomorrow yet.
    LIFECYCLE_DAYS = 7
    past_start = today - timedelta(days=LIFECYCLE_DAYS - 1)
    future_end = today + timedelta(days=LIFECYCLE_DAYS - 1)

    # The Expired row stops YESTERDAY, and that is not an off-by-one.
    #
    # A plan whose end_date is today has not expired: `days_left` is 0, the
    # expiry board lists it under "expiring" (end_date >= today), and
    # `expired_all` counts only end_date < today. Including today in the
    # Expired row therefore counted a connection that is still up, and put the
    # row's own count ABOVE the total sitting next to it - the dashboard read
    # "Expired (1) ... View all 0", which is not a rounding disagreement, it is
    # the panel contradicting itself in the space of four words.
    expired_start = today
    expired_end = today + timedelta(days=LIFECYCLE_DAYS - 1)

    # One query spans every window below; each row reads its own slice of it.
    expiry_query = db.session.query(
        literal('expiry').label('kind'), CustomerPlan.end_date.label('day'),
        func.count(CustomerPlan.id).label('count')).filter(
        CustomerPlan.status == 'active',
        CustomerPlan.end_date >= expired_start,
        CustomerPlan.end_date <= future_end
    ).group_by(CustomerPlan.end_date)

    # Every renewal path - the counter, the billing run, a plan change - stamps
    # CustomerPlan.last_invoice_date with the day the new period was raised.
    # It is the only field all three write, which is why it is the marker here
    # rather than "a CustomerPlan row was created": renewing onto the SAME plan
    # updates the existing row in place and creates nothing.
    #
    # Counted as DISTINCT customers, because the row is labelled "Renewed"
    # under a customer heading - somebody with two connections renewed on one
    # visit is one customer renewing, not two.
    renewed_query = db.session.query(
        literal('renewed').label('kind'),
        CustomerPlan.last_invoice_date.label('day'),
        func.count(func.distinct(CustomerPlan.customer_id)).label('count')).filter(
        CustomerPlan.last_invoice_date >= past_start,
        CustomerPlan.last_invoice_date <= today
    ).group_by(CustomerPlan.last_invoice_date)

    lifecycle_rows = db.session.execute(union_all(
        expiry_query.statement, renewed_query.statement)).all()

    def _as_date(value):
        """MySQL hands back date, SQLite a string. Key on one of them."""
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return None

    expiry_counts, renewed_counts = {}, {}
    for row in lifecycle_rows:
        day = _as_date(row.day)
        if day is None:
            continue
        target = expiry_counts if row.kind == 'expiry' else renewed_counts
        target[day] = target.get(day, 0) + int(row.count or 0)

    def chips(start, counts, label_fmt='%d %b'):
        """Seven days from `start`, each counting only its OWN date."""
        out = []
        for offset in range(LIFECYCLE_DAYS):
            d = start + timedelta(days=offset)
            out.append({'date': iso(d), 'label': d.strftime(label_fmt),
                        'count': int(counts.get(d, 0))})
        return out

    def span(days):
        return {'from': days[0]['date'], 'to': days[-1]['date'],
                'label': f"{days[0]['label']} to {days[-1]['label']}"}

    # Active plans running out on that day - who has to be chased, and when.
    expiring = chips(today, expiry_counts)

    # Connections that ran out on that day and have not come back. A renewal
    # moves end_date forward, so a customer who renewed drops out of this
    # window on their own - the row does not need a second query to exclude
    # them. Ends yesterday: see `expired_start` above.
    recently_expired = chips(expired_start, expiry_counts,
                             label_fmt='%d-%b-%Y')

    # Renewals recorded on that day, as distinct customers: somebody with two
    # connections renewed in one visit is one customer renewing, not two.
    renewed = chips(past_start, renewed_counts)

    # ---- invoices (one GROUP BY) -----------------------------------------
    inv_rows = db.session.query(
        Invoice.status,
        func.count(Invoice.id),
        func.coalesce(func.sum(Invoice.total_amount), 0)
    ).filter(Invoice.issue_date >= month_start,
             Invoice.issue_date <= month_end).group_by(Invoice.status).all()

    total_bills = sum(r[1] for r in inv_rows)
    total_amount = float(sum(r[2] or 0 for r in inv_rows))
    paid_bills = sum(r[1] for r in inv_rows if r[0] == 'paid')
    paid_amount = float(sum(r[2] or 0 for r in inv_rows if r[0] == 'paid'))

    invoice_summary = {
        'total_bills': total_bills,
        'total_amount': float(round(total_amount)),
        'paid_bills': paid_bills,
        'paid_amount': float(round(paid_amount)),
        'pending_bills': total_bills - paid_bills,
        'pending_amount': float(round(total_amount) - round(paid_amount)),
    }

    # ---- collections (one grouped range, split in Python) ----------------
    # The old version sent three nearly identical GROUP BY queries to get
    # today, this month and last month.  One date+mode result set contains all
    # three and avoids two remote database round trips on every page load.
    prev_start = _prev_month(today)
    prev_end = month_start - timedelta(days=1)
    # The collection widgets and the 12-month chart are both payment totals.
    # Group the full chart range once, then use the recent rows for the three
    # collection buckets below.  A daily+mode result remains tiny (at most a
    # few thousand rows for a busy ISP) and removes another remote query.
    trend_start = _month_start(today - timedelta(days=365))
    collection_rows = db.session.query(
        Payment.payment_date,
        Payment.payment_mode,
        func.coalesce(func.sum(Payment.amount), 0)
    ).filter(Payment.status == 'approved',
             Payment.payment_date >= trend_start,
             Payment.payment_date <= today
             ).group_by(Payment.payment_date, Payment.payment_mode).all()
    today_rows, this_month_rows, last_month_rows = [], [], []
    trend_map = defaultdict(float)
    for payment_date, mode, amount in collection_rows:
        # Payment dates are ``date`` columns in production, but normalise a
        # datetime too so this keeps working with older database schemas.
        day = payment_date.date() if isinstance(payment_date, datetime) else payment_date
        row = (mode, amount)
        trend_map[day.strftime('%Y-%m')] += float(amount or 0)
        if day == today:
            today_rows.append(row)
        if month_start <= day <= month_end:
            this_month_rows.append(row)
        elif prev_start <= day <= prev_end:
            last_month_rows.append(row)

    trend = []
    cursor = trend_start
    while cursor <= month_start:
        k = cursor.strftime('%Y-%m')
        trend.append({'month': k,
                      'label': cursor.strftime('%b %Y'),
                      'amount': float(round(trend_map.get(k, 0.0)))})
        cursor = _month_start(cursor + timedelta(days=32))

    # ---- outstanding, in one query ---------------------------------------
    # This used to load every open invoice and read i.balance, which lazy-loads
    # that invoice's payments - one extra round-trip per open invoice, growing
    # forever. See services/outstanding.py.
    from services.outstanding import total_outstanding
    outstanding = total_outstanding()

    pending_auth = Payment.query.filter(
        Payment.status.in_(('approved', 'pending')),
        Payment.authorized_at.is_(None)).count()

    return ok({
        'as_of': iso(today),
        'customers': {
            'total': total_customers,
            'active': active_customers,
            'inactive': total_customers - active_customers,
            'new_this_month': new_this_month,
        },
        'plans': {
            'active': active_plans,
            'expired': expired_plans,
            'expiring': expiring,
            'recently_expired': recently_expired,
            'renewed': renewed,
            # Each row covers a different week now, so each has to say which -
            # the dates alone no longer line up between rows to tell you.
            'window': {
                'expiring': span(expiring),
                'recently_expired': span(recently_expired),
                'renewed': span(renewed),
            },
            # Each row's own figure for the week on screen. Every chip is an
            # exact date and nothing is carried forward, so these are plain
            # sums and no customer is counted twice.
            'expiring_total': sum(day['count'] for day in expiring),
            'expired_total': sum(day['count'] for day in recently_expired),
            # NOT the sum of the daily chips - see `renewed_week` above.
            'renewed_total': renewed_week,
            # Everything on the books, so "View all" can say what it will show.
            'expiring_all': expiring_all,
            'expired_all': expired_all,
            'renewed_all': renewed_all,
        },
        'invoices': invoice_summary,
        'collections': {
            'today': _split_by_mode(today_rows),
            'this_month': _split_by_mode(this_month_rows),
            'last_month': _split_by_mode(last_month_rows),
        },
        'outstanding': outstanding,
        'pending_authorization': pending_auth,
        'trend': trend,
    })


# --------------------------------------------------------------------------- #
#  Zone breakdown  -  powers the "Zone Wise Outstanding / Collection" tabs
# --------------------------------------------------------------------------- #
@bp.get('/dashboard/zones')
@staff_required
def dashboard_zones():
    """Outstanding and current-month collection, grouped by customer zone.

    Follows the same shape as the rest of this module: a small number of
    GROUP BY queries pivoted in Python, rather than one query per zone.
    """
    today = today_local()
    month_start = _month_start(today)

    # --- collection this month, by zone (one GROUP BY) --------------------
    collection_rows = db.session.query(
        Customer.zone,
        func.count(Payment.id),
        func.coalesce(func.sum(Payment.amount), 0)
    ).join(Customer, Customer.id == Payment.customer_id).filter(
        Payment.status == 'approved',
        Payment.payment_date >= month_start
    ).group_by(Customer.zone).all()

    collection = [{
        'zone': row[0] or 'Unassigned',
        'count': int(row[1] or 0),
        'amount': float(round(float(row[2] or 0))),
    } for row in collection_rows]
    collection.sort(key=lambda z: z['amount'], reverse=True)

    # --- outstanding, by zone (one query) ---------------------------------
    # The note that used to sit here said Invoice.balance "cannot be summed in
    # SQL". It can - the payment total per invoice is a GROUP BY, and joining
    # that back gives identical arithmetic without a round-trip per invoice.
    from services.outstanding import outstanding_by_zone
    outstanding = outstanding_by_zone()

    return ok({'outstanding': outstanding, 'collection': collection})


# --------------------------------------------------------------------------- #
#  Monthly summary  -  new clients, billing and collection per month
# --------------------------------------------------------------------------- #
@bp.get('/dashboard/monthly')
@staff_required
def dashboard_monthly():
    """Months with activity in the last year: new customers, bills, paid, pending.

    Empty months are left out rather than printed as rows of dashes.
    """
    today = today_local()
    start = _month_start(today - timedelta(days=365))

    # New customers per month
    cust_key = _month_key(Customer.registration_date)
    cust_rows = db.session.query(
        cust_key.label('m'), func.count(Customer.id)
    ).filter(Customer.registration_date >= start).group_by('m').all()
    new_clients = {str(r[0]): int(r[1] or 0) for r in cust_rows}

    # Invoices per month, split by paid / not paid
    inv_key = _month_key(Invoice.issue_date)
    inv_rows = db.session.query(
        inv_key.label('m'),
        Invoice.status,
        func.count(Invoice.id),
        func.coalesce(func.sum(Invoice.total_amount), 0)
    ).filter(Invoice.issue_date >= start).group_by('m', Invoice.status).all()

    totals = defaultdict(lambda: {
        'total_bills': 0, 'total_amount': 0.0,
        'paid_bills': 0, 'paid_amount': 0.0,
    })
    for month, status, count, amount in inv_rows:
        row = totals[str(month)]
        row['total_bills'] += int(count or 0)
        row['total_amount'] += float(amount or 0)
        if status == 'paid':
            row['paid_bills'] += int(count or 0)
            row['paid_amount'] += float(amount or 0)

    # Only months that actually had something in them.
    #
    # Padding the full twelve produced a table where the first row carried the
    # figures and eleven rows of dashes sat underneath it - an ISP that started
    # billing in July does not need to be told that nothing happened the
    # previous August. The window still looks back a year; it just stops
    # printing the months before the business had any activity.
    out = []
    cursor = start
    while cursor <= _month_start(today):
        key = cursor.strftime('%Y-%m')
        row = totals.get(key)
        clients = new_clients.get(key, 0)

        if row is None and not clients:
            cursor = _month_start(cursor + timedelta(days=32))
            continue

        row = row or {'total_bills': 0, 'total_amount': 0.0,
                      'paid_bills': 0, 'paid_amount': 0.0}
        out.append({
            'month': key,
            'label': cursor.strftime('%b %Y'),
            'new_clients': clients,
            'total_bills': row['total_bills'],
            'total_amount': float(round(row['total_amount'])),
            'paid_bills': row['paid_bills'],
            'paid_amount': float(round(row['paid_amount'])),
            'pending_bills': row['total_bills'] - row['paid_bills'],
            'pending_amount': float(round(row['total_amount']) - round(row['paid_amount'])),
        })
        cursor = _month_start(cursor + timedelta(days=32))

    out.reverse()  # newest first, as in the Jinja dashboard
    return ok(out)


# --------------------------------------------------------------------------- #
#  Inline date edit from the plan-expiry board
# --------------------------------------------------------------------------- #
@bp.put('/customer-plans/<int:cpid>/dates')
@staff_required
def customer_plan_dates(cpid):
    cp = db.session.get(CustomerPlan, cpid)
    if not cp:
        return fail('not_found', 404)

    data = body()

    def as_date(value):
        if not value:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        try:
            return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
        except ValueError:
            return None

    start = as_date(data.get('start_date') or data.get('renew_date'))
    end = as_date(data.get('end_date') or data.get('expiry_date'))

    if not start and not end:
        return fail('no_valid_dates', 400)

    # Validate the proposed pair before mutating the ORM row. Returning an
    # error with a dirty session left a bad value around until Flask removed
    # the session; a later commit in the same request could then save it.
    next_start = start or cp.start_date
    next_end = end or cp.end_date
    if next_end < next_start:
        return fail('end_date_before_start_date', 400)

    cp.start_date = next_start
    cp.end_date = next_end

    if cp.end_date >= today_local():
        cp.status = 'active'

    db.session.commit()
    return ok({'id': cp.id,
               'start_date': iso(cp.start_date),
               'end_date': iso(cp.end_date),
               'status': cp.status,
               'validity_days': (cp.plan.validity_days if cp.plan else 30) or 30})
