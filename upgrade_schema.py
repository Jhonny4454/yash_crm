"""
upgrade_schema.py
=================

Bring an existing UniCRM database up to the current models without a full
Alembic migration chain.

Run it after pulling new code::

    python upgrade_schema.py

It is safe to run repeatedly: every step checks the live schema first and
skips anything already present. Nothing is ever dropped or renamed, so a
half-finished run can simply be re-run.

Why not `db.create_all()` alone? That creates missing *tables* but never adds
a missing *column* to a table that already exists - which is exactly the case
when new fields land on `customers`, `invoices` or `payments`.
"""
import sys

from sqlalchemy import inspect, text

# --------------------------------------------------------------------------- #
#  Columns added since the first release, per table.
#
#  The DDL is written for MySQL, which is what production runs. SQLite (used
#  by the test harness) accepts the same ADD COLUMN syntax for these types.
# --------------------------------------------------------------------------- #
NEW_COLUMNS = {
    'message_templates': [
        # Meta will not deliver free text outside the 24-hour service window,
        # so each row can also name the template Meta approved for it.
        ('meta_template_name', 'VARCHAR(100) NULL'),
        ('meta_language', "VARCHAR(10) NULL DEFAULT 'en'"),
        ('meta_variables', 'VARCHAR(255) NULL'),
    ],
    'users': [
        # Per-user capability list. NULL means unrestricted, which is what
        # every existing row has to keep meaning - see permissions.py.
        ('permissions', 'TEXT NULL'),
    ],
    'customers': [
        ('ip_address', 'VARCHAR(45) NULL'),
        ('ipacct_id', 'VARCHAR(50) NULL'),
        ('service_provider_id', 'INT NULL'),
        ('billing_type', "VARCHAR(10) NULL DEFAULT 'Prepaid'"),
        ('invoice_date', 'DATE NULL'),
        ('latitude', 'VARCHAR(32) NULL'),
        ('longitude', 'VARCHAR(32) NULL'),
        ('wallet_balance', 'DECIMAL(10,2) NOT NULL DEFAULT 0.00'),
    ],
    'invoices': [
        ('discount_reason', 'VARCHAR(100) NULL'),
        # Service period, so a billing run can tell "already billed for August"
        # from "billed in August" and refuse to raise the same bill twice.
        ('period_start', 'DATE NULL'),
        ('period_end', 'DATE NULL'),
    ],
    'customer_plans': [
        # Per-customer plan price. Without it the Customer-vs-Master billing
        # setting had nothing to resolve to and always used the master price.
        ('price', 'DECIMAL(10,2) NULL'),
        # Whether the customer may renew this plan from the portal. Defaults
        # to 1 so every existing plan keeps working exactly as it did.
        ('online_renewal', 'TINYINT(1) NULL DEFAULT 1'),
    ],
    'payments': [
        ('discount_reason', 'VARCHAR(100) NULL'),
    ],
    'audit_logs': [
        ('customer_id', 'INT NULL'),
    ],
}

