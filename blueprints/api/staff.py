"""
blueprints/api/staff.py
=======================

Staff accounts, reports and settings - the endpoints the new React screens
need that resources.py never exposed.

resources.py only had GET /users (list). Creating, editing, deactivating and
password-resetting a staff member all had to go through the Jinja2 app, so
the React Staff screen had nothing to call.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, request
from sqlalchemy import or_

from models import (Attendance, Customer, CustomerPlan, Expense, Invoice,
                    Leave, Payment, Payroll, Plan, StaffType, User, db)

from .serializers import customer_plan_dict, user_dict
from .utils import (admin_required, body, check_enums, current_staff_id, fail,
                    invalid_values, iso, money, ok, paginate, staff_required)

bp = Blueprint('api_staff', __name__)

STAFF_WRITABLE = ('username', 'full_name', 'email', 'mobile', 'role',
                  'staff_type_id', 'is_active')


# --------------------------------------------------------------------------- #
#  Staff accounts
# --------------------------------------------------------------------------- #
@bp.get('/staff')
@staff_required
def staff_list():
    query = User.query
    q = (request.args.get('q') or '').strip()
    if q:
        like = f'%{q}%'
        query = query.filter(or_(User.username.ilike(like),
                                 User.full_name.ilike(like),
                                 User.email.ilike(like),
                                 User.mobile.ilike(like)))
    role = request.args.get('role')
    if role:
        query = query.filter(User.role == role)

    rows, meta = paginate(query.order_by(User.username))
    return ok([user_dict(u) for u in rows], meta=meta)


@bp.get('/staff/<int:uid>')
@staff_required
def staff_get(uid):
    user = db.session.get(User, uid)
    if not user:
        return fail('not_found', 404)
    return ok(user_dict(user))


@bp.post('/staff')
@admin_required
def staff_create():
    data = body()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username:
        return fail('username_required', 400)
    if len(password) < 6:
        return fail('password_too_short', 400)
    if User.query.filter_by(username=username).first():
        return fail('username_taken', 409)

    # `role` is an Enum column. Checked here rather than left to the database:
    # a role outside the list used to save and then break every later read of
    # the users table - including the login lookup.
    bad = check_enums(User, data, STAFF_WRITABLE)
    if bad:
        return invalid_values(bad)

    user = User(username=username, role=data.get('role') or 'support')
    for field in STAFF_WRITABLE:
        if field in data and field != 'username':
            setattr(user, field, data[field])
    user.set_password(password)

    db.session.add(user)
    db.session.commit()
    return ok(user_dict(user)), 201


@bp.put('/staff/<int:uid>')
@admin_required
def staff_update(uid):
    user = db.session.get(User, uid)
    if not user:
        return fail('not_found', 404)

    data = body()
    username = (data.get('username') or '').strip()
    if username and username != user.username:
        if User.query.filter(User.username == username,
                             User.id != uid).first():
            return fail('username_taken', 409)

    bad = check_enums(User, data, STAFF_WRITABLE)
    if bad:
        return invalid_values(bad)

    for field in STAFF_WRITABLE:
        if field in data:
            setattr(user, field, data[field])

    password = data.get('password')
    if password:
        if len(password) < 6:
            return fail('password_too_short', 400)
        user.set_password(password)

    db.session.commit()
    return ok(user_dict(user))


@bp.delete('/staff/<int:uid>')
@admin_required
def staff_delete(uid):
    if uid == current_staff_id():
        return fail('cannot_disable_yourself', 400)

    user = db.session.get(User, uid)
    if not user:
        return fail('not_found', 404)

    if user.role == 'admin':
        remaining = User.query.filter(User.role == 'admin',
                                      User.is_active.is_(True),
                                      User.id != uid).count()
        if remaining == 0:
            return fail('cannot_remove_last_admin', 400)

    user.is_active = False
    db.session.commit()
    return ok({'status': 'deactivated'})


@bp.get('/staff-types')
@staff_required
def staff_type_list():
    rows = StaffType.query.order_by(StaffType.name).all()
    return ok([{'id': s.id, 'name': s.name} for s in rows])


# --------------------------------------------------------------------------- #
#  Reports
# --------------------------------------------------------------------------- #
@bp.get('/reports/plan-expiry')
@staff_required
def report_plan_expiry():
    """Plans by where they sit in their lifecycle.

    ``mode=renewed`` switches from "when does this end" to "when was this last
    renewed", so the Renewed row on the dashboard has somewhere to lead. It is
    the same rows and the same columns - only the date being filtered on
    changes - so the board did not need a second screen.
    """
    from sqlalchemy.orm import selectinload

    # `days=all` drops the far edge of the window: every plan still to expire,
    # or - in renewed mode - every renewal on record. The dashboard's "View
    # all" buttons use it, because the seven-day chips beside them are a view
    # of this week and the operator's next question is always "and the rest?".
    raw_days = str(request.args.get('days', 7)).strip().lower()
    unbounded = raw_days in ('all', '*')
    try:
        days = 7 if unbounded else int(raw_days)
    except (TypeError, ValueError):
        days = 7

    mode = (request.args.get('mode') or 'expiry').strip().lower()
    today = date.today()

    # The plan and the customer, and nothing else.
    #
    # This used to also selectinload every invoice of every customer and every
    # payment on every one of those invoices, purely so the loop below could
    # add up what each customer owed. At ten thousand customers that pulls
    # tens of thousands of rows into memory to produce one number per line -
    # measured at over half a second on this report alone. The totals now come
    # back from a single GROUP BY instead.
    query = CustomerPlan.query.options(
        selectinload(CustomerPlan.plan),
        selectinload(CustomerPlan.customer))

    if mode == 'renewed':
        # Renewed in the last `days` days, today included. Status is not
        # filtered here: a plan renewed three days ago and cancelled since is
        # still a renewal that happened, and hiding it would make the board
        # disagree with the dashboard chip that led the operator to it.
        query = query.filter(CustomerPlan.last_invoice_date.isnot(None),
                             CustomerPlan.last_invoice_date <= today)
        if not unbounded:
            window_start = today - timedelta(days=max(0, days - 1))
            query = query.filter(CustomerPlan.last_invoice_date >= window_start)
        query = query.order_by(CustomerPlan.last_invoice_date.desc())
    else:
        query = query.filter(CustomerPlan.status == 'active')
        if unbounded:
            query = query.filter(CustomerPlan.end_date >= today)
        elif days >= 0:
            query = query.filter(CustomerPlan.end_date >= today,
                                 CustomerPlan.end_date <= today + timedelta(days=days))
        else:
            query = query.filter(CustomerPlan.end_date < today)
        query = query.order_by(CustomerPlan.end_date)

    zone = request.args.get('zone')
    rows = query.all()

    # Filter first, then ask for the money - there is no point totalling
    # balances for rows the zone filter is about to drop.
    visible = [cp for cp in rows if cp.customer
               and not (zone and (cp.customer.zone or '') != zone)]

    from services.outstanding import outstanding_for_customers
    owed = outstanding_for_customers({cp.customer_id for cp in visible})

    out = []
    for cp in visible:
        customer = cp.customer
        outstanding = owed.get(customer.id, 0.0)
        out.append({
            'customer_plan_id': cp.id,
            'customer_id': customer.id,
            'customer_name': customer.full_name,
            'mobile': customer.mobile,
            'zone': customer.zone or '',
            'plan_name': cp.plan.name if cp.plan else '',
            'price': money(cp.effective_price),
            'validity_days': (cp.plan.validity_days if cp.plan else 30) or 30,
            'start_date': iso(cp.start_date),
            'end_date': iso(cp.end_date),
            'renewed_on': iso(cp.last_invoice_date),
            'days_left': (cp.end_date - today).days if cp.end_date else 0,
            'outstanding': outstanding,
        })
    return ok(out)


@bp.get('/reports/attendance')
@staff_required
def report_attendance():
    query = Attendance.query
    d_from = request.args.get('from')
    d_to = request.args.get('to')
    user_id = request.args.get('user_id')
    if d_from:
        query = query.filter(Attendance.date >= d_from)
    if d_to:
        query = query.filter(Attendance.date <= d_to)
    if user_id:
        query = query.filter(Attendance.user_id == user_id)

    rows = query.order_by(Attendance.date.desc()).all()
    return ok([{
        'id': a.id,
        'user_id': a.user_id,
        'user': a.user.full_name if getattr(a, 'user', None) else
                (db.session.get(User, a.user_id).full_name
                 if a.user_id and db.session.get(User, a.user_id) else ''),
        'date': iso(a.date),
        'status': a.status,
    } for a in rows])


@bp.get('/reports/leaves')
@staff_required
def report_leaves():
    query = Leave.query
    status = request.args.get('status')
    user_id = request.args.get('user_id')
    if status:
        query = query.filter(Leave.status == status)
    if user_id:
        query = query.filter(Leave.user_id == user_id)

    rows = query.order_by(Leave.start_date.desc()).all()
    return ok([{
        'id': l.id,
        'user_id': l.user_id,
        'user': (db.session.get(User, l.user_id).full_name
                 if l.user_id and db.session.get(User, l.user_id) else ''),
        'from_date': iso(l.start_date),
        'to_date': iso(l.end_date),
        'reason': l.reason or '',
        'status': l.status,
    } for l in rows])


@bp.post('/hr/leaves/<int:lid>/approve')
@admin_required
def leave_approve(lid):
    leave = db.session.get(Leave, lid)
    if not leave:
        return fail('not_found', 404)
    leave.status = 'approved'
    db.session.commit()
    return ok({'id': leave.id, 'status': 'approved'})


@bp.post('/hr/leaves/<int:lid>/reject')
@admin_required
def leave_reject(lid):
    leave = db.session.get(Leave, lid)
    if not leave:
        return fail('not_found', 404)
    leave.status = 'rejected'
    db.session.commit()
    return ok({'id': leave.id, 'status': 'rejected'})


@bp.get('/reports/payroll')
@staff_required
def report_payroll():
    query = Payroll.query
    month = request.args.get('month')          # YYYY-MM
    if month:
        try:
            start = datetime.strptime(month + '-01', '%Y-%m-%d').date()
            nxt = (start + timedelta(days=32)).replace(day=1)
            query = query.filter(Payroll.month_year >= start,
                                 Payroll.month_year < nxt)
        except ValueError:
            pass

    rows = query.order_by(Payroll.month_year.desc()).all()
    return ok([{
        'id': p.id,
        'user_id': p.user_id,
        'user': (db.session.get(User, p.user_id).full_name
                 if p.user_id and db.session.get(User, p.user_id) else ''),
        'month': iso(p.month_year),
        'salary': money(p.salary),
        'net_pay': money(p.salary),
        'status': 'paid' if p.paid else 'pending',
    } for p in rows])


@bp.get('/reports/collection')
@staff_required
def report_collection():
    """Day-by-day collection for a date range, split by payment mode."""
    d_from = request.args.get('from') or date.today().replace(day=1).isoformat()
    d_to = request.args.get('to') or date.today().isoformat()

    rows = Payment.query.filter(Payment.status == 'approved',
                                Payment.payment_date >= d_from,
                                Payment.payment_date <= d_to).all()

    by_day = {}
    for p in rows:
        key = iso(p.payment_date)
        bucket = by_day.setdefault(key, {'date': key, 'cash': 0.0,
                                         'cheque': 0.0, 'online': 0.0,
                                         'other': 0.0, 'total': 0.0})
        bucket[p.mode_group] = round(bucket[p.mode_group] + money(p.amount), 2)
        bucket['total'] = round(bucket['total'] + money(p.amount), 2)

    days = sorted(by_day.values(), key=lambda r: r['date'])
    return ok({
        'from': d_from,
        'to': d_to,
        'days': days,
        'grand_total': round(sum(d['total'] for d in days), 2),
    })


@bp.get('/reports/expenses')
@staff_required
def report_expenses():
    query = Expense.query
    d_from = request.args.get('from')
    d_to = request.args.get('to')
    if d_from:
        query = query.filter(Expense.expense_date >= d_from)
    if d_to:
        query = query.filter(Expense.expense_date <= d_to)
    for field in ('category_id', 'account_id', 'payee_id'):
        value = request.args.get(field)
        if value:
            query = query.filter(getattr(Expense, field) == value)

    rows = query.order_by(Expense.expense_date.desc()).all()
    return ok({
        'rows': [{
            'id': e.id,
            'date': iso(e.expense_date),
            'amount': money(e.amount),
            'description': e.description or '',
            'category': e.category.name if e.category else '',
            'account': e.account.name if e.account else '',
            'payee': e.payee.name if e.payee else '',
            'status': e.status,
        } for e in rows],
        'total': round(sum(money(e.amount) for e in rows), 2),
    })


@bp.get('/customers/plan-status')
@staff_required
def customer_plan_status():
    """Powers the Plan Status board: every customer with their live plan."""
    from sqlalchemy.orm import selectinload

    status = request.args.get('status')
    today = date.today()

    # One SELECT for the customers and one for the plans, instead of two per
    # row: the loop below reads cp.customer for the name and mobile, and
    # customer_plan_dict() reads cp.plan for the plan name and price. A
    # 25-row page was 31 queries.
    query = CustomerPlan.query.options(
        selectinload(CustomerPlan.customer),
        selectinload(CustomerPlan.plan))
    if status == 'active':
        query = query.filter(CustomerPlan.status == 'active',
                             CustomerPlan.end_date >= today)
    elif status == 'expired':
        query = query.filter(CustomerPlan.status == 'active',
                             CustomerPlan.end_date < today)
    elif status:
        query = query.filter(CustomerPlan.status == status)

    rows, meta = paginate(query.order_by(CustomerPlan.end_date))
    out = []
    for cp in rows:
        c = cp.customer
        out.append({
            'customer_plan_id': cp.id,
            'customer_id': c.id,
            'customer_name': c.full_name,
            'mobile': c.mobile,
            'zone': c.zone or '',
            'plan': customer_plan_dict(cp),
        })
    return ok(out, meta=meta)


# NOTE: GET /customers/<cid>/ledger used to live here.
#
# It was registered a second time by customer_billing.py, and because `staff`
# is registered before `customer_billing` this older handler silently won -
# the richer one never ran. Nothing failed loudly: the screens simply read
# fields (`reference`, wallet rows, the ids they link on) that this version
# does not return, so references rendered blank and wallet movements were
# missing from the statement.
#
# The two are not both needed. customer_billing.payment_ledger is a strict
# superset - same totals and closing balance, plus wallet entries, the ids the
# UI links on, and cancelled invoices excluded - so this one is gone rather
# than renamed. One ledger, one shape.
