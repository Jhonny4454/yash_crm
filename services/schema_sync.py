"""
services/schema_sync.py
=======================

Bring a live database up to the current SQLAlchemy models without a manual
migration step.

This only ever ADDS: missing tables via ``db.create_all()``, then missing
columns via ``ALTER TABLE ... ADD COLUMN``. It never drops a table, drops a
column, or rewrites a row, so it is safe to call on every boot.

``migrate.py`` uses it for the explicit, verbose run; ``app.init_database()``
uses it so a fresh deploy picks up new columns on its own.

Works against SQLite and MySQL/MariaDB.
"""
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError


def column_ddl(column, dialect):
    """Render an ADD COLUMN fragment that is safe on a populated table.

    NOT NULL is deliberately dropped: existing rows have no value for a new
    column, so forcing NOT NULL would fail. A DEFAULT is emitted when the
    model declares a simple scalar one.
    """
    try:
        type_sql = column.type.compile(dialect=dialect)
    except Exception:                                   # noqa: BLE001
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


#: Columns that started NOT NULL and must now accept NULL on databases that
#: already hold the old shape (fresh databases get the model's definition from
#: CREATE TABLE, so they need nothing here). The bill can be deleted while the
#: payment rows survive only if the child FK can be nulled, so the ORM can
#: detach them instead of failing the delete.
NULLIFY_COLUMNS = {
    'payments': ('invoice_id', 'Integer'),
}


def nullify_columns(db, *, dry_run=False, log=None):
    """MODIFY any listed column that is still NOT NULL but should be nullable."""
    say = log or (lambda *a, **k: None)
    engine = db.engine
    dialect = engine.dialect

    if dialect.name not in ('mysql', 'mariadb'):
        # SQLite has no ALTER COLUMN; a fresh SQLite DB gets the new model's
        # nullable definition from CREATE TABLE.
        return []

    changed = []
    inspector = inspect(engine)
    for table_name, (column_name, type_name) in NULLIFY_COLUMNS.items():
        try:
            live = {c['name']: c for c in inspector.get_columns(table_name)}
        except Exception:                                   # noqa: BLE001
            continue
        column = live.get(column_name)
        if column is None or column.get('nullable'):
            continue
        stmt = (f'ALTER TABLE `{table_name}` '
                f'MODIFY COLUMN `{column_name}` {type_name} NULL')
        if dry_run:
            say(f'would run: {stmt}')
            changed.append((table_name, column_name))
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            changed.append((table_name, column_name))
            say(f'made {table_name}.{column_name} nullable')
        except SQLAlchemyError as exc:
            msg = str(getattr(exc, 'orig', exc))
            say(f'FAILED making {table_name}.{column_name} nullable: {msg}')
    return changed


def sync_schema(db, *, dry_run=False, log=None):
    """
    Add whatever the models declare and the database is missing.

    Must be called inside an application context. Returns a dict:

        {'created_tables': [...], 'added_columns': [('table', 'col'), ...],
         'failed': [('table', 'col', 'message'), ...]}
    """
    say = log or (lambda *a, **k: None)
    engine = db.engine
    dialect = engine.dialect

    result = {'created_tables': [], 'added_columns': [], 'failed': []}

    # ---- 1. missing tables ------------------------------------------------ #
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    to_create = sorted(set(db.metadata.tables) - existing)
    if to_create:
        result['created_tables'] = to_create
        say(f"tables to create: {', '.join(to_create)}")
        if not dry_run:
            db.create_all()

    # ---- 2. missing columns ----------------------------------------------- #
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    for table_name, table in db.metadata.tables.items():
        if table_name not in existing:
            continue
        live = {c['name'] for c in inspector.get_columns(table_name)}
        missing = [c for c in table.columns if c.name not in live]
        for column in missing:
            stmt = (f"ALTER TABLE {table_name} "
                    f"ADD COLUMN {column_ddl(column, dialect)}")
            if dry_run:
                say(f"would run: {stmt}")
                result['added_columns'].append((table_name, column.name))
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt))
                result['added_columns'].append((table_name, column.name))
                say(f"added {table_name}.{column.name}")
            except SQLAlchemyError as exc:
                msg = str(getattr(exc, 'orig', exc))
                result['failed'].append((table_name, column.name, msg))
                say(f"FAILED {table_name}.{column.name}: {msg}")

    # ---- 3. nullability changes ------------------------------------------- #
    nullify_columns(db, dry_run=dry_run, log=say)

    return result
