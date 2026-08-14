"""
link_meta_templates.py
======================

Point each CRM message type at the matching Meta-approved WhatsApp template.

    python link_meta_templates.py            # apply
    python link_meta_templates.py --dry-run  # show what would change
    python link_meta_templates.py --language en_US
    python link_meta_templates.py --reset-bodies   # also match the wording

Also runs as step 7 of ``upgrade_schema.py``, so the usual case needs no
separate command.

Why this exists
---------------
WhatsApp only carries free text to somebody who has messaged the business in
the last 24 hours. Every bill, reminder and expiry notice goes to people who
have not, so those must be sent as a template Meta has approved - otherwise
Meta accepts the message, reports success, and delivers nothing. That is the
whole reason messages "queued" and never arrived.

The approved templates are positional: Meta stores ``{{1}}``, ``{{2}}`` with
no names, so what matters is the ORDER of the values we supply. Getting that
order wrong does not fail loudly - it produces a message with the phone number
where the amount should be. So the order lives here, once, written against the
approved wording, rather than being inferred from our own template bodies
(which we are free to reword and Meta's are not).

The names on the left are this CRM's ``message_templates.template_type``
values. The names on the right are the templates in Meta WhatsApp Manager.
"""
import argparse
import os
import sys

#: (template_type, meta template name, variables IN THE APPROVED ORDER)
#:
#: Each variable is a key that services.messaging.build_context() produces.
#: The comment above each row is the approved wording it has to line up with.
MAPPING = [
    # "Dear {{1}} Your Internet Connection Has Been Expired ... {{2}}"
    ('expired', 'plan_expired',
     'customer_name,company_name'),

    # "Dear {{1}}, Your Internet Plan will expire {{2}}. Support No: {{3}} {{4}}"
    ('expiry_3d', 'internet_plan_expiring',
     'customer_name,expiry_date,company_phone,company_name'),
    ('expiry_2d', 'internet_plan_expiring',
     'customer_name,expiry_date,company_phone,company_name'),

    # "Dear {{1}} ... renewed and the invoice has been generated.
    #  Amount Due: Rs. {{2}} ... {{3}} {{4}}"
    ('renewal', 'plan_renewed',
     'customer_name,due_amount,company_phone,company_name'),

    # "Dear {{1}}, Your payment of Rs.{{2}} has been received.
    #  Your outstanding balance is Rs.{{3}} Support {{4}} {{5}}"
    ('payment_received', 'payment_received',
     'customer_name,paid_amount,balance,company_phone,company_name'),

    # "Dear {{1}}, Your payment of Rs.{{2}} is due ... {{3}} {{4}}"
    ('due_reminder', 'payment_due_reminder',
     'customer_name,due_amount,company_phone,company_name'),

    # "Dear {{1}}, Your username: {{2}} Amount payable: Rs.{{3}}
    #  For support: {{4}} {{5}}"
    ('welcome', 'new_account_created',
     'customer_name,username,amount,company_phone,company_name'),

    # "Invoice No: {{1}} Amount: {{2}}" - DOCUMENT header, see below.
    ('bill', 'invoice_attachment', 'invoice_no,amount'),
    ('summary_bill', 'invoice_attachment', 'invoice_no,amount'),
    ('detailed_bill', 'invoice_attachment', 'invoice_no,amount'),

    # "Receipt No:{{1}} Paid Amount: {{2}}" - DOCUMENT header.
    ('payment_approved', 'receipt_attachment', 'receipt_no,paid_amount'),

    # A renewal approved in the portal IS a renewal, and plan_renewed says
    # exactly that in approved wording. This used to be left on free text on
    # the theory that a customer who has just used the portal is inside the
    # 24-hour window - see the note under UNMAPPED for why that is wrong.
    ('renewal_approved', 'plan_renewed',
     'customer_name,due_amount,company_phone,company_name'),
]