#: Indexes worth adding by hand. ALTER TABLE ADD INDEX errors if it already
#: exists, so each one is attempted and failures are reported, not fatal.
NEW_INDEXES = [
    ('audit_logs', 'ix_audit_logs_customer_id', 'customer_id'),
    # The billing run's duplicate check filters on these for every candidate.
    ('invoices', 'ix_invoices_period', 'customer_id, period_start'),

    # ---------------------------------------------------------------- #
    # The indexes that decide whether this system works at 10,000
    # customers.
    #
    # Almost every foreign key on the hot tables was unindexed. At a few
    # hundred customers MySQL scans the whole table in under a millisecond
    # and nobody notices; at ten thousand - about thirty thousand invoices
    # and as many payments - the SAME queries become full table scans, and
    # they run several times per screen. Opening one customer meant
    # scanning every invoice ever raised to find their four.
    #
    # These cost a few megabytes and a fraction of a second on every write.
    # They are the difference between a CRM that grows with the business
    # and one that gets slower every month.
    # ---------------------------------------------------------------- #

    # "this customer's bills" - the customer profile, the ledger, the
    # portal, every outstanding calculation.
    ('invoices', 'ix_invoices_customer_id', 'customer_id'),
    # "what is still open" - the dashboard, the dues total, reminders.
    ('invoices', 'ix_invoices_status', 'status'),
    ('invoices', 'ix_invoices_issue_date', 'issue_date'),

    # Payments are joined back to invoices to work out a balance. Without
    # this, every balance in the system is a scan of the payments table.
    ('payments', 'ix_payments_invoice_id', 'invoice_id'),
    ('payments', 'ix_payments_customer_id', 'customer_id'),
    # The authorisation queue filters on exactly these two.
    ('payments', 'ix_payments_status', 'status'),
    ('payments', 'ix_payments_payment_date', 'payment_date'),

    # The expiry board, the renewal queue and the dashboard lifecycle rows
    # all ask "which plans end between these dates, and are they active".
    ('customer_plans', 'ix_customer_plans_customer_id', 'customer_id'),
    ('customer_plans', 'ix_customer_plans_end_date', 'end_date'),
    ('customer_plans', 'ix_customer_plans_status_end', 'status, end_date'),
    # "renewed in the last 7 days", on the dashboard.
    ('customer_plans', 'ix_customer_plans_last_invoice', 'last_invoice_date'),

    # Customer lookup: the search box, the zone filters, the active count.
    ('customers', 'ix_customers_username', 'username'),
    ('customers', 'ix_customers_mobile', 'mobile'),
    ('customers', 'ix_customers_zone', 'zone'),
    ('customers', 'ix_customers_is_active', 'is_active'),

    # The customer's SMS log, and the message history panel.
    ('message_logs', 'ix_message_logs_customer_created',
     'customer_id, created_at'),
]

#: Reasons pre-loaded so the Addon Invoice dropdown is not empty on day one.
#: Matches what the live UniCRM instance carries.
SEED_DISCOUNT_REASONS = [
    ('Power Supply', 'Compensation for a power outage at the customer end'),
    ('wire supply', 'Cable or drop-wire replacement absorbed by us'),
    ('Goodwill', 'Retention or service-recovery gesture'),
    ('Downtime Credit', 'Credit for an outage on our network'),
]


def _existing_columns(inspector, table):
    try:
        return {c['name'] for c in inspector.get_columns(table)}
    except Exception:
        return None  # table itself is missing


def add_missing_columns(db):
    """ALTER TABLE for every column in NEW_COLUMNS not already present."""
    inspector = inspect(db.engine)
    added, skipped = [], []

    for table, columns in NEW_COLUMNS.items():
        present = _existing_columns(inspector, table)
        if present is None:
            print(f"  ! table '{table}' does not exist yet - skipping")
            continue

        for name, ddl in columns:
            if name in present:
                skipped.append(f'{table}.{name}')
                continue
            statement = f'ALTER TABLE `{table}` ADD COLUMN `{name}` {ddl}'
            try:
                db.session.execute(text(statement))
                db.session.commit()
                added.append(f'{table}.{name}')
                print(f'  + {table}.{name}')
            except Exception as exc:
                db.session.rollback()
                print(f'  ! {table}.{name} failed: {str(exc)[:160]}')

    if skipped:
        print(f'  = {len(skipped)} column(s) already present')
    return added


def add_missing_indexes(db):
    """Create the hand-listed indexes, ignoring ones that already exist."""
    inspector = inspect(db.engine)

    for table, index_name, column in NEW_INDEXES:
        try:
            existing = {i['name'] for i in inspector.get_indexes(table)}
        except Exception:
            continue
        if index_name in existing:
            continue
        # Composite indexes are given as "a, b"; quote each part separately or
        # the whole string becomes one backticked identifier and the DDL fails.
        columns = ', '.join(f'`{c.strip()}`' for c in column.split(','))
        try:
            db.session.execute(
                text(f'CREATE INDEX `{index_name}` ON `{table}` ({columns})'))
            db.session.commit()
            print(f'  + index {index_name}')
        except Exception as exc:
            db.session.rollback()
            print(f'  ! index {index_name} skipped: {str(exc)[:120]}')


#: Columns whose set of allowed values grew after the table was created.
#:
#: MySQL stores an ENUM as a fixed list on the column, so widening the Python
#: model is not enough - the live column keeps refusing the new values (or, out
#: of strict mode, quietly stores an empty string). SQLite does not enforce
#: ENUMs at all, so it needs nothing here and the ALTER is skipped.
WIDENED_ENUMS = [
    ('attendance', 'status',
     "ENUM('present','absent','half-day','leave','holiday') "
     "NOT NULL DEFAULT 'present'",
     ('leave', 'holiday')),
]


