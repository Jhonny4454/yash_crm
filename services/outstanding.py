"""
services/outstanding.py
=======================

"What is still owed", expressed in SQL.

Why this exists
---------------
``Invoice.balance`` is a Python property: total, minus the sum of its approved
payments, minus any discount.  Reading it triggers a lazy load of that
invoice's payments, so the obvious way to total up what a business is owed -

    open_invoices = Invoice.query.filter(Invoice.status.in_(OPEN)).all()
    outstanding = sum(i.balance for i in open_invoices if i.balance > 0)

- issues one query for the invoices and then **one more per invoice**.  The
dashboard did exactly that, twice (summary and zones), and the profiler showed
30 extra round-trips on a 30-invoice test database.  That is not a constant
cost: it grows with the number of open invoices, so the dashboard gets slower
every month the business grows, and it does it on the one screen everybody
opens first.  With the database on another host - Railway, in this deployment -
each of those round-trips is tens of milliseconds of pure network latency.

The comment in dashboard.py said balance "cannot be summed in SQL".  It can:
the payment total per invoice is a GROUP BY, and joining that back gives the
same arithmetic in one query.

The arithmetic lives here, once.  Two endpoints computing "outstanding" with
subtly different rules - one counting pending payments, the other not - is the
kind of discrepancy nobody notices until two screens disagree in front of a
customer.
"""
from sqlalchemy import func

from models import Customer, Invoice, Payment, db

#: Invoice statuses that can still owe money.
OPEN_STATUSES = ('draft', 'sent', 'overdue')

#: Only approved payments reduce a balance, matching Invoice.paid_amount.
COUNTED_PAYMENT_STATUS = 'approved'


def _paid_per_invoice():
    """Sub-select: invoice_id -> total approved payments."""
    return (
        db.session.query(
            Payment.invoice_id.label('invoice_id'),
            func.coalesce(func.sum(Payment.amount), 0).label('paid'),
        )
        .filter(Payment.status == COUNTED_PAYMENT_STATUS,
                Payment.invoice_id.isnot(None))
        .group_by(Payment.invoice_id)
        .subquery()
    )


def _balance_expression(paid):
    """The SQL equivalent of ``Invoice.balance``.

    Kept beside COUNTED_PAYMENT_STATUS deliberately: if the Python property
    ever changes which payments count, both halves have to move together or
    the dashboard silently disagrees with every invoice screen.
    """
    return (Invoice.total_amount
            - func.coalesce(paid.c.paid, 0)
            - func.coalesce(Invoice.discount_amount, 0))


def total_outstanding():
    """Everything still owed across all open invoices, as one query."""
    paid = _paid_per_invoice()
    balance = _balance_expression(paid)
    total = (
        db.session.query(func.coalesce(func.sum(balance), 0))
        .select_from(Invoice)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .filter(Invoice.status.in_(OPEN_STATUSES), balance > 0)
        .scalar()
    )
    return float(round(float(total or 0)))


def outstanding_by_zone():
    """``[{'zone', 'count', 'amount'}]``, biggest first, in one query.

    Zones come off the customer, so unassigned customers - and invoices whose
    customer row has gone - collapse into a single 'Unassigned' bucket rather
    than disappearing from the total.
    """
    paid = _paid_per_invoice()
    balance = _balance_expression(paid)

    rows = (
        db.session.query(
            Customer.zone,
            func.count(Invoice.id),
            func.coalesce(func.sum(balance), 0),
        )
        .select_from(Invoice)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .outerjoin(Customer, Customer.id == Invoice.customer_id)
        .filter(Invoice.status.in_(OPEN_STATUSES), balance > 0)
        .group_by(Customer.zone)
        .all()
    )

    buckets = {}
    for zone, count, amount in rows:
        # Two rows can collapse into one bucket: a NULL zone and an empty
        # string both mean "not set", and summing them separately would show
        # the same zone twice.
        label = (zone or '').strip() or 'Unassigned'
        bucket = buckets.setdefault(label, {'zone': label, 'count': 0, 'amount': 0.0})
        bucket['count'] += int(count or 0)
        bucket['amount'] += float(amount or 0)

    out = list(buckets.values())
    for bucket in out:
        bucket['amount'] = float(round(bucket['amount']))
    out.sort(key=lambda z: z['amount'], reverse=True)
    return out