#: Two of the approved templates have a DOCUMENT header, so the message
#: carries the actual PDF rather than a line of text about one. Meta fetches
#: that file itself from a public URL, which is why the CRM signs an expiring
#: public link for each (services/signed_links.py) - and why these two need
#: PUBLIC_BASE_URL set to a reachable https address before they will work.
#:
#: The wiring lives in services.messaging.META_DOCUMENT_HEADERS; it is
#: repeated here only so this file tells the whole story.
DOCUMENT_HEADER_TEMPLATES = ('invoice_attachment', 'receipt_attachment')

#: Types with no approved equivalent, and therefore NOT DELIVERABLE to most
#: customers. Listed so nobody has to work out whether they were forgotten.
#:
#: These were previously excused as "the customer just used the portal, so the
#: 24-hour window is open". That is wrong, and it is worth being precise about
#: because the same mistake is easy to make again: the 24-hour window opens
#: when a customer messages your number ON WHATSAPP. Paying on your website
#: does not open it. Somebody who has only ever used the portal has never
#: written to you on WhatsApp, so free text to them is refused exactly like
#: any other cold contact - with a 200 from the gateway and no delivery.
#:
#: Both of these need a Utility template created and approved in WhatsApp
#: Manager before they will reach anyone. Suggested wording, matching the
#: bodies already in services.messaging.DEFAULT_TEMPLATES:
#:
#:   payment_submitted   "Dear {{1}}, we have received your payment request
#:                        of Rs.{{2}}. It will be confirmed once verified.
#:                        {{3}}"                      -> 3 variables
#:   payment_rejected    "Dear {{1}}, we could not verify your payment of
#:                        Rs.{{2}}. Please contact us on {{3}}. {{4}}"
#:                                                    -> 4 variables
#:
#: Once approved, add them to MAPPING above and re-run this script.
UNMAPPED = {
    'payment_submitted': 'no approved template exists yet - create one in '
                         'WhatsApp Manager (Utility), then add it to MAPPING',
    'payment_rejected': 'no approved template exists yet - same as above',
}

#: Approved templates with nothing in this CRM to send them. Listed so it is
#: obvious they were considered rather than missed: the complaint and daily
#: report templates belong to a ticketing module that is not in this build.
NO_CRM_EQUIVALENT = (
    'daily_report', 'internet_down', 'internet_restored',
    'complaint_registered', 'issue_resolved', 'new_complaint',
)

#: Meta stores a language per template. English templates are registered as
#: either `en` or `en_US` and the two are NOT interchangeable - sending the
#: wrong one is rejected with error 132001, "template does not exist".
DEFAULT_LANGUAGE = os.environ.get('META_TEMPLATE_LANGUAGE', 'en')


