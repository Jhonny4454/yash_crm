"""
blueprints/api/authorisation.py
===============================

The Authorising Report: the queue of collected payments waiting for an admin
to sign them off, and the bulk sign-off itself.

Split from resources.py because this screen has a different shape to the
generic payment list. It joins the customer in so the operator can see *where*
the money came from - flat, building, area, zone - and filter on those, which
is how collections are actually reconciled at the end of a day: by walking a
zone or a collector's round, not by scrolling a flat list of payment ids.

The join is explicit and eager. Reading `payment.customer` per row would be an
N+1 that turns a 200-row queue into 200 extra queries.
"""
from datetime import date, datetime

from flask import Blueprint, request
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from models import Customer, Invoice, Payment, User, db

from .utils import (admin_required, body, current_staff_id, fail, iso, money,
                    ok, paginate, staff_required)

bp = Blueprint('api_authorisation', __name__)

#: A payment is in the queue when it has been collected but nobody senior has
#: confirmed it. Rejected entries are out - they were already decided.
QUEUE_STATUSES = ('approved', 'pending')


def _queue_query():
    return (Payment.query
            .options(joinedload(Payment.invoice))
            .join(Customer, Customer.id == Payment.customer_id)
            .filter(Payment.status.in_(QUEUE_STATUSES),
                    Payment.authorized_at.is_(None)))


def _apply_filters(query):
    """Translate the Search Customer panel into SQL."""
    args = request.args

    for field, column in (('locality', Customer.locality),
                          ('area', Customer.area),
                          ('building', Customer.building),
                          ('zone', Customer.zone)):
        value = (args.get(field) or '').strip()
        if value:
            query = query.filter(column == value)

    mode = (args.get('mode') or '').strip()
    if mode:
        query = query.filter(Payment.payment_mode == mode)

    staff = (args.get('staff_id') or '').strip()
    if staff.isdigit():
        query = query.filter(Payment.received_by_user_id == int(staff))

    d_from = (args.get('from') or '').strip()
    d_to = (args.get('to') or '').strip()
    if d_from:
        query = query.filter(Payment.payment_date >= d_from)
    if d_to:
        query = query.filter(Payment.payment_date <= d_to)

    q = (args.get('q') or '').strip()
    if q:
        like = f'%{q}%'
        query = query.filter(or_(
            Customer.first_name.ilike(like), Customer.last_name.ilike(like),
            Customer.username.ilike(like), Customer.mobile.ilike(like),
            Payment.book_receipt_no.ilike(like)))

    return query


def _outstanding_map(customer_ids):
    """Total still owed per customer, as one aggregate query.

    The comment here used to claim "one query rather than one per row", and
    the invoice fetch was indeed one query - but reading invoice.balance on
    each result lazy-loads that invoice's payments, so it was one query per
    INVOICE after all. Same arithmetic, done in SQL.
    """
    if not customer_ids:
        return {}

    from sqlalchemy import func
    from services.outstanding import (OPEN_STATUSES, _balance_expression,
                                      _paid_per_invoice)

    paid = _paid_per_invoice()
    balance = _balance_expression(paid)
    rows = (db.session.query(Invoice.customer_id,
                             func.coalesce(func.sum(balance), 0))
            .select_from(Invoice)
            .outerjoin(paid, paid.c.invoice_id == Invoice.id)
            .filter(Invoice.customer_id.in_(customer_ids),
                    Invoice.status.in_(OPEN_STATUSES),
                    balance > 0)
            .group_by(Invoice.customer_id)
            .all())

    return {cid: round(float(total or 0), 2) for cid, total in rows}


