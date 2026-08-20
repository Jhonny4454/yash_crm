# The `queued` bug — cause and fix

## What is actually happening

Nothing is broken in the send path. The message goes out, Meta carries it, the
customer reads it. The log is simply never told.

Traced through your code:

1. `services/messaging.py:599` posts to WabAssist.
2. WabAssist answers `200 {"status":"QUEUED","success":true}`.
3. `interpret_response()` (line 705) classifies that as `'queued'` — **and it is
   right to**. The docstring explains why: recording gateway custody as `'sent'`
   is how the CRM once reported success for messages nobody received.
4. `_log()` (line 539) writes `MessageLog(status='queued')`.
5. …and that is the end of it.

Step 5 is the bug. `grep` across the repo finds **zero** references to
`messages/status`, `queueId`, `requestId`, `reconcile` or `poll`. WabAssist has
no webhook to subscribe to — its API collection has no such endpoint — so
delivery state exists only behind `GET /api/v1/messages/status`, and nothing
ever asks.

`'queued'` is therefore terminal in practice. Not by design; by omission.

There is a second, quieter problem. `interpret_response()` reads the response
body for its status word and discards the rest — including `queueId`, the only
key the status endpoint accepts. So even after adding a poller, existing rows
have nothing to look up.

## What WabAssist actually tracks

Two statuses, moving independently:

```
queueStatus     PENDING | PROCESSING | SENT | FAILED | CANCELLED
deliveryStatus  SENT | DELIVERED | READ | FAILED
```

`deliveryStatus` is the one that reflects the handset. If `queueStatus` sticks
at `PENDING` while `deliveryStatus` reads `DELIVERED`, that is WabAssist's own
bookkeeping failing — not yours, and not something you need to care about once
you read the right field.

## The fix, in three parts

### 1. Schema — `migrate_message_status.py`

Adds `queue_id` (indexed), `request_id`, `meta_message_id`, `delivered_at`,
`read_at`, `status_checks` to `message_logs`. Idempotent; reads the database
from the app config, so no credentials in the file.

```
python migrate_message_status.py
```

Also add the columns to the model so SQLAlchemy knows about them —
`models.py`, in `class MessageLog` after `error`:

```python
    queue_id        = db.Column(db.String(64), index=True)
    request_id      = db.Column(db.String(64))
    meta_message_id = db.Column(db.String(128))
    delivered_at    = db.Column(db.DateTime)
    read_at         = db.Column(db.DateTime)
    status_checks   = db.Column(db.Integer, default=0)
```

And widen the comment on line 912, which is now out of date:

```python
    #: sent | queued | delivered | read | failed | skipped | dry-run
```

### 2. Capture the ids — `services/messaging.py`

**a.** `SendResult` (line 512) — two more slots:

```python
class SendResult:
    __slots__ = ('ok', 'status', 'detail', 'phone', 'body',
                 'queue_id', 'request_id')

    def __init__(self, ok, status, detail='', phone='', body='',
                 queue_id=None, request_id=None):
        self.ok = ok
        self.status = status
        self.detail = (detail or '')[:500]
        self.phone = phone
        self.body = body
        self.queue_id = queue_id
        self.request_id = request_id
```

**b.** New helper, next to `interpret_response`. Deliberately separate: that
function returns a 3-tuple two call sites unpack, and widening it would break
line 1178.

```python
def extract_gateway_ids(text):
    """The ids WabAssist returns alongside QUEUED.

    These are the only keys GET /api/v1/messages/status accepts. Without them
    a row can never be reconciled, which is how 'queued' became permanent.
    """
    body = (text or '')[:2000]
    if not body.strip().startswith('{'):
        return None, None
    try:
        payload = json.loads(body) or {}
    except Exception:
        return None, None
    inner = payload.get('data') if isinstance(payload.get('data'), dict) else {}

    def pick(*keys):
        for key in keys:
            for source in (payload, inner):
                if isinstance(source, dict) and source.get(key):
                    return str(source[key])[:64]
        return None

    return pick('queueId', 'queue_id'), pick('requestId', 'request_id')
```

**c.** The send site (line 601). Two changed lines:

```python
        ok, status, detail = interpret_response(resp.status_code, resp.text)
        queue_id, request_id = extract_gateway_ids(resp.text)      # <-- add
        ...
        res = SendResult(ok, status, detail, msisdn, message,      # <-- extend
                         queue_id=queue_id, request_id=request_id)
```

**d.** `_log()` (line 543) — carry them onto the row:

```python
        db.session.add(MessageLog(
            customer_id=customer_id,
            phone=phone or '',
            channel=channel,
            template_type=template_type,
            body=body or '',
            status=result.status,
            error=('' if result.ok else result.detail)[:500],
            queue_id=getattr(result, 'queue_id', None),            # <-- add
            request_id=getattr(result, 'request_id', None),        # <-- add
        ))
```

`getattr` rather than direct access so an older `SendResult` from anywhere else
in the codebase cannot raise here — `_log` promises never to raise.

### 3. Poll — `services/wa_reconcile.py`

Drop the file into `services/`. Then, every few minutes:

```
*/3 * * * *  cd /path/to/app && python -c "from services.wa_reconcile import run; run()"
```

It skips providers with no status API, walks non-terminal rows that have a
`queue_id`, prefers `deliveryStatus` over `queueStatus`, refuses to walk a row
backwards, and gives up after 40 checks so a permanently stuck row stops
burning API calls.

`summary()` returns counts by status if you want it on a settings screen.

## Rows already at `queued`

They predate `queue_id` and cannot be looked up — nothing can be done for them.
They will sit at `queued` forever. Everything sent after the patch reconciles
within a few minutes.

## Unrelated, but you should know

`migrate_locations.py` has your **production MySQL host, port, root user and
password in plaintext** at lines 4–7, in a file that appears to be tracked in
git. Anyone with repo access — or anyone who ever gets a copy of it — has full
control of that database.

Worth doing, in order: rotate that password, move the credentials to `.env`
(which `.gitignore` already covers), and, since rotating does not remove it
from history, either purge it with `git filter-repo` or treat the old password
as permanently compromised. `migrate_csv_import.py`, `migrate_building_locality.py`
and the other `migrate_*.py` scripts are worth checking for the same pattern.
