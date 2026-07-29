"""
services/invoicing.py
=====================

Invoice rendering helpers.

  amount_in_words(3000)  ->  "Three Thousand Only"
  render_invoice_pdf(html) -> bytes

Register the words filter once in app.py:

    from services.invoicing import amount_in_words
    app.jinja_env.globals['amount_in_words'] = amount_in_words
"""
from decimal import Decimal, InvalidOperation
import io
import logging

log = logging.getLogger(__name__)

_ONES = ('', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight',
         'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen',
         'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen')
_TENS = ('', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy',
         'Eighty', 'Ninety')


def _under_thousand(n):
    if n == 0:
        return ''
    if n < 20:
        return _ONES[n]
    if n < 100:
        return (_TENS[n // 10] + (' ' + _ONES[n % 10] if n % 10 else '')).strip()
    return (_ONES[n // 100] + ' Hundred'
            + (' ' + _under_thousand(n % 100) if n % 100 else '')).strip()


def amount_in_words(amount, currency='Rupees', include_currency=False):
    """
    Indian numbering system (lakh / crore), matching the wording on your PDF.

        amount_in_words(3000)      -> 'Three Thousand Only'
        amount_in_words(125050.75) -> 'One Lakh Twenty Five Thousand Fifty and
                                       Seventy Five Paise Only'
    """
    try:
        value = Decimal(str(amount or 0))
    except (InvalidOperation, TypeError, ValueError):
        return ''

    negative = value < 0
    value = abs(value).quantize(Decimal('0.01'))
    rupees = int(value)
    paise = int((value - rupees) * 100)

    if rupees == 0 and paise == 0:
        out = 'Zero'
    else:
        parts = []
        crore, rem = divmod(rupees, 10_000_000)
        lakh, rem = divmod(rem, 100_000)
        thousand, rem = divmod(rem, 1_000)

        if crore:
            parts.append(f"{_under_thousand(crore)} Crore")
        if lakh:
            parts.append(f"{_under_thousand(lakh)} Lakh")
        if thousand:
            parts.append(f"{_under_thousand(thousand)} Thousand")
        if rem:
            parts.append(_under_thousand(rem))
        out = ' '.join(p for p in parts if p)

    if paise:
        out += f" and {_under_thousand(paise)} Paise"

    if include_currency:
        out = f"{currency} {out}"
    if negative:
        out = f"Minus {out}"
    return f"{out} Only".strip()


# --------------------------------------------------------------------------- #
#  PDF rendering
# --------------------------------------------------------------------------- #
def render_invoice_pdf(html, base_url=None):
    """
    Convert rendered invoice HTML to PDF bytes.

    Tries WeasyPrint, then xhtml2pdf. Returns None if neither is installed so
    the caller can fall back to serving HTML rather than 500-ing.

        pip install weasyprint          # best output, needs system libs
        pip install xhtml2pdf           # pure python fallback
    """
    try:
        from weasyprint import HTML
        return HTML(string=html, base_url=base_url).write_pdf()
    except ImportError:
        pass
    except Exception:                                    # noqa: BLE001
        log.exception("WeasyPrint failed; trying xhtml2pdf")

    try:
        from xhtml2pdf import pisa
        buf = io.BytesIO()
        result = pisa.CreatePDF(io.StringIO(html), dest=buf)
        if result.err:
            log.error("xhtml2pdf reported %s errors", result.err)
            return None
        return buf.getvalue()
    except ImportError:
        log.warning("No PDF engine installed — serving HTML instead. "
                    "Install weasyprint or xhtml2pdf for PDF output.")
        return None
    except Exception:                                    # noqa: BLE001
        log.exception("xhtml2pdf failed")
        return None
