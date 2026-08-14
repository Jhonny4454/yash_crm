"""
round_amounts.py
================

Round every stored money value to whole rupees.

    python round_amounts.py            # show what would change
    python round_amounts.py --apply    # write it

Why this is separate from the display fix
-----------------------------------------
The API now rounds every amount on the way out, so the screens already show
whole rupees. The numbers still SITTING in the database may carry paise -
usually from a tax or proration calculation years ago - and while that is
invisible, it is not harmless: a column of six invoices each holding .855
displays as six whole rupees that do not add up to the displayed total.

This makes the stored values agree with what everyone can see.

It changes real financial records, which is why it is a separate, deliberate
command with a dry run, and not something that happens quietly at startup.
Take a backup first (Settings > Backup, or mysqldump). Nothing here can be
undone from inside the application.

ROUND_HALF_UP throughout, matching blueprints/api/utils.money(), so a value
rounds the same way here as it does on screen.
"""
import argparse
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

#: (model name, column names). Only columns that hold money.
MONEY_COLUMNS = [
    ('Invoice', ('total_amount', 'tax_amount', 'discount_amount')),
    ('Payment', ('amount', 'discount_amount')),
    ('Plan', ('price_monthly', 'isp_amount')),
    ('CustomerPlan', ('price',)),
    ('Customer', ('discount_amount', 'wallet_balance')),
]


def whole(value):
    """The rupee value, rounded half-up. None stays None."""
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='write the changes (default is a dry run)')
    args = parser.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app import app
    from models import db
    import models

    total_changed = 0

    with app.app_context():
        for model_name, columns in MONEY_COLUMNS:
            model = getattr(models, model_name, None)
            if model is None:
                print(f'  ? {model_name} not found - skipped')
                continue

            changed = 0
            examples = []
            try:
                rows = model.query.all()
            except Exception as exc:
                print(f'  ! {model_name} could not be read: {str(exc)[:120]}')
                continue

            for row in rows:
                for column in columns:
                    if not hasattr(row, column):
                        continue
                    current = getattr(row, column)
                    if current is None:
                        continue
                    rounded = whole(current)
                    if rounded is None or Decimal(str(current)) == rounded:
                        continue
                    if len(examples) < 3:
                        examples.append(f'{model_name}#{row.id}.{column} '
                                        f'{current} -> {rounded}')
                    if args.apply:
                        setattr(row, column, rounded)
                    changed += 1

            if changed:
                total_changed += changed
                verb = 'rounded' if args.apply else 'would round'
                print(f'  {verb} {changed} value(s) on {model_name}')
                for line in examples:
                    print(f'      {line}')
            else:
                print(f'  = {model_name} already whole')

        if args.apply and total_changed:
            db.session.commit()

    print()
    if not total_changed:
        print('Every stored amount is already a whole number.')
    elif args.apply:
        print(f'{total_changed} value(s) rounded. Restart Flask.')
    else:
        print(f'{total_changed} value(s) would change. '
              f'Take a backup, then re-run with --apply.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
