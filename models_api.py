"""
models_api.py
=============

Additional models introduced by the REST / React / React-Native rebuild.

Import this from app.py *after* models.py so the tables are registered::

    from models import db
    import models_api            # noqa: F401

Then run ``db.create_all()`` (dev) or the SQL in ``mysql/migrations_api.sql``.
"""
from datetime import datetime

from models import db


# --------------------------------------------------------------------------- #
#  Push notification device registry
# --------------------------------------------------------------------------- #
class DeviceToken(db.Model):
    """One push token per installed app copy for a customer."""
    __tablename__ = 'device_tokens'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'),
                            nullable=False, index=True)
    token = db.Column(db.String(255), nullable=False, index=True)
    platform = db.Column(db.String(20), default='android')   # android | ios | web
    provider = db.Column(db.String(20), default='expo')      # expo | fcm | apns
    app_version = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    customer = db.relationship('Customer', backref='device_tokens')

    __table_args__ = (
        db.UniqueConstraint('customer_id', 'token', name='uq_device_token'),
    )


# --------------------------------------------------------------------------- #
#  Notification templates
# --------------------------------------------------------------------------- #
class NotificationTemplate(db.Model):
    """
    An editable in-app / push notification body.

    ``code`` is the stable key application code looks the template up by;
    ``name`` is the human label shown on the admin screen.
    """
    __tablename__ = 'notification_templates'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(150), default='')
    body = db.Column(db.Text, default='')
    description = db.Column(db.String(255))
    channel = db.Column(db.String(20), default='push')   # push | whatsapp | both
    send_push = db.Column(db.Boolean, default=True)
    send_whatsapp = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    def render(self, context=None):
        """Return ``(title, body)`` with ``{placeholders}`` filled in."""
        ctx = context or {}

        def _fill(text):
            out = text or ''
            for key, value in ctx.items():
                out = out.replace('{%s}' % key, '' if value is None else str(value))
            return out

        return _fill(self.title), _fill(self.body)


# --------------------------------------------------------------------------- #
#  Delivered notifications
# --------------------------------------------------------------------------- #
class Notification(db.Model):
    """A notification actually delivered (or queued) to one customer."""
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'),
                            nullable=False, index=True)
    template_code = db.Column(db.String(50), index=True)
    title = db.Column(db.String(150))
    body = db.Column(db.Text)
    channel = db.Column(db.String(20), default='push')
    #: queued | sent | failed | read
    status = db.Column(db.String(20), default='queued', index=True)
    error = db.Column(db.String(500))
    is_read = db.Column(db.Boolean, default=False, index=True)
    read_at = db.Column(db.DateTime)
    sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    customer = db.relationship('Customer', backref='notifications')

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = datetime.utcnow()
            if self.status != 'failed':
                self.status = 'read'


# --------------------------------------------------------------------------- #
#  Seed data
# --------------------------------------------------------------------------- #
DEFAULT_NOTIFICATION_TEMPLATES = [
    dict(code='plan_expiring',
         name='Plan expiring soon',
         title='Your plan expires in {days} days',
         body='Hi {customer_name}, your {plan_name} plan expires on '
              '{end_date}. Renew from the app to stay connected.',
         description='Sent 7/3/1 days before expiry.'),
    dict(code='plan_expired',
         name='Plan expired',
         title='Your plan has expired',
         body='Hi {customer_name}, your {plan_name} plan expired on '
              '{end_date}. Renew now to restore your connection.',
         description='Sent on the expiry date.'),
    dict(code='payment_received',
         name='Payment received',
         title='Payment received - Rs {amount}',
         body='Thank you {customer_name}. We have received Rs {amount} '
              'against invoice {invoice_no}.',
         description='Sent when a payment is recorded or a gateway payment '
                     'succeeds.'),
    dict(code='invoice_generated',
         name='New invoice',
         title='New invoice {invoice_no}',
         body='Hi {customer_name}, invoice {invoice_no} for Rs {amount} is '
              'due on {due_date}. Pay from the app.',
         description='Sent when a new invoice is raised.'),
    dict(code='plan_changed',
         name='Plan changed',
         title='Plan updated to {plan_name}',
         body='Hi {customer_name}, your plan is now {plan_name}, valid until '
              '{end_date}.',
         description='Sent after a successful plan change.'),
]


def seed_notification_templates():
    """Idempotent - safe to call on every boot. Returns rows created."""
    created = 0
    for spec in DEFAULT_NOTIFICATION_TEMPLATES:
        if not NotificationTemplate.query.filter_by(code=spec['code']).first():
            db.session.add(NotificationTemplate(**spec))
            created += 1
    if created:
        db.session.commit()
    return created
