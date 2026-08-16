"""
services/invoice_pdf.py
=======================

Renders a customer invoice as a PDF matching the printed YASH bill.

ReportLab rather than WeasyPrint: WeasyPrint needs GTK/Cairo system libraries,
which are awkward to install on Windows. ReportLab is pure Python and ships as
a single wheel, so `pip install reportlab` is the whole setup.

Usage:
    from services.invoice_pdf import build_invoice_pdf
    pdf_bytes = build_invoice_pdf(invoice)
"""
from io import BytesIO
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

# Fallbacks used when the Company row is missing a field.
DEFAULTS = {
    'name': 'YASH INTERNET SERVICES',
    'address': 'K-47, Sector-4, Opp SDV College, Airoli, Navi Mumbai, Maharashtra - 400708.',
    'mobile': '9029508777',
    'email': 'yashinternetservices9@gmail.com',
    # The STATE, not the locality. This read "Airoli, Sector-4, Navi Mumbai."
    # - which is the street address again, printed under a heading that on a
    # tax invoice means the state of supply.
    'state': 'Maharashtra',
    # GST state code, which is what this box is for: Maharashtra is 27. It
    # held 400708, the PIN code, which is a different number entirely and
    # matches no state.
    'state_code': '27',
}

TERMS = [
    'All Internet plans are prepaid plans hence once amount is paid will not be refunded.',
    'Payment must be made within two days of renewal.',
    'Internet wires & switches are the sole property Of Yash Internet',
    'Fiber wire Installed at the time of connection has validity of 12 months post to that '
    'Rs.50/meter is applicable to replace it, if it is damaged due to any reason',
    "Router configuration would be done free of cost, but further if internet connection is "
    "affected due to router & it's hardware issues, then company would not be responsible for it.",
    'We only provide the internet plan for the use on single device, for using connection on '
    'multiple devices customer can install wireless Router.',
    'Customers should unplug wire from their devices at the times of rains & thunder lightnings; '
    'Company would not be responsible if any hardware is affected due to rains & thunder lightnings.',
    'Please Do Not Pay Technician Any Amount Without Manager Or Owner Consent',
]

ONES = ('', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight',
        'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen',
        'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen')
TENS = ('', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy',
        'Eighty', 'Ninety')


