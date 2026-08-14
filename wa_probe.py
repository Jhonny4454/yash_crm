#!/usr/bin/env python3
"""
wa_probe.py - send ONE approved WhatsApp template, using nothing but requests.

    python wa_probe.py 9876543210
    python wa_probe.py 9876543210 plan_expired
    python wa_probe.py --list

Why this exists
---------------
Two different things can stop a bill from arriving, and until they are
separated every test is ambiguous:

    1. WabAssist / Meta will not deliver a template to this number.
    2. The CRM is not sending a template in the first place.

This script settles (1) on its own. It imports no CRM code, builds the request
by hand from WabAssist's documented shape, and prints exactly what went out and
exactly what came back. If the message arrives, the gateway, the API key, the
approved template and the recipient are all proven good - and anything still
failing in the app is the app's problem, not WhatsApp's.

Send it to a phone that has NOT messaged your business today. That is the
whole point: free text reaches somebody who wrote to you an hour ago and tells
you nothing, because that case was never broken.

Reads the API key from, in order: --key, the WA_API_TOKEN environment
variable, the `wa_api_token` row in the database, then .env. Nothing is
written anywhere.
"""
import argparse
import json
import os
import re
import sys

try:
    import requests
except ImportError:                                          # pragma: no cover
    sys.exit("The 'requests' package is missing. Activate the venv first.")

TEXT_ENDPOINT = 'https://api.wabassist.com/api/v1/messages/text'
TEMPLATE_ENDPOINT = 'https://api.wabassist.com/api/v1/messages/template'

#: The approved templates and the values to put in {{1}}, {{2}}, ... in order.
#: Deliberately recognisable: if {{2}} shows up holding the phone number, the
#: ORDER is wrong and you can see it in the chat window.
SAMPLES = {
    'plan_expired': ['TEST CUSTOMER', 'Yash Internet Services'],
    'internet_plan_expiring': ['TEST CUSTOMER', '20-Aug-2026', '0000000000',
                               'Yash Internet Services'],
    'plan_renewed': ['TEST CUSTOMER', '1', '0000000000',
                     'Yash Internet Services'],
    'payment_due_reminder': ['TEST CUSTOMER', '1', '0000000000',
                             'Yash Internet Services'],
    'payment_received': ['TEST CUSTOMER', '1', '0', '0000000000',
                         'Yash Internet Services'],
    'new_account_created': ['TEST CUSTOMER', 'test.user', '1', '0000000000',
                            'Yash Internet Services'],
}

#: Approved WITH a PDF header, so they cannot be sent without a public link to
#: a file. Out of scope here - test one of the others first.
NEEDS_PDF = ('invoice_attachment', 'receipt_attachment')

NATIONAL_DIGITS = 10
COUNTRY_CODE = os.environ.get('WA_COUNTRY_CODE', '91')


def msisdn(raw):
    """Local or international number -> full international, no plus."""
    digits = re.sub(r'\D', '', str(raw or '')).lstrip('0')
    if not digits:
        return ''
    if len(digits) == NATIONAL_DIGITS:
        return COUNTRY_CODE + digits
    if digits.startswith(COUNTRY_CODE):
        return digits
    return COUNTRY_CODE + digits


def find_key(explicit=None):
    if explicit:
        return explicit.strip(), '--key'
    if os.environ.get('WA_API_TOKEN'):
        return os.environ['WA_API_TOKEN'].strip(), 'WA_API_TOKEN'

    # The live value the app is actually using.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import app as flask_app
        from models_ext import Setting
        with flask_app.app.app_context():
            row = Setting.query.filter_by(key='wa_api_token').first()
            if row and (row.value or '').strip():
                return row.value.strip(), 'settings table'
    except Exception:
        pass

    try:
        with open('.env', encoding='utf-8') as handle:
            for line in handle:
                if line.strip().startswith('WA_API_TOKEN='):
                    return line.split('=', 1)[1].strip(), '.env'
    except OSError:
        pass
    return '', ''