@bp.get('/payments/authorisation-queue')
@staff_required
def authorisation_queue():
    """Payments awaiting sign-off, with the customer detail needed to place them."""
    query = _apply_filters(_queue_query()).order_by(
        Payment.payment_date.desc(), Payment.id.desc())

    rows, meta = paginate(query, default_per_page=50)

    customers = {c.id: c for c in Customer.query.filter(
        Customer.id.in_([p.customer_id for p in rows])).all()} if rows else {}
    outstanding = _outstanding_map(list(customers))

    entries = []
    for payment in rows:
        customer = customers.get(payment.customer_id)
        entries.append({
            'id': payment.id,
            'customer_id': payment.customer_id,
            'name': customer.full_name if customer else '',
            'username': (customer.username or '') if customer else '',
            'flat_no': (customer.flat_no or '') if customer else '',
            'building': (customer.building or '') if customer else '',
            'area': (customer.area or '') if customer else '',
            'locality': (customer.locality or '') if customer else '',
            'zone': (customer.zone or '') if customer else '',
            'mode': payment.payment_mode or '',
            # The bank reference, cheque number or UPI id, whichever was given.
            'details': payment.reference or payment.payment_mode or '',
            'receipt_no': payment.book_receipt_no or f'R{payment.id}',
            'amount': money(payment.amount),
            'discount': money(payment.discount_amount),
            'outstanding': outstanding.get(payment.customer_id, 0.0),
            'receipt_date': iso(payment.payment_date),
            'recorded_at': iso(payment.created_at),
            'agent': (payment.received_by_user.full_name
                      if payment.received_by_user else ''),
            'invoice_id': payment.invoice_id,
            'invoice_no': payment.invoice.invoice_no if payment.invoice else '',
            'source': payment.source or 'admin',
            'status': payment.status,
        })

    return ok(entries, meta=meta, totals={
        'amount': round(sum(e['amount'] for e in entries), 2),
        'discount': round(sum(e['discount'] for e in entries), 2),
        'count': meta.get('total', len(entries)),
    })


@bp.get('/payments/authorisation-filters')
@staff_required
def authorisation_filters():
    """
    Options for the Search Customer panel.

    Drawn from the customers who actually have money in the queue, not from
    the master tables. A zone with nothing pending is a dead end in this
    screen, and offering it only wastes the operator's time.
    """
    pending = _queue_query().all()
    ids = {p.customer_id for p in pending}

    localities, areas, buildings, zones = set(), set(), set(), set()
    if ids:
        for customer in Customer.query.filter(Customer.id.in_(ids)).all():
            if customer.locality:
                localities.add(customer.locality)
            if customer.area:
                areas.add(customer.area)
            if customer.building:
                buildings.add(customer.building)
            if customer.zone:
                zones.add(customer.zone)

    staff_ids = {p.received_by_user_id for p in pending if p.received_by_user_id}
    staff = User.query.filter(User.id.in_(staff_ids)).all() if staff_ids else []

    return ok({
        'localities': sorted(localities),
        'areas': sorted(areas),
        'buildings': sorted(buildings),
        'zones': sorted(zones),
        'modes': sorted({p.payment_mode for p in pending if p.payment_mode}),
        'staff': [{'id': u.id, 'name': u.full_name or u.username} for u in
                  sorted(staff, key=lambda u: (u.full_name or u.username or ''))],
        'pending_count': len(pending),
    })


