"""
blueprints/portal_admin_bp.py
=============================

The admin side of the customer portal: one screen showing everything
customers have submitted and are waiting on.

    /admin/portal-activity                       The queue
    /admin/portal-activity/payments/<id>/approve Verify a UTR and credit it
    /admin/portal-activity/payments/<id>/reject  Turn an entry down
    /admin/portal-activity/renewals/<id>/approve Extend/switch a plan
    /admin/portal-activity/renewals/<id>/reject  Turn a renewal down
    /admin/utr-search                            Find a payment by reference

Approving a payment here does exactly what approving it from the
authorisation queue does - both call services/payments.py - so a renewal
gets applied either way.
"""
from datetime import date, datetime, timedelta

from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from models import Customer, Invoice, Payment, db
from models_ext import RenewalRequest
from services import payments as payment_service
from services import renewals

portal_admin_bp = Blueprint('portal_admin', __name__, url_prefix='/admin')


def _admin_only(f):
    from functools import wraps

    @wraps(f)
    def _wrap(*a, **kw):
        if not getattr(current_user, 'is_admin', lambda: False)():
            flash('Only an administrator can do that.', 'danger')
            return redirect(url_for('portal_admin.queue'))
        return f(*a, **kw)
    return _wrap


def _audit(action, details):
    try:
        from models import AuditLog
        db.session.add(AuditLog(
            user_id=getattr(current_user, 'id', None),
            action=action, details=(details or '')[:500],
            ip_address=request.remote_addr))
        db.session.commit()
    except Exception:                                    # noqa: BLE001
        db.session.rollback()


def _back(default_endpoint='portal_admin.queue'):
    return redirect(request.referrer or url_for(default_endpoint))


# --------------------------------------------------------------------------- #
#  The queue
# --------------------------------------------------------------------------- #
@portal_admin_bp.route('/portal-activity')
@login_required
def queue():
    pending_payments = payment_service.pending_portal_entries()
    pending_renewals = renewals.pending_requests()

    # Plan-change requests raised the older way (an invoice tagged in remarks)
    # that nobody has settled yet.
    legacy_changes = [
        i for i in Invoice.query.filter(
            Invoice.status.in_(['draft', 'sent', 'overdue']),
            Invoice.remarks.like('PLAN_CHANGE:%')).order_by(
            Invoice.issue_date.desc()).all()
        if i.balance > 0]

    recent = (Payment.query
              .filter(Payment.source == 'portal',
                      Payment.status.in_(['approved', 'rejected']))
              .order_by(Payment.id.desc()).limit(50).all())

    return render_template(
        'admin/portal_activity.html',
        pending_payments=pending_payments,
        pending_renewals=pending_renewals,
        legacy_changes=legacy_changes,
        recent=recent,
        pending_total=sum(float(p.amount or 0) for p in pending_payments),
        renewal_total=sum(float(r.amount or 0) for r in pending_renewals),
        today=date.today())


# --------------------------------------------------------------------------- #
#  Payment entries
# --------------------------------------------------------------------------- #
@portal_admin_bp.route('/portal-activity/payments/<int:id>/approve',
                       methods=['POST'])
@login_required
@_admin_only
def payment_approve(id):
    from models import db as _db
    payment = _db.session.query(Payment).with_for_update().get_or_404(id)
    ok, renewal_applied = payment_service.approve_payment(payment, current_user)
    if not ok:
        flash('That payment has already been dealt with.', 'info')
        return _back()

    _audit('Verify Portal Payment',
           f'Verified UTR {payment.reference or "-"} for Rs.{payment.amount} '
           f'from customer #{payment.customer_id}')
    if renewal_applied:
        flash(f'Payment verified and the plan was renewed for Rs.'
              f'{payment.amount:,.2f}.', 'success')
    else:
        flash(f'Payment of Rs.{payment.amount:,.2f} verified and credited.',
              'success')
    return _back()


@portal_admin_bp.route('/portal-activity/payments/<int:id>/reject',
                       methods=['POST'])
@login_required
@_admin_only
def payment_reject(id):
    from models import db as _db
    payment = _db.session.query(Payment).with_for_update().get_or_404(id)
    reason = (request.form.get('reason') or '').strip()
    ok, _ = payment_service.reject_payment(payment, current_user, reason)
    if not ok:
        flash('That payment has already been rejected.', 'info')
        return _back()
    _audit('Reject Portal Payment',
           f'Rejected UTR {payment.reference or "-"} for Rs.{payment.amount}: '
           f'{payment.rejection_reason}')
    flash('Payment entry rejected and the customer has been told why.',
          'warning')
    return _back()