def link(language=DEFAULT_LANGUAGE, dry_run=False, quiet=False,
         reset_bodies=False):
    """Apply MAPPING to the message_templates table. Returns what changed.

    ``reset_bodies`` also rewrites each linked template's TEXT to the wording
    in services.messaging.DEFAULT_TEMPLATES - which is written to match the
    approved Meta templates word for word.

    Off by default, because that text belongs to the operator and overwriting
    it without being asked would throw away wording somebody chose. Worth
    turning on once, when the approved templates and the CRM's copies have
    drifted apart: the CRM's version is what the SMS Log shows, so if it says
    something different from what Meta actually delivered, the log is lying.
    """
    from models import MessageTemplate, db
    from services.messaging import DEFAULT_TEMPLATES

    approved_body = {spec['template_type']: spec['body']
                     for spec in DEFAULT_TEMPLATES}

    def say(line):
        if not quiet:
            print(line)

    changed, missing, already, rebodied = [], [], [], []

    for template_type, meta_name, variables in MAPPING:
        row = MessageTemplate.query.filter_by(template_type=template_type).first()
        if row is None:
            missing.append(template_type)
            continue

        wanted_body = approved_body.get(template_type)
        body_differs = (reset_bodies and wanted_body
                        and (row.body or '').strip() != wanted_body.strip())

        if (row.meta_template_name == meta_name
                and row.meta_variables == variables
                and row.meta_language == language
                and not body_differs):
            already.append(template_type)
            continue

        if not dry_run:
            row.meta_template_name = meta_name
            row.meta_language = language
            row.meta_variables = variables
            # A mapped template that is switched off still cannot be sent.
            row.is_active = True
            if body_differs:
                row.body = wanted_body

        if body_differs:
            rebodied.append(template_type)
        changed.append((template_type, meta_name))

    if changed and not dry_run:
        db.session.commit()

    verb = 'would link' if dry_run else 'linked'
    for template_type, meta_name in changed:
        say(f'  + {verb} {template_type:<18} -> {meta_name}')
    for template_type in already:
        say(f'  = {template_type:<18} already linked')
    for template_type in missing:
        say(f'  ! {template_type:<18} has no row in message_templates')

    if missing and not quiet:
        say('    Run: Settings > WhatsApp gateway > Restore defaults, '
            'then this again.')

    if rebodied:
        say(f'  * rewrote the wording of {len(rebodied)} template(s) to match '
            f'the approved text')

    # ---- who can actually be reached --------------------------------- #
    #
    # The counts above say what was wired. This says what it MEANS, which is
    # the question anybody running this script actually has: which messages
    # reach a customer who has not written to us today - i.e. all of them.
    from models import MessageTemplate

    backed, free_text = [], []
    for row in MessageTemplate.query.order_by(MessageTemplate.template_type):
        if not row.is_active:
            continue
        if (getattr(row, 'meta_template_name', '') or '').strip():
            backed.append(row.template_type)
        else:
            free_text.append(row.template_type)

    say('')
    say(f'  {len(backed)} message type(s) go as an APPROVED TEMPLATE '
        f'- these reach any customer.')
    if free_text:
        say(f'  {len(free_text)} still go as FREE TEXT - these reach ONLY '
            f'customers who messaged you in the last 24 hours:')
        for template_type in free_text:
            why = UNMAPPED.get(template_type, 'not mapped to an approved template')
            say(f'    - {template_type:<18} {why}')

    return {'changed': [c[0] for c in changed], 'already': already,
            'missing': missing, 'rebodied': rebodied, 'language': language,
            'template_backed': backed, 'free_text_only': free_text}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', default=DEFAULT_LANGUAGE,
                        help='Meta language code for these templates '
                             f'(default {DEFAULT_LANGUAGE}; try en_US if Meta '
                             f'answers 132001)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--reset-bodies', action='store_true',
                        help="also rewrite each template's wording to match "
                             "the approved Meta text (overwrites any wording "
                             "you have customised)")
    args = parser.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app import app

    with app.app_context():
        print(f'Linking approved Meta templates (language: {args.language})')
        result = link(language=args.language, dry_run=args.dry_run,
                      reset_bodies=args.reset_bodies)

    if UNMAPPED:
        print('\nLeft on free text, on purpose:')
        for template_type, why in UNMAPPED.items():
            print(f'  - {template_type}: {why}')

    print('\nApproved at Meta but with nothing here to send them:')
    print('  ' + ', '.join(NO_CRM_EQUIVALENT))
    print('  (complaint / daily-report templates - that module is not in '
          'this build)')

    print('\nA linked type is sent as the approved template, which WhatsApp '
          'will carry\nto anybody at any time. An unlinked one is free text, '
          'which only reaches\nsomeone who messaged you in the last 24 hours.')

    print('\n' + ', '.join(DOCUMENT_HEADER_TEMPLATES)
          + ' attach a PDF, which Meta fetches\nfrom a public URL. Those two '
            'need PUBLIC_BASE_URL set to an https address\nthe internet can '
            'reach, or the send is rejected for a missing header.')

    if result['changed'] and not args.dry_run:
        print('\nRestart Flask, then send a test from Settings > WhatsApp '
              'gateway.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