def _under_thousand(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return (TENS[n // 10] + (' ' + ONES[n % 10] if n % 10 else '')).strip()
    return (ONES[n // 100] + ' Hundred'
            + (' ' + _under_thousand(n % 100) if n % 100 else '')).strip()


def amount_in_words(amount):
    """Indian-system number to words: 'Three Thousand Six Hundred Only'."""
    rupees = int(round(float(amount or 0)))
    if rupees == 0:
        return 'Zero Only'

    parts = []
    for divisor, label in ((10_000_000, 'Crore'), (100_000, 'Lakh'), (1_000, 'Thousand')):
        if rupees >= divisor:
            count = rupees // divisor
            rupees %= divisor
            parts.append(f'{_under_thousand(count)} {label}')
    if rupees:
        parts.append(_under_thousand(rupees))

    return ' '.join(parts).strip() + ' Only'


def _fmt(value):
    return f'{float(value or 0):.2f}'


def _date(value):
    return value.strftime('%d-%b-%Y') if value else ''


def _company():
    """Best-effort company record; falls back to the printed letterhead."""
    try:
        from models import Company
        row = Company.query.first()
    except Exception:
        row = None

    out = dict(DEFAULTS)
    if row:
        for key in ('name', 'address', 'mobile', 'email', 'gstin', 'pan'):
            value = getattr(row, key, None)
            if value:
                out[key] = value

        # Settings owns these two, and the bill never read them: whatever an
        # administrator typed into Place of Supply and State Code was ignored
        # and the constants above printed instead.
        place = getattr(row, 'place_of_supply', None)
        if place:
            out['state'] = place
        code = getattr(row, 'state_code', None)
        # A PIN code is six digits; a GST state code is two. Printing the PIN
        # under "State Code" is the mistake this whole block is here to stop
        # repeating, so it is not carried over from the record either.
        if code and len(str(code).strip()) <= 3:
            out['state_code'] = str(code).strip()

        out['logo'] = getattr(row, 'company_logo', None)
    return out


def _root():
    """The application directory, whether or not there is an app context."""
    try:
        from flask import current_app
        return current_app.root_path
    except Exception:                                       # noqa: BLE001
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _logo_path(explicit=None):
    """Filesystem path to the company logo, or None if there isn't one.

    This is why bills went out with an empty letterhead. An uploaded logo lives
    at  static/uploads/logos/<name>  - that is where the upload writes it and
    what blueprints/api/serializers.logo_url() resolves for the web views - but
    the PDF route looked under  static/uploads/<name>, one directory short.
    os.path.exists() was therefore always False, logo_path arrived as None, and
    the letterhead cell rendered empty on every invoice and receipt. Nothing
    logged an error, because a company with no logo is not an error.

    Resolving it HERE rather than in each caller means the admin invoice, the
    portal download, the WhatsApp attachment and the receipt all get the same
    answer from one place.
    """
    if explicit and os.path.exists(explicit):
        return explicit

    stored = str(_company().get('logo') or '').replace('\\', '/').strip()
    if not stored:
        # Nothing uploaded at all. The application ships a logo, so a company
        # that never used the Settings upload still gets a letterhead rather
        # than a blank box - this returned None and printed nothing.
        return _bundled_logo(_root())
    if stored.startswith(('http://', 'https://')):
        # A remote URL cannot be handed to ReportLab as a local file.
        return _bundled_logo(_root())

    bare = stored.lstrip('/').split('/')[-1]
    root = _root()

    # Current layout first, then the places older uploads landed.
    for candidate in (
        os.path.join(root, 'static', 'uploads', 'logos', bare),
        os.path.join(root, 'static', 'uploads', bare),
        os.path.join(root, 'static', 'images', bare),
        os.path.join(root, 'static', bare),
        os.path.join(root, stored.lstrip('/')),
    ):
        if os.path.exists(candidate):
            return candidate

    return _bundled_logo(root, missing=stored)


def _bundled_logo(root, missing=''):
    """The logo that ships with the application.

    Uploads live on the web server's local disk. On a platform that rebuilds
    that disk on every deploy - Render, which is what this runs on - the
    Company row keeps pointing at a file the new container has never had, so
    `_logo_path` finds nothing and every bill and receipt prints with an empty
    letterhead. The file the repository ships is the same logo, so use it
    rather than printing a blank box.

    The miss is logged: a bill silently losing its letterhead is exactly the
    kind of fault nobody reports and everybody notices.
    """
    if missing:
        try:
            from flask import current_app
            current_app.logger.warning(
                'Company logo %s is not on disk (uploads do not survive a '
                'deploy on this host) - falling back to the bundled logo.',
                missing)
        except Exception:                                   # noqa: BLE001
            pass

    for name in ('logo.jpg', 'logo.png', 'logo.jpeg'):
        for candidate in (os.path.join(root, 'static', 'images', name),
                          os.path.join(root, 'static', name)):
            if os.path.exists(candidate):
                return candidate
    return None


def _logo_flowable(explicit=None, width=30, height=18):
    """The letterhead image, or '' when there is genuinely no logo to print.

    One helper for both documents: the invoice and the receipt each had their
    own copy of resolve-then-guard, which is how one of them can quietly stop
    printing a letterhead while the other keeps working.
    """
    resolved = _logo_path(explicit)
    if not resolved:
        return ''
    try:
        return Image(resolved, width=width * mm, height=height * mm,
                     kind='proportional')
    except Exception:                                       # noqa: BLE001
        # A corrupt or unreadable image must not take the whole bill down.
        return ''


def _gst_label(base, tax):
    """`18%` when the numbers say so, otherwise just `GST`.

    Read back from the invoice's own figures rather than from the current
    setting: a reprint of an old bill has to describe the tax that bill
    carries, not the rate the company happens to charge today.
    """
    if base <= 0 or tax <= 0:
        return 'GST'
    rate = round(tax / base * 100, 2)
    return f'{rate:g}%'


def _payment_breakdown(invoice, W, small, heading, grid):
    """Payment rows for the detailed bill.

    Staff names are omitted on purpose. The customer's copy should carry the
    facts they might need to trace a payment - date, mode, bank reference,
    transaction and receipt numbers - not who behind the counter handled it.
    """
    try:
        payments = [p for p in (invoice.payments or [])
                    if getattr(p, 'status', '') != 'rejected']
    except Exception:
        payments = []

    if not payments:
        return []

    rows = [[Paragraph('<b>Sr.no</b>', small),
             Paragraph('<b>Date</b>', small),
             Paragraph('<b>Mode</b>', small),
             Paragraph('<b>Bank / Transaction Ref.</b>', small),
             Paragraph('<b>Receipt No</b>', small),
             Paragraph('<b>Amount</b>', small)]]

    total = 0.0
    for index, payment in enumerate(payments, start=1):
        reference = (getattr(payment, 'reference', '') or '').strip()
        if not reference:
            reference = (payment.mode_detail or '').strip() or '-'
        amount = float(payment.amount or 0)
        total += amount
        rows.append([
            Paragraph(str(index), small),
            Paragraph(_date(payment.payment_date), small),
            Paragraph(payment.payment_mode or '-', small),
            Paragraph(reference, small),
            Paragraph(payment.receipt_no, small),
            Paragraph(_fmt(amount), small),
        ])

    # The total row spans the columns it has nothing to say about. Four empty
    # ruled boxes followed by a figure reads as a row that failed to render -
    # on a document about money, the wrong thing to make somebody wonder
    # about.
    rows.append(['', '', '', '',
                 Paragraph('<b>Total Paid</b>', small),
                 Paragraph(f'<b>{_fmt(total)}</b>', small)])
    last = len(rows) - 1

    widths = [W * 0.07, W * 0.15, W * 0.16, W * 0.32, W * 0.16, W * 0.14]
    payments_style = TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.6, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        # One box across the four columns that carry no total.
        ('SPAN', (0, last), (3, last)),
        ('BACKGROUND', (0, last), (-1, last), colors.Color(.96, .96, .96)),
    ])

    out = [
        Table([[Paragraph('PAYMENT DETAILS', heading)]],
              colWidths=[W], style=grid),
        # repeatRows: a customer with a long payment history spills onto a
        # second page, and a column of figures with no headings above it is
        # not a table anybody can read.
        Table(rows, colWidths=widths, style=payments_style, repeatRows=1),
    ]

    balance = float(invoice.balance or 0)
    out.append(Table([[Paragraph(
        f"<b>Balance Outstanding:</b> {_fmt(balance)}"
        + ("  (settled in full)" if balance <= 0 else ""), small)]],
        colWidths=[W], style=grid))
    return out


def build_invoice_pdf(invoice, logo_path=None, detailed=False):
    """Return the invoice as PDF bytes, laid out like the printed bill.

    ``detailed=True`` adds a payment breakdown underneath the summary -
    every payment against this bill with its mode, bank reference,
    transaction number and receipt number. Which member of staff took the
    money is deliberately left out: it is internal, and a customer holding
    the bill has no use for it.
    """
    company = _company()
    customer = invoice.customer
    plan = invoice.customer_plan.plan if invoice.customer_plan else None

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=f'Invoice {invoice.invoice_no}',
    )

    styles = getSampleStyleSheet()
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8, leading=11)
    bold = ParagraphStyle('bold', parent=small, fontName='Helvetica-Bold')
    heading = ParagraphStyle('heading', parent=styles['Normal'], fontSize=9,
                             alignment=TA_CENTER, fontName='Helvetica-Bold')
    term = ParagraphStyle('term', parent=small, fontSize=7.5, leading=11,
                          alignment=TA_LEFT)

    # Long unbroken strings - an email address, a plan code - have nowhere to
    # wrap, so in a narrow cell they run over the rule beside them. CJK word
    # wrapping breaks anywhere, which is the lesser evil on a bill.
    wrap = ParagraphStyle('wrap', parent=small, wordWrap='CJK')

    story = [Paragraph('INVOICE', heading), Spacer(1, 4)]
    W = doc.width

    # ---- letterhead: logo | company block ------------------------------
    logo_cell = _logo_flowable(logo_path)

    company_block = [
        Paragraph(f"<b>{company['name']}</b>", small),
        Paragraph(f"<b>Address:</b> {company.get('address', '')}", wrap),
        Paragraph(f"<b>Contact No.:</b> {company.get('mobile', '')} &nbsp;"
                  f"<b>Email:</b> {company.get('email', '')}", wrap),
    ]
    # Only when there is one. The line read "PAN No:" followed by nothing on
    # every bill this company has ever sent, because the label was printed
    # whether or not the field was filled in - and an empty label on an
    # invoice reads as a number somebody forgot to enter.
    if company.get('pan'):
        company_block.append(
            Paragraph(f"<b>PAN No:</b> {company['pan']}", small))

    letterhead = TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.6, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ])
    # No logo means no empty box: a third of the letterhead ruled off around
    # nothing looks like a missing image, which is precisely what it was.
    story.append(Table([[logo_cell, company_block]],
                       colWidths=[W * 0.28, W * 0.72], style=letterhead)
                 if logo_cell else
                 Table([[company_block]], colWidths=[W], style=letterhead))

    # ---- GSTIN / state row ---------------------------------------------
    grid = TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.6, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ])

    # The seller's tax identity. GSTIN only when the company has one - a
    # business that is not registered should not print an empty GSTIN box on
    # a bill it hands to a customer.
    seller = [Paragraph(f"State: {company.get('state', DEFAULTS['state'])}", wrap),
              Paragraph(f"State Code: {company.get('state_code', DEFAULTS['state_code'])}",
                        small)]
    if company.get('gstin'):
        gstin_row = [Paragraph(f"GSTIN: {company['gstin']}", small)] + seller
        story.append(Table([gstin_row],
                           colWidths=[W * 0.40, W * 0.36, W * 0.24], style=grid))
    else:
        story.append(Table([seller], colWidths=[W * 0.62, W * 0.38], style=grid))

    story.append(Table([[
        Paragraph(f"<b>Customer Name:</b> {customer.full_name if customer else ''}", wrap),
        Paragraph(f"<b>Tel:</b> {getattr(customer, 'mobile', '') or ''}", small),
        Paragraph(f"<b>Email:</b> {getattr(customer, 'email', '') or ''}", wrap),
    ]], colWidths=[W * 0.38, W * 0.24, W * 0.38], style=grid))

    address = ', '.join(filter(None, [
        getattr(customer, 'flat_no', None), getattr(customer, 'building', None),
        getattr(customer, 'locality', None), getattr(customer, 'area', None),
    ])) if customer else ''
    # `Address:, 1008, Rabale...` - that comma was hard-coded, so every bill
    # for a customer with no flat number opened its address with a comma.
    story.append(Table([[Paragraph(
        f"<b>Address:</b> {address or '-'}", wrap)]], colWidths=[W], style=grid))

    invoice_row = [
        Paragraph(f"<b>Invoice No:</b> {invoice.invoice_no}", wrap),
        Paragraph(f"<b>Invoice Date:</b> {_date(invoice.issue_date)}", small),
    ]
    if getattr(customer, 'gstin', ''):
        invoice_row.append(Paragraph(f"<b>Customer GSTIN:</b> {customer.gstin}", wrap))
        widths = [W * 0.40, W * 0.32, W * 0.28]
    else:
        widths = [W * 0.58, W * 0.42]
    story.append(Table([invoice_row], colWidths=widths, style=grid))

    story.append(Table([[Paragraph('INVOICE SUMMARY', heading)]],
                       colWidths=[W], style=grid))

    # ---- line items -----------------------------------------------------
    caption = invoice.caption or (plan.name if plan else 'Service')
    # The invoice's own service window first. Falling back to the plan's dates
    # was the only source before, which meant two things went wrong: a bill
    # with no linked plan row printed a blank Period, and a reprint of an old
    # bill showed the plan's CURRENT window - the dates move every renewal, so
    # last year's invoice would claim to cover this year.
    period = ''
    start = getattr(invoice, 'period_start', None)
    end = getattr(invoice, 'period_end', None)

    if not (start and end):
        cp = invoice.customer_plan
        if cp and cp.start_date and cp.end_date:
            start, end = cp.start_date, cp.end_date

    if start and end:
        period = f"{start.strftime('%d-%m-%Y')} to {end.strftime('%d-%m-%Y')}"

    # `total_amount` is what the customer owes, GST included; `tax_amount` is
    # the tax sitting inside it. The bill printed the gross figure as "Base
    # Amount" and never mentioned GST at all, so a taxable customer got an
    # invoice that could not be reconciled with their own books - and their
    # accountant could not see what tax had been charged.
    gross = float(invoice.total_amount or 0)
    tax = float(getattr(invoice, 'tax_amount', 0) or 0)
    base = gross - tax
    discount = float(getattr(invoice, 'discount_amount', 0) or 0)
    net = gross - discount

    items = [[
        Paragraph('<b>Sr.no</b>', small), Paragraph('<b>Description of services</b>', small),
        Paragraph('<b>No.of Service</b>', small), Paragraph('<b>Period</b>', small),
        Paragraph('<b>Base Amount</b>', small),
    ], [
        Paragraph('1', small), Paragraph(caption, wrap), Paragraph('1', small),
        Paragraph(period, small), Paragraph(_fmt(base), small),
    ]]
    story.append(Table(items,
                       colWidths=[W * 0.09, W * 0.36, W * 0.13, W * 0.26, W * 0.16],
                       style=TableStyle([
                           ('GRID', (0, 0), (-1, -1), 0.6, colors.black),
                           ('ALIGN', (0, 0), (0, -1), 'CENTER'),
                           ('ALIGN', (2, 0), (2, -1), 'CENTER'),
                           ('ALIGN', (3, 0), (4, -1), 'CENTER'),
                           ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                           ('TOPPADDING', (0, 0), (-1, -1), 5),
                           ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                       ])))

    # ---- totals ---------------------------------------------------------
    totals = [['Sub Amount:', _fmt(base)]]
    if tax > 0:
        totals.append([f'GST ({_gst_label(base, tax)}):', _fmt(tax)])
    totals.extend([['Discount:', _fmt(discount)],
                   ['Net Amount:', _fmt(net)], ['Total Amount:', _fmt(net)]])

    # The amount in words goes in the space beside the figures rather than in
    # a row of its own underneath. It is the same information a printed bill
    # puts there, and it fills what was a third of a page of ruled-off blank.
    # The inner table has to fit INSIDE its cell, padding included: at
    # 0.20 + 0.16 it was exactly as wide as the 0.36 column holding it, so
    # every figure and the rule above the total pushed out through the right
    # border of the bill. 0.19 + 0.145 leaves room for the 5pt padding.
    story.append(Table(
        [[Paragraph(f"<b>Rupees in words:</b> {amount_in_words(net)}", wrap),
          Table(totals, colWidths=[W * 0.19, W * 0.145], style=TableStyle([
              ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
              ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
              ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
              ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
              ('FONTSIZE', (0, 0), (-1, -1), 8),
              ('LINEABOVE', (0, -1), (-1, -1), 0.6, colors.black),
              ('TOPPADDING', (0, 0), (-1, -1), 2),
              ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
          ]))]],
        colWidths=[W * 0.64, W * 0.36],
        style=TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.6, colors.black),
            ('VALIGN', (0, 0), (0, 0), 'MIDDLE'),
            ('VALIGN', (1, 0), (1, 0), 'TOP'),
            ('LEFTPADDING', (0, 0), (0, 0), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (1, 0), (1, 0), 5),
        ])))

    if detailed:
        story.extend(_payment_breakdown(invoice, W, small, heading, grid))

    # ---- username, terms, footer ---------------------------------------
    body = [Paragraph(f"<b>Username:</b> {getattr(customer, 'username', '') or ''}", small),
            Spacer(1, 3), Paragraph('<b>NOTES:</b>', small),
            Paragraph('Terms and conditions:', small), Spacer(1, 6)]
    for index, text in enumerate(TERMS, start=1):
        body.append(Paragraph(f'{index}) {text}', term))
        body.append(Spacer(1, 5))

    story.append(Table([[body]], colWidths=[W], style=grid))
    story.append(Table([[Paragraph(
        '<b>This is a computer generated invoice and does not require any signature.</b>',
        heading)]], colWidths=[W], style=grid))

    doc.build(story)
    return buffer.getvalue()


