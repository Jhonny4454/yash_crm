"""
services/plans.py
=================

The one place that answers "which plan is this customer on?", and the one
place that closes the rows a new plan replaces.

A customer is on a single service at a time. The customer_plans table is a
history of that: assigning or changing a plan closes the row that was open
and writes a new one, so the account can be read back later. That only holds
if every write closes what it replaces - and every write used to do it with
``filter_by(status='active').first()``, which has two holes in it:

* ``first()`` is whatever row the database hands back first. With one open
  row that is the right answer by luck, not by rule; with two it is a coin
  toss, so the header could print one plan's expiry while Renew extended the
  other's.
* it closes ONE row. An account that had somehow picked up a second open row
  kept it forever, because each new assignment only ever shut one of them.

So both questions live here now: `current_plan` picks deterministically, and
`close_active_plans` shuts every open row rather than one of them.

When more than one row is open, the longest-running one is the current plan -
that is the date the customer's service actually runs to, and it is the date
the header, the expiry reports and the renewal quote all key off.
"""
from datetime import date

from models import CustomerPlan

#: The only status that means "this is the service the customer has now".
ACTIVE = 'active'


def _sort_key(customer_plan):
    return (customer_plan.end_date or date.min,
            customer_plan.start_date or date.min,
            customer_plan.id or 0)


def active_plans(customer_id):
    """Every open plan row for this customer, the current one first."""
    return (CustomerPlan.query
            .filter(CustomerPlan.customer_id == customer_id,
                    CustomerPlan.status == ACTIVE)
            .order_by(CustomerPlan.end_date.desc(),
                      CustomerPlan.start_date.desc(),
                      CustomerPlan.id.desc())
            .all())


def current_plan(customer_id):
    """The plan this customer is on, or None if nothing is open."""
    rows = active_plans(customer_id)
    return rows[0] if rows else None


def current_plan_of(customer):
    """`current_plan` for a Customer already loaded with its plans.

    Same choice, no extra query - the serializers already hold the rows.
    """
    if customer is None:
        return None
    open_rows = [cp for cp in (customer.plans or []) if cp.status == ACTIVE]
    return max(open_rows, key=_sort_key) if open_rows else None


def close_active_plans(customer_id, keep=None, status='terminated'):
    """Close every open plan for this customer except ``keep``.

    ``keep`` may be a CustomerPlan or an id. Returns the rows that were
    closed, so a caller can report or log what it replaced. Does not commit -
    the caller owns the transaction, because closing the old plan and writing
    the new one have to land together or not at all.
    """
    keep_id = getattr(keep, 'id', keep)
    closed = []
    for customer_plan in active_plans(customer_id):
        if keep_id is not None and customer_plan.id == keep_id:
            continue
        customer_plan.status = status
        closed.append(customer_plan)
    return closed
