"""
blueprints/api/settings_schema.py
=================================

What each row in the `settings` table actually IS.

The table stores everything as a string, which is the right call for storage
and the wrong thing to put on a screen. The Settings page used to render all
47 rows as identical text boxes with machine-generated labels - so
`tax_type` was a free text field that accepts "Exclude", "exclude",
"Exclde" or "banana" equally, `wa_enabled` was a box you typed 1 or 0 into,
and `Coll Amount Change` meant nothing to anybody.

This module is the missing half: for every key, what it is called in English,
what kind of control edits it, which values are legal, and what it does. Both
the API and the UI read it, so the dropdown on the screen and the validation
on the server can never disagree - the usual way a settings page ends up
accepting a value the rest of the application then chokes on.

Adding a setting: add it to SETTING_DEFAULTS in models_ext.py (storage) and
here (presentation). A key with no entry here still works - it falls back to a
plain text field - so nothing breaks by being forgotten, it is just plainer.
"""

# --------------------------------------------------------------------------- #
#  Groups, in the order they should appear on the page.
# --------------------------------------------------------------------------- #
GROUPS = [
    ('numbering', 'Numbering & prefixes',
     'The codes on staff, customers, invoices and receipts. The "next number" '
     'fields are what the NEXT record created will use.'),
    ('billing', 'Billing & tax',
     'How bills are priced, taxed and dated.'),
    ('collection', 'Collection counter',
     'What the person taking a payment at the counter is allowed to change.'),
    ('whatsapp', 'WhatsApp gateway',
     'Where outgoing WhatsApp messages are sent. Use the tester below after '
     'changing anything here.'),
    ('email', 'Outgoing email',
     'Brevo sends emailed invoices and receipts over a plain HTTPS API - no '
     'SMTP port to open, so it keeps working on Render. With no API key set, '
     'the mailer reports "dry-run" instead of pretending a message was '
     'delivered.'),
    ('payment', 'Online payments',
     'Cashfree credentials for the customer portal. Leave the App ID blank to '
     'hide the Pay button entirely.'),
    ('sms', 'SMS templates',
     'Plain-text fallbacks. WhatsApp templates live under Masters → Message '
     'templates.'),
    ('branding', 'Branding & links',
     'The banner in the customer portal and the app/website links used in '
     'messages.'),
    ('cloudinary', 'Cloudinary',
     'Cloud image storage for the logo, portal banner and customer documents. '
     'Files live on Cloudinary, not on the server disk, so they survive '
     'redeploys.'),
    ('general', 'Other', ''),
]

GROUP_ORDER = {key: i for i, (key, _label, _hint) in enumerate(GROUPS)}
GROUP_LABELS = {key: label for key, label, _hint in GROUPS}
GROUP_HINTS = {key: hint for key, _label, hint in GROUPS}


def _opt(value, label):
    return {'value': value, 'label': label}


#: Country codes the phone normaliser knows the national number length for.
#: Keep in step with _NATIONAL_DIGITS in services/messaging.py - a code that
#: is offered here but missing there falls back to a guess of 10 digits.
COUNTRY_CODES = [
    _opt('91', 'India (+91)'),
    _opt('1', 'USA / Canada (+1)'),
    _opt('44', 'United Kingdom (+44)'),
    _opt('61', 'Australia (+61)'),
    _opt('971', 'UAE (+971)'),
    _opt('977', 'Nepal (+977)'),
    _opt('880', 'Bangladesh (+880)'),
    _opt('94', 'Sri Lanka (+94)'),
]