# --------------------------------------------------------------------------- #
#  Renewal requests
# --------------------------------------------------------------------------- #
@portal_admin_bp.route('/portal-activity/renewals/<int:id>/approve',
                       methods=['POST'])
@login_required
@_admin_only
def renewal_approve(id):
    req = RenewalRequest.query.get_or_404(id)
    note = (request.form.get('note') or '').strip() or 'Approved by admin'
    if not renewals.approve(req, current_user, note):
        flash('That renewal has already been decided.', 'info')
        return _back()

    _audit('Approve Renewal',
           f'{req.plan_label} for {req.duration_label} - customer '
           f'#{req.customer_id}, active until {req.effective_to}')
    try:
        from services import messaging
        messaging.send_template(req.customer, 'renewal_approved',
                                customer_plan=req.customer_plan,
                                invoice=req.invoice)
    except Exception:                                    # noqa: BLE001
        pass
    flash(f'Renewal approved. Plan is active until '
          f'{req.effective_to:%d-%b-%Y}.', 'success')
    return _back()


@portal_admin_bp.route('/portal-activity/renewals/<int:id>/reject',
                       methods=['POST'])
@login_required
@_admin_only
def renewal_reject(id):
    req = RenewalRequest.query.get_or_404(id)
    note = (request.form.get('note') or '').strip() or 'Rejected by admin'
    if not renewals.reject(req, current_user, note):
        flash('That renewal has already been decided.', 'info')
        return _back()
    _audit('Reject Renewal', f'Renewal #{req.id} rejected: {note}')
    flash('Renewal rejected and its invoice cancelled.', 'warning')
    return _back()


# --------------------------------------------------------------------------- #
#  UTR verification
# --------------------------------------------------------------------------- #
@portal_admin_bp.route('/utr-search')
@login_required
def utr_search():
    term = (request.args.get('q') or '').strip()
    results = payment_service.search_by_reference(term) if term else []
    return render_template('admin/utr_search.html',
                           term=term, results=results, today=date.today())


# --------------------------------------------------------------------------- #
#  Activity log / login history
# --------------------------------------------------------------------------- #
#: Actions that are specifically about who signed in.
LOGIN_ACTIONS = ('Login', 'Logout', 'Failed Login', 'Customer Login')


@portal_admin_bp.route('/activity-log')
@login_required
def activity_log():
    """
    Everything the system recorded: who did what, from where, and when.

    `log_audit()` has always been writing these rows - this is the screen
    that finally reads them back.
    """
    from models import AuditLog, User

    page = max(1, request.args.get('page', type=int) or 1)
    per_page = 50
    view = (request.args.get('view') or 'all').lower()
    term = (request.args.get('q') or '').strip()
    user_id = request.args.get('user_id', type=int)
    days = request.args.get('days', type=int) or 30

    q = AuditLog.query
    if view == 'logins':
        q = q.filter(AuditLog.action.in_(LOGIN_ACTIONS))
    elif view == 'failed':
        q = q.filter(AuditLog.action == 'Failed Login')
    elif view == 'money':
        q = q.filter(AuditLog.action.ilike('%payment%')
                     | AuditLog.action.ilike('%invoice%')
                     | AuditLog.action.ilike('%renew%')
                     | AuditLog.action.ilike('%discount%'))

    if days > 0:
        q = q.filter(AuditLog.created_at >= datetime.utcnow() - timedelta(days=days))
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    if term:
        like = f'%{term}%'
        q = q.filter(AuditLog.action.ilike(like) | AuditLog.details.ilike(like))

    rows = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    total = rows.count()
    entries = rows.offset((page - 1) * per_page).limit(per_page).all()

    return render_template('admin/activity_log.html',
                           entries=entries,
                           total=total,
                           page=page,
                           pages=max(1, (total + per_page - 1) // per_page),
                           view=view, term=term, days=days,
                           user_id=user_id,
                           users=User.query.order_by(User.username).all(),
                           today=date.today())


def register(app):
    app.register_blueprint(portal_admin_bp)

    @app.context_processor
    def _portal_admin_badges():
        """Counter for the 'Portal activity' nav item."""
        from flask_login import current_user as _u
        if not getattr(_u, 'is_authenticated', False):
            return {}
        try:
            n = (Payment.query.filter(Payment.source == 'portal',
                                      Payment.status == 'pending').count()
                 + RenewalRequest.query.filter_by(status='pending').count())
        except Exception:                                # noqa: BLE001
            n = 0
        return {'portal_pending_count': n}

    return portal_admin_bp