def build_receipt_pdf(payment, logo_path=None):
    """
    Render a money-received receipt as PDF bytes.

    Deliberately shorter than the invoice: a receipt answers "who paid what,
    when, how, and against which bill" and nothing else. The terms and
    conditions belong on the bill, not on the acknowledgement of payment, so
    they are not repeated here.
    """
    company = _company()
    customer = payment.customer if hasattr(payment, 'customer') else None
    if customer is None:
        try:
            from models import Customer, db
            customer = db.session.get(Customer, payment.customer_id)
        except Exception:
            customer = None
    invoice = payment.invoice

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=f'Receipt {payment.id}',
    )

    styles = getSampleStyleSheet()
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8, leading=11)
    heading = ParagraphStyle('heading', parent=styles['Normal'], fontSize=9,
                             alignment=TA_CENTER, fontName='Helvetica-Bold')

    grid = TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.6, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ])

    story = [Paragraph('PAYMENT RECEIPT', heading), Spacer(1, 4)]
    W = doc.width

    wrap = ParagraphStyle('wrap', parent=small, wordWrap='CJK')
    logo_cell = _logo_flowable(logo_path)
    company_block = [
        Paragraph(f"<b>{company['name']}</b>", small),
        Paragraph(f"<b>Address:</b> {company.get('address', '')}", wrap),
        Paragraph(f"<b>Contact No.:</b> {company.get('mobile', '')} &nbsp;"
                  f"<b>Email:</b> {company.get('email', '')}", wrap),
    ]
    story.append(Table([[logo_cell, company_block]],
                       colWidths=[W * 0.28, W * 0.72], style=grid)
                 if logo_cell else
                 Table([[company_block]], colWidths=[W], style=grid))

    receipt_no = payment.receipt_no
    story.append(Table([[
        Paragraph(f"Receipt No: {receipt_no}", small),
        Paragraph(f"Receipt Date: {_date(payment.payment_date)}", small),
    ]], colWidths=[W * 0.5, W * 0.5], style=grid))

    story.append(Table([[
        Paragraph(f"Received From: {customer.full_name if customer else ''}", small),
        Paragraph(f"Username: {getattr(customer, 'username', '') or ''}", small),
    ]], colWidths=[W * 0.6, W * 0.4], style=grid))

    story.append(Table([[
        Paragraph(f"Tel: {getattr(customer, 'mobile', '') or ''}", small),
        Paragraph(f"Against Invoice: {invoice.invoice_no if invoice else '-'}", small),
    ]], colWidths=[W * 0.4, W * 0.6], style=grid))

    # ---- what was received ---------------------------------------------
    amount = float(payment.amount or 0)
    discount = float(getattr(payment, 'discount_amount', 0) or 0)

    rows = [[
        Paragraph('<b>Description</b>', small),
        Paragraph('<b>Mode</b>', small),
        Paragraph('<b>Reference</b>', small),
        Paragraph('<b>Amount</b>', small),
    ], [
        Paragraph(invoice.display_caption if invoice else 'Payment', small),
        Paragraph(payment.payment_mode or '', small),
        Paragraph(payment.reference or '-', small),
        Paragraph(_fmt(amount), small),
    ]]
    if discount:
        rows.append([
            Paragraph(f"Discount{' - ' + payment.discount_reason if getattr(payment, 'discount_reason', None) else ''}", small),
            Paragraph('', small), Paragraph('', small),
            Paragraph('-' + _fmt(discount), small),
        ])

    story.append(Table(rows,
                       colWidths=[W * 0.38, W * 0.20, W * 0.24, W * 0.18],
                       style=TableStyle([
                           ('GRID', (0, 0), (-1, -1), 0.6, colors.black),
                           ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
                           ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                           ('TOPPADDING', (0, 0), (-1, -1), 5),
                           ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                       ])))

    story.append(Table([[
        Paragraph(f"<b>Rupees in words:</b> {amount_in_words(amount)}", small),
        Paragraph(f"<b>Total Received: {_fmt(amount)}</b>", small),
    ]], colWidths=[W * 0.68, W * 0.32], style=grid))

    if invoice:
        story.append(Table([[Paragraph(
            f"Invoice total {_fmt(invoice.total_amount)} &nbsp;|&nbsp; "
            f"Paid to date {_fmt(invoice.paid_amount)} &nbsp;|&nbsp; "
            f"Balance {_fmt(invoice.balance)}", small)]],
            colWidths=[W], style=grid))

    # "Received By" used to sit here. It is internal - the customer's copy has
    # no use for which member of staff took the money, and printing a name on
    # every receipt invites them to ask for that person by name later.
    reference = (getattr(payment, 'reference', '') or '').strip()
    bits = []
    if reference:
        bits.append(f"<b>Transaction Ref.:</b> {reference}")
    if payment.book_receipt_no:
        bits.append(f"<b>Book Receipt No.:</b> {payment.book_receipt_no}")
    if payment.remarks:
        bits.append(f"<b>Remark:</b> {payment.remarks}")
    if bits:
        story.append(Table([[Paragraph('<br/>'.join(bits), small)]],
                           colWidths=[W], style=grid))

    story.append(Table([[Paragraph(
        '<b>This is a computer generated receipt and does not require any signature.</b>',
        heading)]], colWidths=[W], style=grid))

    doc.build(story)
    return buffer.getvalue()
