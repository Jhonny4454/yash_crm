"""
services/customer_purge.py
==========================

Deleting a customer, and everything that was ever attached to them.

Delete used to mean two different things depending on which route answered:
the customers list only deactivated the account (the row stayed, the history
stayed, the name still appeared in every report), while the customer screen
refused outright as soon as a bill existed. Neither of them removed a
customer, so an operator who wanted a record gone had no way to get one.

This module is the real thing. It walks the schema rather than naming
fifteen tables by hand, because a hand-written list is a list somebody has to
remember to update: the day a new table gains a ``customer_id`` the delete
would stop with a foreign-key error and no explanation of which table
objected. Walking ``db.metadata`` in reverse dependency order means children
are always removed before their parents, whatever the schema grows next.

Two things deliberately survive a purge:

* The audit trail and vendor bills. An audit row is the OFFICE's record of
  what staff did, and a vendor bill is money the business owes somebody
  else - neither stops being true because a customer left. Their link to the
  customer is set to NULL instead, so the row keeps its meaning without
  pointing at a row that no longer exists.
* The username reservation. ``UsernameReservation.customer_id`` is not a
  foreign key precisely so it outlives the customer: a username is spent when
  it is issued and is never reissued, or the next person to get it inherits
  the old customer's identity in every log that still names them.

Nothing here commits. The caller owns the transaction, so a purge that fails
halfway leaves the customer exactly as they were.
"""
from models import Customer, CustomerPlan, Invoice, Payment, db

#: Parent tables a row can point at that make it part of this customer.
_ROOTS = ('customers', 'invoices', 'payments', 'customer_plans')

#: ``table.column`` pairs that are unlinked (set to NULL) instead of deleted,
#: because the row belongs to the business rather than to the customer.
#: A column listed here that turns out to be NOT NULL falls back to deleting
#: the row - there is no third option that leaves the database consistent.
UNLINK = {
    ('audit_logs', 'customer_id'),
    ('vendor_bills', 'customer_id'),
    ('vendor_bills', 'invoice_id'),
    # A referral has two sides. Losing the person who was referred should not
    # erase the referrer's reward; losing the referrer takes the row with it,
    # because that column cannot be NULL.
    ('referrals', 'referee_customer_id'),
}


def _linked_columns(table):
    """Columns in ``table`` that point at one of the root tables.

    Returns ``[(column, root_table_name), ...]``.
    """
    found = []
    for column in table.columns:
        for fk in column.foreign_keys:
            target = fk.column.table.name
            if target in _ROOTS:
                found.append((column, target))
                break
    return found


def plan_purge(customer):
    """What ``purge_customer`` would remove, without removing it.

    Split out so the delete endpoint can report the damage in its audit line
    and so this is testable without a destroyed row.
    """
    cid = customer.id
    invoice_ids = {i for (i,) in db.session.query(Invoice.id)
                   .filter(Invoice.customer_id == cid)}
    payment_ids = {p for (p,) in db.session.query(Payment.id)
                   .filter(Payment.customer_id == cid)}
    # A payment written against this customer's invoice belongs to this
    # customer even if its own customer_id was never set - older rows exist
    # that way, and leaving them behind would break the invoice delete.
    if invoice_ids:
        payment_ids |= {p for (p,) in db.session.query(Payment.id)
                        .filter(Payment.invoice_id.in_(invoice_ids))}
    plan_ids = {p for (p,) in db.session.query(CustomerPlan.id)
                .filter(CustomerPlan.customer_id == cid)}

    return {
        'customers': {cid},
        'invoices': invoice_ids,
        'payments': payment_ids,
        'customer_plans': plan_ids,
    }


def purge_customer(customer):
    """Remove ``customer`` and everything that points at them.

    Returns ``{table_name: rows_affected}`` for the tables that were actually
    touched. Does not commit.
    """
    ids = plan_purge(customer)
    report = {}

    def _count(name, rows):
        if rows:
            report[name] = report.get(name, 0) + int(rows)

    # Reverse dependency order: a table is only visited once every table that
    # references it has been dealt with. `customers` itself is last, and is
    # skipped here so the ORM can delete it (and run its own cascades).
    for table in reversed(db.metadata.sorted_tables):
        if table.name == 'customers':
            continue

        deletes = []
        for column, root in _linked_columns(table):
            targets = ids.get(root)
            if not targets:
                continue
            if (table.name, column.name) in UNLINK and column.nullable:
                result = db.session.execute(
                    table.update().where(column.in_(targets))
                    .values({column.name: None}))
                _count(f'{table.name} (unlinked)', result.rowcount)
            else:
                deletes.append(column.in_(targets))

        if deletes:
            from sqlalchemy import or_
            condition = deletes[0] if len(deletes) == 1 else or_(*deletes)
            result = db.session.execute(table.delete().where(condition))
            _count(table.name, result.rowcount)

    # The ORM session still holds the rows just deleted underneath it, and a
    # stale copy would be re-INSERTed on flush. Dropping the identity map is
    # cheaper than expiring row by row and cannot miss one.
    db.session.expire_all()
    db.session.delete(db.session.get(Customer, customer.id))
    _count('customers', 1)
    return report
