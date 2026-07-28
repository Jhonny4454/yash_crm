#!/usr/bin/env python3
"""
migrate.py — bring an existing YASH CRM database up to the current schema.

This compares the live database against the SQLAlchemy models and adds
whatever is missing. It is generic: any column you add to models.py later is
picked up automatically, so you do not have to maintain a hand-written list.

Safe to run repeatedly. It only ADDS tables and columns. It never drops a
table, drops a column, or rewrites existing rows.

    python migrate.py            # uses DATABASE_URL from .env
    python migrate.py --dry-run  # show what would change, change nothing

Works against SQLite and MySQL/MariaDB.
"""
import os
import sys

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault('SECRET_KEY', 'migration-only-not-used-at-runtime')

from app import app                                   # noqa: E402
from models import db                                 # noqa: E402
import models_ext                                     # noqa: E402,F401

DRY_RUN = '--dry-run' in sys.argv


def column_ddl(column, dialect):
    """Render an ADD COLUMN fragment that is safe on a populated table.

    NOT NULL is deliberately dropped: existing rows have no value for a new
    column, so forcing NOT NULL would fail. A DEFAULT is emitted when the
    model declares a simple scalar one.
    """
    try:
        type_sql = column.type.compile(dialect=dialect)
    except Exception:                                  # noqa: BLE001
        type_sql = 'TEXT'

    ddl = f"{column.name} {type_sql}"

    default = getattr(column, 'default', None)
    if default is not None and getattr(default, 'is_scalar', False):
        value = default.arg
        if isinstance(value, bool):
            ddl += f" DEFAULT {1 if value else 0}"
        elif isinstance(value, (int, float)):
            ddl += f" DEFAULT {value}"
        elif isinstance(value, str):
            escaped = value.replace("'", "''")
            ddl += f" DEFAULT '{escaped}'"
    return ddl


def main():
    with app.app_context():
        engine = db.engine
        dialect = engine.dialect
        print(f"Database : {engine.url.render_as_string(hide_password=True)}")
        print(f"Dialect  : {dialect.name}")
        if DRY_RUN:
            print("Mode     : DRY RUN (no changes will be written)")
        print()

        # ---- 1. create missing tables ----------------------------------- #
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        model_tables = set(db.metadata.tables)
        to_create = sorted(model_tables - existing_tables)

        if to_create:
            print(f"Missing tables ({len(to_create)}):")
            for t in to_create:
                print(f"  + {t}")
            if not DRY_RUN:
                db.create_all()
                print("  -> created")
        else:
            print("Tables   : all present")
        print()

        # ---- 2. add missing columns ------------------------------------- #
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        added, failed = 0, []

        for table_name, table in db.metadata.tables.items():
            if table_name not in existing_tables:
                continue
            live_cols = {c['name'] for c in inspector.get_columns(table_name)}
            missing = [c for c in table.columns if c.name not in live_cols]
            if not missing:
                continue

            print(f"{table_name}: {len(missing)} missing column(s)")
            for column in missing:
                frag = column_ddl(column, dialect)
                stmt = f"ALTER TABLE {table_name} ADD COLUMN {frag}"
                if DRY_RUN:
                    print(f"  ~ {stmt}")
                    continue
                try:
                    with engine.begin() as conn:
                        conn.execute(text(stmt))
                    print(f"  + {column.name}")
                    added += 1
                except SQLAlchemyError as exc:
                    msg = str(exc.orig if hasattr(exc, 'orig') else exc)
                    print(f"  ! {column.name} FAILED: {msg}")
                    failed.append((table_name, column.name, msg))

        if not added and not failed and not DRY_RUN:
            print("Columns  : all present")
        print()

        if DRY_RUN:
            print("Dry run complete. Re-run without --dry-run to apply.")
            return 0

        # ---- 3. backfill stock rows so every product is billable -------- #
        from models import Product, Stock
        created = 0
        for p in Product.query.all():
            if not Stock.query.filter_by(product_id=p.id).first():
                db.session.add(Stock(product_id=p.id, quantity=0))
                created += 1
        if created:
            db.session.commit()
            print(f"Stock    : backfilled {created} row(s)")

        # ---- 4. seed settings ------------------------------------------- #
        try:
            from blueprints.settings_bp import seed_settings
            n = seed_settings()
            print(f"Settings : seeded {n} row(s)")
        except Exception as exc:                        # noqa: BLE001
            print(f"Settings : could not seed ({exc})")

        # ---- 5. ensure admin exists ------------------------------------- #
        try:
            from app import init_database
            init_database(app)
            print("Admin    : verified")
        except Exception as exc:                        # noqa: BLE001
            print(f"Admin    : could not verify ({exc})")

        print()
        if failed:
            print(f"Completed with {len(failed)} problem(s):")
            for t, c, m in failed:
                print(f"  {t}.{c}: {m}")
            return 1
        print("Migration complete.")
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:                            # noqa: BLE001
        print(f"\nMigration FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