def redact(key):
    return f'{key[:4]}{"*" * max(0, len(key) - 8)}{key[-4:]}' if len(key) > 8 else '****'


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mobile', nargs='?', help='number to send to')
    parser.add_argument('template', nargs='?', default='payment_due_reminder',
                        help=f'approved template name (default: payment_due_reminder)')
    parser.add_argument('--key', help='API key; otherwise read from env/db/.env')
    parser.add_argument('--language', default='en', help="'en' or 'en_US'")
    parser.add_argument('--text', action='store_true',
                        help='send FREE TEXT instead, to show the contrast')
    parser.add_argument('--list', action='store_true', help='list templates and exit')
    args = parser.parse_args()

    if args.list:
        print('Templates this probe can send:\n')
        for name, values in SAMPLES.items():
            print(f'  {name:26} {len(values)} variable(s)')
        print('\nApproved with a PDF header, not testable here:')
        for name in NEEDS_PDF:
            print(f'  {name}')
        return 0

    if not args.mobile:
        parser.error('give a mobile number, or use --list')

    to = msisdn(args.mobile)
    if not to:
        return fail('That does not look like a mobile number.')

    key, source = find_key(args.key)
    if not key:
        return fail('No API key found. Pass --key, or set WA_API_TOKEN.')

    if args.text:
        url = TEXT_ENDPOINT
        payload = {'to': f'+{to}',
                   'text': 'Free-text probe from the CRM. If this does NOT '
                           'arrive, the 24-hour window is closed - which is '
                           'expected, and why bills must go as templates.'}
    else:
        if args.template in NEEDS_PDF:
            return fail(f"'{args.template}' is approved with a PDF header and "
                        f"cannot be sent without a public link to the file. "
                        f"Try payment_due_reminder or plan_expired instead.")
        values = SAMPLES.get(args.template)
        if values is None:
            return fail(f"No sample values for '{args.template}'. "
                        f"Run --list to see what this probe can send.")
        url = TEMPLATE_ENDPOINT
        payload = {
            'to': f'+{to}',
            'template_name': args.template,
            'language_code': args.language,
            'components': [{'type': 'body',
                            'parameters': [{'type': 'text', 'text': v}
                                           for v in values]}],
        }

    print(f'key      : {redact(key)}  (from {source})')
    print(f'to       : +{to}')
    print(f'mode     : {"FREE TEXT" if args.text else "APPROVED TEMPLATE"}')
    print(f'endpoint : {url}')
    print('body     :')
    print(json.dumps(payload, indent=2))
    print()

    try:
        resp = requests.post(url, json=payload, timeout=(5, 30), headers={
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
    except Exception as exc:
        return fail(f'{type(exc).__name__}: {exc}')

    print(f'HTTP {resp.status_code}')
    print(resp.text[:1200])
    print()

    body = resp.text or ''
    if '131026' in body:
        print('=> 131026 is about the RECIPIENT, not your templates. Most '
              'likely you are sending to your own WhatsApp Business number '
              '(one API account cannot message another). Try a personal '
              'phone that is not attached to any Business API.')
    elif '131047' in body or 'session' in body.lower():
        print('=> That is the 24-hour rule, and it only applies to FREE TEXT. '
              'If you got this while sending a template, the template did not '
              'go out - check the name and language against WhatsApp Manager.')
    elif '132001' in body:
        print("=> No approved template with that name AND language. 'en' and "
              "'en_US' are different templates to Meta - try --language en_US.")
    elif '132000' in body:
        print('=> Wrong number of variables for that template. Count the '
              '{{n}} placeholders in WhatsApp Manager and match SAMPLES here.')
    elif resp.ok:
        print('=> The gateway accepted it. Now look at the handset. If it '
              'ARRIVES, templates work end to end and anything still failing '
              'is in the app, not WhatsApp.')
    return 0 if resp.ok else 1


def fail(message):
    print(f'FAILED: {message}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
