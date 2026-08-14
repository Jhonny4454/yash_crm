"""
services/outbox.py
==================

Bulk sending, off the web request.

Why this exists
---------------
``POST /messages/bulk`` used to loop over every recipient inside the request
that started it. One message costs a handful of database queries plus an HTTP
call to the gateway that is allowed up to eight seconds. Two hundred
recipients is therefore a request that runs for minutes:

* the browser gives up long before it finishes and shows a failure, while the
  send carries on regardless - so the operator presses it again;
* the thread serving it is unavailable to everybody else, and the development
  server has few of them, so every other screen in the CRM goes slow at
  exactly the moment somebody is doing the most visible thing in the product.

That second effect is the one that gets reported as "the whole site is slow",
and no amount of tuning the other screens fixes it.

So the loop runs on a background thread and the request returns immediately
with a job id. The durable record of what happened is where it always was -
``message_logs``, one row per attempt. The job registry below is only for
showing progress while it runs.

Deliberately in-memory
----------------------
Job progress is not written to the database. It changes many times a second,
it is worthless ten minutes later, and persisting it would add write load to
the very thing being relieved. The consequence is honest and worth knowing:
across multiple worker processes only the worker running the job can report
its progress, and a restart forgets it. The message log is unaffected.
"""
import threading
import time
import uuid

_lock = threading.Lock()
_jobs = {}

#: Finished jobs are kept so the screen can show the outcome, then dropped.
KEEP_FINISHED_SECONDS = 30 * 60


def _prune(now):
    """Drop old finished jobs. Called under the lock."""
    for job_id, job in list(_jobs.items()):
        finished = job.get('finished_at')
        if finished and now - finished > KEEP_FINISHED_SECONDS:
            _jobs.pop(job_id, None)


def active_job(kind=None):
    """The running job of this kind, if there is one.

    Used to refuse a second bulk run while one is in flight. Two overlapping
    runs would message everybody twice, and the operator has no way to tell
    from the screen that the first is still going.
    """
    with _lock:
        for job in _jobs.values():
            if job['finished_at'] is None and (kind is None or job['kind'] == kind):
                return dict(job)
    return None


def get_job(job_id):
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def start(app, kind, label, items, handler):
    """Run ``handler(item)`` for every item, on a thread. Returns the job.

    ``handler`` returns a truthy value for a successful hand-off and falsy
    otherwise; anything it raises counts as a failure and does not stop the
    run. One bad phone number must not cancel the other four hundred.
    """
    job_id = uuid.uuid4().hex[:12]
    now = time.time()

    with _lock:
        _prune(now)
        _jobs[job_id] = {
            'id': job_id, 'kind': kind, 'label': label,
            'total': len(items), 'done': 0, 'sent': 0, 'failed': 0,
            'started_at': now, 'finished_at': None, 'error': '',
        }

    def run():
        # A thread has no application context of its own, and every database
        # read inside the handler needs one.
        with app.app_context():
            try:
                for item in items:
                    ok = False
                    try:
                        ok = bool(handler(item))
                    except Exception:
                        ok = False
                    with _lock:
                        job = _jobs.get(job_id)
                        if job is None:
                            return            # pruned or cancelled
                        job['done'] += 1
                        job['sent' if ok else 'failed'] += 1
            except Exception as exc:          # pragma: no cover - belt and braces
                with _lock:
                    if job_id in _jobs:
                        _jobs[job_id]['error'] = f'{type(exc).__name__}: {exc}'[:200]
            finally:
                # The session belongs to this thread; leaving it open holds a
                # pooled connection for as long as the process lives.
                try:
                    from models import db
                    db.session.remove()
                except Exception:
                    pass
                with _lock:
                    if job_id in _jobs:
                        _jobs[job_id]['finished_at'] = time.time()

    thread = threading.Thread(target=run, name=f'outbox-{kind}-{job_id}',
                              daemon=True)
    thread.start()
    return get_job(job_id)
