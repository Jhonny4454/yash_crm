"""
blueprints/referral_bp.py
=========================

Refer-a-friend campaigns.

``templates/referral/{index,add,edit}.html`` and the ``ReferralCampaign`` /
``Referral`` models were already in the project, but no route ever rendered
them - every link in the UI raised a BuildError. This wires them up.

Endpoints are registered on the app directly (not namespaced under a
blueprint prefix) so the existing templates' ``url_for('referral_index')``
calls keep working unchanged.
"""
from datetime import date, datetime

from flask import (abort, flash, redirect, render_template, request, url_for)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import (BooleanField, DateField, DecimalField, SelectField,
                     StringField, SubmitField, TextAreaField)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from models import AuditLog, Customer, db
from models_ext import Referral, ReferralCampaign


class ReferralCampaignForm(FlaskForm):
    name = StringField('Campaign name',
                       validators=[DataRequired(), Length(max=120)])
    code = StringField('Referral code',
                       validators=[Optional(), Length(max=40)])
    description = TextAreaField('Description', validators=[Optional()])
    reward_type = SelectField('Reward type',
                              choices=[('fixed', 'Fixed amount'),
                                       ('percent', 'Percent of plan'),
                                       ('days', 'Extra days')],
                              default='fixed')
    referrer_reward = DecimalField('Reward for the referrer', places=2,
                                   default=0,
                                   validators=[Optional(),
                                               NumberRange(min=0)])
    referee_reward = DecimalField('Reward for the new customer', places=2,
                                  default=0,
                                  validators=[Optional(),
                                              NumberRange(min=0)])
    start_date = DateField('Starts on', validators=[Optional()])
    end_date = DateField('Ends on', validators=[Optional()])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save campaign')


def _audit(action, details):
    try:
        db.session.add(AuditLog(user_id=getattr(current_user, 'id', None),
                                action=action, details=details,
                                ip_address=request.remote_addr))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _admin_only():
    if not getattr(current_user, 'is_admin', lambda: False)():
        flash('Only an administrator can manage referral campaigns.', 'danger')
        abort(403)


# --------------------------------------------------------------------------- #
#  Views
# --------------------------------------------------------------------------- #
@login_required
def referral_index():
    campaigns = ReferralCampaign.query.order_by(
        ReferralCampaign.id.desc()).all()
    referrals = Referral.query.order_by(Referral.created_at.desc()).limit(200).all()

    stats = {
        'campaigns': len(campaigns),
        'running': len([c for c in campaigns if c.is_running]),
        'referrals': Referral.query.count(),
        'converted': Referral.query.filter(
            Referral.status.in_(('converted', 'rewarded'))).count(),
        'rewarded_total': float(sum(float(r.reward_credited or 0)
                                    for r in Referral.query.all())),
    }
    return render_template('referral/index.html', campaigns=campaigns,
                           referrals=referrals, stats=stats,
                           today=date.today())


@login_required
def referral_add():
    _admin_only()
    form = ReferralCampaignForm()
    if form.validate_on_submit():
        code = (form.code.data or '').strip() or None
        if code and ReferralCampaign.query.filter_by(code=code).first():
            flash('That referral code is already in use.', 'danger')
            return render_template('referral/add.html', form=form)

        campaign = ReferralCampaign(
            name=form.name.data.strip(),
            code=code,
            description=form.description.data,
            reward_type=form.reward_type.data,
            referrer_reward=form.referrer_reward.data or 0,
            referee_reward=form.referee_reward.data or 0,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            is_active=bool(form.is_active.data),
        )
        db.session.add(campaign)
        db.session.commit()
        _audit('Add Referral Campaign', campaign.name)
        flash('Referral campaign created.', 'success')
        return redirect(url_for('referral_index'))
    return render_template('referral/add.html', form=form)