def outstanding_for_customer(customer_id):
    """What one customer owes. Same arithmetic, one customer."""
    paid = _paid_per_invoice()
    balance = _balance_expression(paid)
    total = (
        db.session.query(func.coalesce(func.sum(balance), 0))
        .select_from(Invoice)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .filter(Invoice.customer_id == customer_id,
                Invoice.status.in_(OPEN_STATUSES),
                balance > 0)
        .scalar()
    )
    return float(round(float(total or 0)))


def outstanding_summary_for_customer(customer_id):
    """Return ``(amount, open_invoice_count)`` in one query.

    The customer profile needs both figures.  Loading every invoice and its
    payments just to produce these two header badges made the profile slower
    as a customer's billing history grew.
    """
    paid = _paid_per_invoice()
    balance = _balance_expression(paid)
    total, count = (
        db.session.query(
            func.coalesce(func.sum(balance), 0),
            func.count(Invoice.id),
        )
        .select_from(Invoice)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .filter(Invoice.customer_id == customer_id,
                Invoice.status.in_(OPEN_STATUSES),
                balance > 0)
        .one()
    )
    return float(round(float(total or 0))), int(count or 0)


def customers_with_balance():
    """A sub-select of every customer id that still owes something.

    Returned as a selectable, not a list, so callers can drop it straight into
    an ``IN (...)`` and let the database do the work. The bulk-message screen
    built the same set by loading every open invoice and reading
    ``Invoice.balance`` on each one, which lazy-loads that invoice's payments -
    one query per invoice, on the path that then sends a few hundred WhatsApp
    messages.
    """
    paid = _paid_per_invoice()
    balance = _balance_expression(paid)
    return (
        db.session.query(Invoice.customer_id)
        .select_from(Invoice)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .filter(Invoice.status.in_(OPEN_STATUSES), balance > 0)
        .distinct()
    )


def total_outstanding_for(customer_id_select):
    """Total owed by whichever customers a sub-select names.

    Takes a SQLAlchemy selectable of customer ids rather than a Python list.
    The expiry board's footer has to report what the WHOLE filtered set owes
    while the operator is looking at one page of it; materialising several
    thousand ids in Python only to send them straight back as an ``IN (...)``
    makes the statement grow with the result set and, past a few thousand,
    runs into MySQL's max_allowed_packet. The database already knows which
    rows match - let it keep them.
    """
    paid = _paid_per_invoice()
    balance = _balance_expression(paid)
    total = (
        db.session.query(func.coalesce(func.sum(balance), 0))
        .select_from(Invoice)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .filter(Invoice.customer_id.in_(customer_id_select),
                Invoice.status.in_(OPEN_STATUSES),
                balance > 0)
        .scalar()
    )
    return float(round(float(total or 0)))


def outstanding_for_customers(customer_ids):
    """``{customer_id: amount}`` for many customers, in ONE query.

    The plan-expiry board needed this figure for every row and got it by
    eager-loading each customer's whole invoice list, and every payment on
    every one of those invoices, so Python could add them up. On a few hundred
    customers that is merely wasteful. On ten thousand - roughly thirty
    thousand invoices and as many payments - it drags the entire result set
    into memory to produce one number per row, and the report takes half a
    second before MySQL has even been asked anything hard.

    Customers who owe nothing are simply absent from the result; callers
    should default to 0.
    """
    ids = [int(i) for i in customer_ids if i]
    if not ids:
        return {}

    paid = _paid_per_invoice()
    balance = _balance_expression(paid)

    rows = (
        db.session.query(Invoice.customer_id, func.coalesce(func.sum(balance), 0))
        .select_from(Invoice)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .filter(Invoice.customer_id.in_(ids),
                Invoice.status.in_(OPEN_STATUSES),
                balance > 0)
        .group_by(Invoice.customer_id)
        .all()
    )
    return {cid: float(round(float(total or 0))) for cid, total in rows}