@bp.post('/payments/authorize-bulk')
@admin_required
def authorize_bulk():
    """
    Sign off several payments at once - the Submit button on the report.

    Entries that are not actually in the queue are reported back by id rather
    than silently skipped: an operator who ticked twenty rows and sees
    "20 authorised" must be able to trust that all twenty moved.
    """
    ids = body().get('ids') or []
    if not isinstance(ids, list) or not ids:
        return fail('ids_required', 400,
                    detail='Select at least one payment to authorise.')

    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return fail('invalid_ids', 400)

    if len(ids) > 500:
        return fail('too_many', 400,
                    detail='Authorise at most 500 payments at a time.')

    found = {p.id: p for p in Payment.query.filter(Payment.id.in_(ids)).all()}

    authorised, skipped = [], []
    now = datetime.utcnow()
    staff_id = current_staff_id()

    for pid in ids:
        payment = found.get(pid)
        if payment is None:
            skipped.append({'id': pid, 'reason': 'No such payment.'})
            continue
        if payment.status == 'rejected':
            skipped.append({'id': pid, 'reason': 'Already rejected.'})
            continue
        if payment.authorized_at is not None:
            skipped.append({'id': pid, 'reason': 'Already authorised.'})
            continue

        payment.status = 'approved'
        payment.authorized_at = now
        payment.authorized_by_user_id = staff_id

        # Approving the money can settle the bill it was against.
        invoice = payment.invoice
        if invoice and invoice.balance <= 0 and invoice.status != 'cancelled':
            invoice.status = 'paid'

        authorised.append(pid)

    db.session.commit()

    try:
        from app import log_audit
        log_audit('Authorise Payments',
                  f'Authorised {len(authorised)} payment(s): '
                  f'{", ".join(str(i) for i in authorised[:20])}')
    except Exception:
        pass

    return ok({
        'authorised': authorised,
        'authorised_count': len(authorised),
        'skipped': skipped,
        'as_of': iso(now),
    })


@bp.post('/payments/reject-bulk')
@admin_required
def reject_bulk():
    """The other half of the decision: refuse several entries with one reason."""
    data = body()
    ids = data.get('ids') or []
    reason = (data.get('reason') or '').strip()

    if not isinstance(ids, list) or not ids:
        return fail('ids_required', 400,
                    detail='Select at least one payment to reject.')
    if not reason:
        return fail('reason_required', 400,
                    detail='Say why these entries are being rejected - the '
                           'customer may ask.')

    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return fail('invalid_ids', 400)

    found = {p.id: p for p in Payment.query.filter(Payment.id.in_(ids)).all()}
    rejected, skipped = [], []
    now = datetime.utcnow()
    staff_id = current_staff_id()

    for pid in ids:
        payment = found.get(pid)
        if payment is None:
            skipped.append({'id': pid, 'reason': 'No such payment.'})
            continue
        if payment.authorized_at is not None:
            skipped.append({'id': pid, 'reason': 'Already authorised.'})
            continue

        payment.status = 'rejected'
        payment.rejection_reason = reason[:255]
        payment.rejected_at = now
        payment.rejected_by_user_id = staff_id
        payment.authorized_at = now
        payment.authorized_by_user_id = staff_id

        # Money that is no longer good must reopen the bill it had settled.
        invoice = payment.invoice
        if invoice and invoice.status == 'paid' and invoice.balance > 0:
            invoice.status = 'sent'

        rejected.append(pid)

    db.session.commit()
    return ok({'rejected': rejected, 'rejected_count': len(rejected),
               'skipped': skipped})


@bp.get('/payments/authorisation-summary')
@staff_required
def authorisation_summary():
    """Per-day totals for the queue, matching the dashboard's To Be Authorized."""
    # One GROUP BY, not "load every pending payment and count them in Python".
    #
    # The old version called _queue_query().all(), which joinedloads each
    # payment's invoice, and then bucketed the whole list by date. That is a
    # full read of the authorisation queue - with a join - to produce a
    # handful of daily totals, and it was the slowest endpoint in the app at
    # scale: 753ms on a ten-thousand-customer database, on a panel that sits
    # on the dashboard and therefore loads for everybody, all day.
    from sqlalchemy import func

    rows = (
        db.session.query(
            Payment.payment_date,
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.amount), 0),
        )
        .join(Customer, Customer.id == Payment.customer_id)
        .filter(Payment.status.in_(QUEUE_STATUSES),
                Payment.authorized_at.is_(None))
        .group_by(Payment.payment_date)
        .order_by(Payment.payment_date.asc())
        .all()
    )

    # Oldest first: money has been waiting longest on the earliest day, and
    # that is the one to sign off first.
    return ok([{'date': iso(day or date.today()),
                'count': int(count or 0),
                'amount': round(float(total or 0), 2)}
               for day, count, total in rows])