@login_required
def referral_edit(id):
    _admin_only()
    campaign = ReferralCampaign.query.get_or_404(id)
    form = ReferralCampaignForm(obj=campaign)

    if form.validate_on_submit():
        code = (form.code.data or '').strip() or None
        if code and ReferralCampaign.query.filter(
                ReferralCampaign.code == code,
                ReferralCampaign.id != id).first():
            flash('That referral code is already in use.', 'danger')
            return render_template('referral/edit.html', form=form,
                                   campaign=campaign)

        campaign.name = form.name.data.strip()
        campaign.code = code
        campaign.description = form.description.data
        campaign.reward_type = form.reward_type.data
        campaign.referrer_reward = form.referrer_reward.data or 0
        campaign.referee_reward = form.referee_reward.data or 0
        campaign.start_date = form.start_date.data
        campaign.end_date = form.end_date.data
        campaign.is_active = bool(form.is_active.data)
        db.session.commit()
        _audit('Edit Referral Campaign', campaign.name)
        flash('Referral campaign updated.', 'success')
        return redirect(url_for('referral_index'))

    return render_template('referral/edit.html', form=form, campaign=campaign)


@login_required
def referral_toggle(id):
    _admin_only()
    campaign = ReferralCampaign.query.get_or_404(id)
    campaign.is_active = not campaign.is_active
    db.session.commit()
    _audit('Toggle Referral Campaign',
           f'{campaign.name} -> {"active" if campaign.is_active else "paused"}')
    flash(f'Campaign {"activated" if campaign.is_active else "paused"}.',
          'success')
    return redirect(url_for('referral_index'))


@login_required
def referral_delete(id):
    _admin_only()
    campaign = ReferralCampaign.query.get_or_404(id)
    if Referral.query.filter_by(campaign_id=id).first():
        flash('This campaign already has referrals against it, so it was '
              'paused instead of deleted.', 'warning')
        campaign.is_active = False
        db.session.commit()
        return redirect(url_for('referral_index'))

    name = campaign.name
    db.session.delete(campaign)
    db.session.commit()
    _audit('Delete Referral Campaign', name)
    flash('Referral campaign deleted.', 'success')
    return redirect(url_for('referral_index'))


@login_required
def referral_record():
    """Log a new referral against a campaign."""
    campaign_id = request.form.get('campaign_id', type=int)
    referrer_id = request.form.get('referrer_customer_id', type=int)
    if not referrer_id:
        flash('Choose the customer who made the referral.', 'danger')
        return redirect(url_for('referral_index'))

    referral = Referral(
        campaign_id=campaign_id,
        referrer_customer_id=referrer_id,
        referee_name=(request.form.get('referee_name') or '').strip()[:120],
        referee_mobile=(request.form.get('referee_mobile') or '').strip()[:20],
        status='pending',
    )
    db.session.add(referral)
    db.session.commit()
    _audit('Record Referral',
           f'{referral.referee_name or referral.referee_mobile} referred by '
           f'customer {referrer_id}')
    flash('Referral recorded.', 'success')
    return redirect(url_for('referral_index'))


@login_required
def referral_mark(id, status):
    _admin_only()
    if status not in ('pending', 'converted', 'rewarded', 'rejected'):
        abort(400)

    referral = Referral.query.get_or_404(id)
    referral.status = status
    if status in ('converted', 'rewarded') and not referral.converted_at:
        referral.converted_at = datetime.utcnow()
    if status == 'rewarded' and referral.campaign:
        referral.reward_credited = referral.campaign.referrer_reward or 0
    db.session.commit()
    _audit('Update Referral', f'Referral {id} -> {status}')
    flash(f'Referral marked as {status}.', 'success')
    return redirect(url_for('referral_index'))


# --------------------------------------------------------------------------- #
#  Wiring
# --------------------------------------------------------------------------- #
def register(app):
    app.add_url_rule('/referral', 'referral_index', referral_index)
    app.add_url_rule('/referral/add', 'referral_add', referral_add,
                     methods=['GET', 'POST'])
    app.add_url_rule('/referral/<int:id>/edit', 'referral_edit', referral_edit,
                     methods=['GET', 'POST'])
    app.add_url_rule('/referral/<int:id>/toggle', 'referral_toggle',
                     referral_toggle, methods=['POST'])
    app.add_url_rule('/referral/<int:id>/delete', 'referral_delete',
                     referral_delete, methods=['POST'])
    app.add_url_rule('/referral/record', 'referral_record', referral_record,
                     methods=['POST'])
    app.add_url_rule('/referral/<int:id>/mark/<status>', 'referral_mark',
                     referral_mark, methods=['POST'])