def widen_enums(db):
    """Grow ENUM columns that gained values, on databases that enforce them."""
    if db.engine.dialect.name not in ('mysql', 'mariadb'):
        print('  = enum widening not needed on '
              f'{db.engine.dialect.name}')
        return

    inspector = inspect(db.engine)
    for table, column, ddl, required in WIDENED_ENUMS:
        try:
            cols = {c['name']: c for c in inspector.get_columns(table)}
        except Exception:
            continue
        current = str(cols.get(column, {}).get('type', '')).lower()
        if all(f"'{value}'" in current for value in required):
            print(f'  = {table}.{column} already accepts {", ".join(required)}')
            continue
        try:
            db.session.execute(
                text(f'ALTER TABLE `{table}` MODIFY COLUMN `{column}` {ddl}'))
            db.session.commit()
            print(f'  + widened {table}.{column}')
        except Exception as exc:
            db.session.rollback()
            print(f'  ! {table}.{column} not widened: {str(exc)[:120]}')


def seed_discount_reasons(db):
    """Populate Discount Master, but never overwrite what is already there."""
    from models import DiscountReason

    created = 0
    for name, description in SEED_DISCOUNT_REASONS:
        if DiscountReason.query.filter_by(name=name).first():
            continue
        db.session.add(DiscountReason(name=name, description=description,
                                      is_active=True))
        created += 1

    if created:
        db.session.commit()
        print(f'  + {created} discount reason(s) seeded')
    else:
        print('  = discount reasons already present')
    return created


def seed_settings(db):
    """
    Create any Setting row listed in SETTING_DEFAULTS that does not exist yet.

    Existing values are never touched - this fills gaps, it does not reset
    anything the operator has already configured. New keys (the SMTP block,
    for instance) need a row to exist before the Settings screen can show a
    field for them, so without this the feature is unconfigurable.
    """
    from models_ext import Setting, SETTING_DEFAULTS

    existing = {row.key for row in Setting.query.all()}
    created = 0
    for key, value, value_type in SETTING_DEFAULTS:
        if key in existing:
            continue
        db.session.add(Setting(key=key, value=value, value_type=value_type))
        created += 1

    if created:
        db.session.commit()
        print(f'  + {created} setting(s) created')
    else:
        print('  = all settings already present')
    return created


def main():
    from app import app
    from models import db

    with app.app_context():
        print('1. Creating any missing tables...')
        db.create_all()
        print('   done')

        print('2. Adding missing columns...')
        add_missing_columns(db)
        add_missing_indexes(db)
        widen_enums(db)

        print('3. Seeding Discount Master...')
        try:
            seed_discount_reasons(db)
        except Exception as exc:
            db.session.rollback()
            print(f'  ! seeding failed: {str(exc)[:200]}')

        print('4. Seeding settings...')
        try:
            seed_settings(db)
        except Exception as exc:
            db.session.rollback()
            print(f'  ! settings seeding skipped: {str(exc)[:200]}')

        print('5. Reserving usernames already in use...')
        try:
            # db.create_all() above makes the table; this fills it. Without the
            # backfill the ledger starts empty, so every username already
            # issued would look reusable the moment its customer row went away
            # - which is the exact thing the ledger exists to prevent.
            from services.usernames import backfill_existing
            added = backfill_existing()
            print(f'   {added} username(s) reserved')
        except Exception as exc:
            db.session.rollback()
            print(f'  ! username backfill skipped: {str(exc)[:200]}')

        print('6. Seeding message templates...')
        try:
            from services.messaging import seed_default_templates
            seed_default_templates()
            print('   done')
        except Exception as exc:
            db.session.rollback()
            print(f'  ! template seeding skipped: {str(exc)[:200]}')

        # Step 2 above is what adds meta_template_name / meta_language /
        # meta_variables, so this can only run once those columns exist -
        # which is why it lives here rather than being something else to
        # remember to run.
        print('7. Linking Meta-approved WhatsApp templates...')
        try:
            from link_meta_templates import link
            link()
        except Exception as exc:
            db.session.rollback()
            print(f'  ! template linking skipped: {str(exc)[:200]}')

    print('\nSchema upgrade complete. Restart the Flask app.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