# --------------------------------------------------------------------------- #
#  key -> how to show it and what it may hold
#
#  input:  text | number | switch | select | textarea | password | url | email
#  order:  position within its group
# --------------------------------------------------------------------------- #
FIELDS = {
    # ------------------------------------------------------------ numbering --
    'customer_prefix': dict(
        group='numbering', order=10, input='text', label='Customer code prefix',
        help='Letters in front of every customer code, e.g. C in C-2851.',
        maxlength=10),
    'customer_next_no': dict(
        group='numbering', order=11, input='number', label='Next customer number',
        help='The number the next new customer will be given.', min=1),
    'staff_prefix': dict(
        group='numbering', order=20, input='text', label='Staff code prefix',
        maxlength=10),
    'staff_next_no': dict(
        group='numbering', order=21, input='number', label='Next staff number',
        min=1),
    'invoice_prefix': dict(
        group='numbering', order=30, input='text', label='Invoice number prefix',
        help='Appears at the start of every invoice number, e.g. IN in IN-4958.',
        maxlength=10),
    'invoice_next_no': dict(
        group='numbering', order=31, input='number', label='Next invoice number',
        help='Lower this only if you are certain the numbers are free - '
             'invoice numbers must stay unique.', min=1),
    'receipt_prefix': dict(
        group='numbering', order=40, input='text', label='Receipt number prefix',
        maxlength=10),
    'receipt_next_no': dict(
        group='numbering', order=41, input='number', label='Next receipt number',
        min=1),
    'voucher_no': dict(
        group='numbering', order=50, input='number', label='Next voucher number',
        min=1),

    # -------------------------------------------------------------- billing --
    'invoice_package_price': dict(
        group='billing', order=10, input='select', label='Plan price to bill',
        help='Customer - use the price agreed with that customer. '
             'Master - always use the price on the plan itself.',
        options=[_opt('Customer', "The customer's agreed price"),
                 _opt('Master', 'The plan master price')]),
    'tax_type': dict(
        group='billing', order=20, input='select', label='Tax treatment',
        help='Whether the plan price already contains tax, or tax is added on '
             'top of it.',
        options=[_opt('Include', 'Price includes tax'),
                 _opt('Exclude', 'Tax added on top of the price')]),
    'tax_on': dict(
        group='billing', order=21, input='select', label='Calculate tax on',
        options=[_opt('Base', 'Base amount, before discount'),
                 _opt('Total', 'Total, after discount')]),
    'invoice_due_days': dict(
        group='billing', order=30, input='number', label='Invoice due after',
        help='Days between raising a bill and its due date.',
        min=0, max=365, suffix='days'),
    'grace_period_days': dict(
        group='billing', order=31, input='number', label='Grace period',
        help='Days a plan keeps working past its end date before it counts as '
             'expired.',
        min=0, max=90, suffix='days'),
    'discount_applicable': dict(
        group='billing', order=40, input='switch', label='Allow discounts',
        help='Off hides the discount fields on the invoice and payment screens.'),
    'happy_code_enabled': dict(
        group='billing', order=41, input='switch', label='Require happy code',
        help='Ask for the customer confirmation code before closing a job.'),

    # ----------------------------------------------------------- collection --
    'coll_amount_change': dict(
        group='collection', order=10, input='switch',
        label='Counter may change the amount',
        help='Off forces the amount shown on the bill to be taken in full.'),
    'coll_date_change': dict(
        group='collection', order=11, input='switch',
        label='Counter may back-date a payment',
        help='Off records every payment on the day it is entered.'),
    'coll_renew_only': dict(
        group='collection', order=12, input='switch',
        label='Counter may only take renewals',
        help='On stops the counter from collecting against anything except a '
             'plan renewal.'),

    # ------------------------------------------------------------- whatsapp --
    'wa_enabled': dict(
        group='whatsapp', order=10, input='switch', label='Send WhatsApp messages',
        help='Off means nothing is sent and every attempt is logged as '
             '"disabled" - useful while you are still setting the gateway up.'),
    'wa_provider': dict(
        group='whatsapp', order=11, input='select', label='Gateway',
        help='Meta Cloud API talks to Meta directly. WabAssist and Generic go '
             'through a reseller.',
        options=[_opt('webassist', 'WabAssist'),
                 _opt('meta_cloud', 'Meta Cloud API (direct)'),
                 _opt('generic', 'Generic / other (custom payload)')]),
    'wa_country_code': dict(
        group='whatsapp', order=12, input='select', label='Default country code',
        help='Added to any customer mobile stored without one.',
        options=COUNTRY_CODES),
    'wa_api_url': dict(
        group='whatsapp', order=20, input='url', label='API endpoint',
        help='Leave blank to use the gateway default. Anything entered here '
             'wins.',
        placeholder='https://api.wabassist.com/api/v1/messages/text'),
    'wa_api_token': dict(
        group='whatsapp', order=21, input='password', label='API token',
        secret=True),
    'wa_instance_id': dict(
        group='whatsapp', order=22, input='text', label='Instance ID',
        help='Meta Cloud API: the phone number ID. Resellers: your instance or '
             'device ID.'),
    'wa_sender': dict(
        group='whatsapp', order=23, input='text', label='Sender number',
        help='The WhatsApp business number messages come from.'),
    'wa_http_method': dict(
        group='whatsapp', order=30, input='select', label='HTTP method',
        options=[_opt('POST', 'POST'), _opt('GET', 'GET')]),
    'wa_payload_template': dict(
        group='whatsapp', order=31, input='textarea', label='Request body template',
        mono=True, rows=4,
        help='Generic gateways only. Placeholders: {phone} {message} '
             '{instance_id} {token} {sender}.'),
    'wa_document_url': dict(
        group='whatsapp', order=32, input='url', label='Document send endpoint',
        help='Only needed if PDFs go to a different address than text messages.'),

    # ---------------------------------------------------------------- email --
    'mail_enabled': dict(
        group='email', order=10, input='switch', label='Send email',
        help='Off means the mailer reports "dry-run" rather than pretending an '
             'invoice was delivered.'),
    'brevo_api_key': dict(
        group='email', order=12, input='password', label='Brevo API key',
        secret=True,
        help='Create one at brevo.com → SMTP & API → API Keys. Verify the '
             'From address under Senders & IP → Senders first; a verified '
             'email address works without owning a domain.'),
    'mail_from': dict(
        group='email', order=40, input='email', label='From address',
        placeholder='billing@yashinternetservices.in'),
    'mail_from_name': dict(
        group='email', order=41, input='text', label='From name',
        placeholder='YASH Internet Services'),

    # -------------------------------------------------------------- payment --
    'cashfree_env': dict(
        group='payment', order=10, input='select', label='Cashfree environment',
        help='Sandbox takes test cards only. Nothing is charged until this '
             'says Production.',
        options=[_opt('sandbox', 'Sandbox (testing)'),
                 _opt('production', 'Production (live money)')]),
    'cashfree_app_id': dict(
        group='payment', order=11, input='text', label='Cashfree App ID',
        help='Blank hides the Pay button in the customer portal.'),
    'cashfree_secret_key': dict(
        group='payment', order=12, input='password', label='Cashfree secret key',
        secret=True),

    # ------------------------------------------------------------------ sms --
    'sms_template_renewal': dict(
        group='sms', order=10, input='textarea', label='Renewal SMS', rows=3,
        help='Placeholders: {name} {plan} {expiry}.'),
    'sms_template_expiry': dict(
        group='sms', order=11, input='textarea', label='Expiry reminder SMS',
        rows=3, help='Placeholders: {name} {plan} {expiry}.'),

    # ------------------------------------------------------------- branding --
    'banner_link': dict(
        group='branding', order=10, input='text', label='Portal banner link',
        help='Where the portal banner takes the customer.'),
    'banner_image': dict(
        group='branding', order=11, input='text', label='Portal banner image',
        help='URL or filename of the portal banner image.'),

    # ---------------------------------------------------------- cloudinary --
    'cloudinary_enabled': dict(
        group='cloudinary', order=10, input='switch', label='Store images on Cloudinary',
        help='Off keeps uploads on the server disk, where a redeploy wipes them.'),
    'cloudinary_cloud_name': dict(
        group='cloudinary', order=11, input='text', label='Cloud name',
        help='From the Cloudinary Dashboard. It is the first part of every '
             'delivery URL, so it is not a secret.'),
    'cloudinary_api_key': dict(
        group='cloudinary', order=12, input='password', label='API key',
        help='From Cloudinary Dashboard > Settings > Access Keys.'),
    'cloudinary_api_secret': dict(
        group='cloudinary', order=13, input='password', label='API secret',
        secret=True,
        help='From Cloudinary Dashboard > Settings > Access Keys.'),
    'cloudinary_upload_preset': dict(
        group='cloudinary', order=20, input='text', label='Upload preset',
        help='Optional. An unsigned preset lets the browser upload directly '
             'without exposing the API secret. Blank falls back to signed '
             'server-side uploads.'),
    'cloudinary_folder': dict(
        group='cloudinary', order=21, input='text', label='Folder',
        help='Optional. Where uploads are stored inside your Cloudinary '
             'account, e.g. yash-crm.'),

    # -------------------------------------------------------------- general --
    'app_link': dict(
        group='general', order=10, input='url', label='Mobile app link',
        help='Sent in welcome messages.'),
    'web_link': dict(
        group='general', order=11, input='url', label='Website link'),

    # ---------------------------------------------------------- notifications --
    'admin_email': dict(
        group='notifications', order=10, input='email', label='Admin email',
        help='Receives the daily report and system alerts.'),
    'admin_mobile': dict(
        group='notifications', order=11, input='text', label='Admin mobile',
        help='Receives the daily report on WhatsApp.'),
}


