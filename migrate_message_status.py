"""
migrate_message_status.py
=========================
Add the columns that let a 'queued' message log row ever finish.

Why this is needed
------------------
``services/messaging.py`` classifies WabAssist's ``200 {"status":"QUEUED"}``
as its own status, on purpose - gateway custody is not delivery, and calling
it 'sent' was how the CRM once reported success for messages nobody received.

That half is right. The missing half is that nothing ever revisits the row.
WabAssist does not call back; its status lives behind
``GET /api/v1/messages/status``, which has to be polled. And the send response
- the only place the queue id appears - is read for its status word and then
thrown away, so even a poller would have no key to ask about.

So 'queued' is terminal in practice. The customer gets the message and the log
never moves. This migration adds somewhere to keep the key, and somewhere to
record the answer.

Safe to run more than once; every step checks first. Reads the database from
the app's own config, so it works against SQLite locally and MySQL in
production without credentials in the file.

    python migrate_message_status.py
"""
from sqlalchemy import inspect, text

COLUMNS = {
    # The lookup key. Without it a row can never be reconciled -- index it,
    # because the reconcile pass filters on it every few minutes.
    'queue_id':        'VARCHAR(64)',
    'request_id':      'VARCHAR(64)',
    # Meta's own id, useful when arguing a case with WabAssist support.
    'meta_message_id': 'VARCHAR(128)',
    'delivered_at':    'DATETIME',
    'read_at':         'DATETIME',
    # Number of polls, so a row that never settles is visible rather than
    # quietly re-polled forever.
    'status_checks':   'INTEGER',
}

INDEXES = {
    'ix_message_logs_queue_id': 'queue_id',
}


def main():
    from app import app                      # noqa: E402  (app factory / module app)
    from models import db

    with app.app_context():
        engine = db.engine
        insp = inspect(engine)

        if 'message_logs' not in insp.get_table_names():
            print('message_logs does not exist yet -- run the app once first.')
            return 1

        existing = {c['name'] for c in insp.get_columns('message_logs')}
        added = []

        with engine.begin() as conn:
            for name, ddl_type in COLUMNS.items():
                if name in existing:
                    continue
                conn.execute(text(
                    f'ALTER TABLE message_logs ADD COLUMN {name} {ddl_type}'))
                added.append(name)

        # Index creation is separate: MySQL rejects it in the same statement,
        # and SQLite needs the column to exist first.
        made = []
        existing_ix = {i['name'] for i in insp.get_indexes('message_logs')}
        with engine.begin() as conn:
            for ix_name, col in INDEXES.items():
                if ix_name in existing_ix or col not in (existing | set(added)):
                    continue
                try:
                    conn.execute(text(
                        f'CREATE INDEX {ix_name} ON message_logs ({col})'))
                    made.append(ix_name)
                except Exception as exc:
                    print(f'  index {ix_name}: skipped ({type(exc).__name__})')

        print(f'columns added : {added or "none (already present)"}')
        print(f'indexes added : {made or "none (already present)"}')

        stuck = conn_count(db, "status = 'queued'")
        print(f"\nrows currently at 'queued': {stuck}")
        if stuck:
            print('These predate the queue_id column, so they cannot be looked '
                  'up and will stay as they are. New sends will reconcile.')
            print('\nNext: add the capture patch to services/messaging.py, then')
            print('      python -c "from services.wa_reconcile import run; run()"')
    return 0


def conn_count(db, where):
    try:
        return db.session.execute(
            text(f'SELECT COUNT(*) FROM message_logs WHERE {where}')).scalar() or 0
    except Exception:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
