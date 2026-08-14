"""
services/usernames.py
=====================

One place that decides whether a portal username may be used.

The rule the office asked for is stronger than "unique": a username is spent
the moment it is issued and is never issued again. That is two checks, not
one - the live ``customers`` table AND the permanent reservation ledger - and
having them in one function is the only way the answer given to the form while
someone is typing is guaranteed to match the answer given when they press
Save. A separate "is it free?" query written next to the availability endpoint
is how you end up telling someone a name is available and then refusing it.
"""
import re

from models import Customer, UsernameReservation, db

#: Long enough to be distinguishable, short enough for the column.
MIN_LENGTH = 3
MAX_LENGTH = 50

#: Letters, digits, dot, underscore, hyphen. No spaces and no '@': a username
#: that looks like an email address is indistinguishable from one at the
#: customer login, which accepts either.
ALLOWED = re.compile(r'^[A-Za-z0-9._-]+$')

#: Names that must never belong to a customer, because they read as the
#: company speaking rather than as a person.
RESERVED_WORDS = {
    'admin', 'administrator', 'root', 'staff', 'support', 'help', 'helpdesk',
    'billing', 'accounts', 'system', 'test', 'null', 'none', 'undefined',
    'unicrm', 'yash', 'office',
}


def normalise(raw):
    """The comparison key: trimmed and lowercased."""
    return (raw or '').strip().lower()


def validate(raw):
    """Return an error string, or None when the shape is acceptable.

    Shape only - this says nothing about whether it is taken.
    """
    username = (raw or '').strip()
    if not username:
        return 'A username is required.'
    if len(username) < MIN_LENGTH:
        return f'Use at least {MIN_LENGTH} characters.'
    if len(username) > MAX_LENGTH:
        return f'Use at most {MAX_LENGTH} characters.'
    if not ALLOWED.match(username):
        return ('Use letters, numbers, dot, underscore or hyphen only - '
                'no spaces.')
    if normalise(username) in RESERVED_WORDS:
        return 'That name is reserved. Please choose another.'
    return None


def availability(raw, scope='customer'):
    """``(available: bool, reason: str)`` for a username.

    ``reason`` is written to be shown to the operator as-is.
    """
    problem = validate(raw)
    if problem:
        return False, problem

    key = normalise(raw)

    # Someone is using it right now.
    live = Customer.query.filter(db.func.lower(Customer.username) == key).first()
    if live is not None:
        return False, 'That username already belongs to another customer.'

    # Someone used it before. Deliberately a different message: "already taken"
    # sends the operator hunting for a customer who is no longer there.
    spent = UsernameReservation.query.filter_by(username_key=key,
                                                scope=scope).first()
    if spent is not None:
        return False, ('That username has been used before and cannot be '
                       'issued again. Please choose another.')

    return True, 'Available.'


def reserve(raw, customer_id=None, scope='customer'):
    """Record a username as spent. Safe to call twice for the same name.

    Called on create, not on check: reserving what someone typed into a
    availability box would burn every name they tried.
    """
    key = normalise(raw)
    if not key:
        return None

    existing = UsernameReservation.query.filter_by(username_key=key,
                                                   scope=scope).first()
    if existing is not None:
        # Fill in the owner if an earlier backfill did not know it.
        if customer_id and not existing.customer_id:
            existing.customer_id = customer_id
        return existing

    row = UsernameReservation(username=(raw or '').strip(), username_key=key,
                              scope=scope, customer_id=customer_id)
    db.session.add(row)
    return row


def backfill_existing():
    """Reserve every username already in use. Idempotent.

    Without this the ledger starts empty, so on the first run every existing
    customer's username would look reusable the moment their row went away.
    Returns how many reservations were added.
    """
    known = {row.username_key for row in
             UsernameReservation.query.with_entities(
                 UsernameReservation.username_key).all()}

    added = 0
    for customer in Customer.query.filter(Customer.username.isnot(None)).all():
        key = normalise(customer.username)
        if not key or key in known:
            continue
        known.add(key)
        db.session.add(UsernameReservation(
            username=customer.username.strip(), username_key=key,
            scope='customer', customer_id=customer.id))
        added += 1

    if added:
        db.session.commit()
    return added