SECRET_HINTS = ('secret', 'token', 'password', 'api_key')


def is_secret(key):
    spec = FIELDS.get(key) or {}
    if 'secret' in spec:
        return bool(spec['secret'])
    return any(hint in key for hint in SECRET_HINTS)


def describe(key, value_type='str'):
    """Everything the UI needs to draw one setting."""
    spec = dict(FIELDS.get(key) or {})
    group = spec.pop('group', None) or _fallback_group(key)
    label = spec.pop('label', None) or key.replace('_', ' ').capitalize()
    control = spec.pop('input', None) or _fallback_input(key, value_type)

    out = {
        'key': key,
        'label': label,
        'group': group,
        'group_label': GROUP_LABELS.get(group, group.title()),
        'group_hint': GROUP_HINTS.get(group, ''),
        'group_order': GROUP_ORDER.get(group, len(GROUPS)),
        'input': control,
        'order': spec.pop('order', 999),
        'is_secret': is_secret(key),
        'options': spec.pop('options', None),
        'known': key in FIELDS,
    }
    out.update({k: v for k, v in spec.items() if k != 'secret'})
    return out


def _fallback_group(key):
    for prefix, group in (('wa_', 'whatsapp'), ('sms_', 'sms'), ('mail_', 'email'),
                          ('cashfree_', 'payment'), ('invoice_', 'billing'),
                          ('receipt_', 'billing'), ('staff_', 'numbering'),
                          ('customer_', 'numbering'), ('coll_', 'collection'),
                          ('banner_', 'branding')):
        if key.startswith(prefix):
            return group
    return 'general'


def _fallback_input(key, value_type):
    if is_secret(key):
        return 'password'
    if value_type == 'bool':
        return 'switch'
    if value_type == 'int':
        return 'number'
    return 'text'


# --------------------------------------------------------------------------- #
#  Validation - the same rules the dropdowns above imply, enforced server side
# --------------------------------------------------------------------------- #
TRUE_WORDS = {'1', 'true', 'yes', 'on', 'enable', 'enabled'}
FALSE_WORDS = {'0', 'false', 'no', 'off', 'disable', 'disabled', ''}


def coerce(key, raw, value_type='str'):
    """Return (stored_value, error_message).

    Booleans are stored as 'True'/'False' to match SETTING_DEFAULTS, which is
    what services.messaging and the Jinja settings form both already read.
    """
    spec = FIELDS.get(key) or {}
    control = spec.get('input') or _fallback_input(key, value_type)
    label = spec.get('label') or key.replace('_', ' ').capitalize()
    text = '' if raw is None else str(raw).strip()

    if control == 'switch' or value_type == 'bool':
        low = text.lower()
        if low in TRUE_WORDS:
            return 'True', None
        if low in FALSE_WORDS:
            return 'False', None
        return None, f'{label} must be on or off.'

    if control == 'select':
        allowed = [o['value'] for o in (spec.get('options') or [])]
        if allowed and text not in allowed:
            return None, (f'{label} must be one of: '
                          + ', '.join(allowed) + f' (got "{text}").')
        return text, None

    if control == 'number' or value_type == 'int':
        if text == '':
            return None, f'{label} cannot be blank.'
        try:
            number = int(float(text))
        except (TypeError, ValueError):
            return None, f'{label} must be a whole number (got "{text}").'
        low, high = spec.get('min'), spec.get('max')
        if low is not None and number < low:
            return None, f'{label} cannot be less than {low}.'
        if high is not None and number > high:
            return None, f'{label} cannot be more than {high}.'
        return str(number), None

    if control == 'email' and text and '@' not in text:
        return None, f'{label} does not look like an email address.'

    if control == 'url' and text and not text.startswith(('http://', 'https://')):
        return None, f'{label} must start with http:// or https://.'

    maxlength = spec.get('maxlength')
    if maxlength and len(text) > maxlength:
        return None, f'{label} cannot be longer than {maxlength} characters.'

    return text, None
