"""
services/wa_reconcile.py
========================
Finish the story that ``services/messaging.py`` starts.

messaging.py records 'queued' when WabAssist answers ``200 QUEUED``, which is
the honest reading: the gateway has custody, WhatsApp has not confirmed
anything. But WabAssist never calls back. There is no webhook to subscribe to
in its API at all -- delivery state lives behind

    GET /api/v1/messages/status?queueId=...

and only appears if you ask. Nothing in this codebase asked, which is why a
customer can receive a message while the log still reads 'queued' weeks later.

WabAssist keeps TWO statuses and they move independently:

    queueStatus     PENDING | PROCESSING | SENT | FAILED | CANCELLED
    deliveryStatus  SENT | DELIVERED | READ | FAILED

deliveryStatus wins here. It is the one that reflects the handset, which is
what anybody asking "did they get it?" actually means. When queueStatus sticks
at PENDING while deliveryStatus says DELIVERED, that is a WabAssist-side
bookkeeping bug and not something this application can fix -- but it is also
not something it needs to care about, as long as it reads the right field.

Run every few minutes:

    */3 * * * *  cd /path/to/app && python -c "from services.wa_reconcile import run; run()"
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

try:
    import requests
except ImportError:                            # pragma: no cover
    requests = None

log = logging.getLogger(__name__)

#: Only WabAssist exposes this endpoint. Meta Cloud sends webhooks instead, and
#: the generic provider has no status API at all.
SUPPORTED_PROVIDERS = {'webassist', 'wabassist'}

DEFAULT_BASE = 'https://api.wabassist.com'
STATUS_PATH = '/api/v1/messages/status'

#: Rows in these states are finished; never poll them again.
TERMINAL = ('delivered', 'read', 'failed', 'skipped', 'dry-run')

#: Ordering so an out-of-order reply cannot walk a row backwards.
RANK = {'queued': 0, 'pending': 0, 'processing': 1, 'sent': 2,
        'submitted': 2, 'delivered': 3, 'read': 4,
        'failed': 99, 'cancelled': 99}

#: Give up after this many polls. A row that has not settled by now is stuck
#: on WabAssist's side; continuing to ask just burns API calls.
MAX_CHECKS = 40

TIMEOUT = 15


def _pick(payload, *keys, default=None):
    """Response casing is undocumented -- accept either, wrapped or not."""
    for k in keys:
        if isinstance(payload, dict) and payload.get(k) is not None:
            return payload[k]
    inner = payload.get('data') if isinstance(payload, dict) else None
    if isinstance(inner, dict):
        for k in keys:
            if inner.get(k) is not None:
                return inner[k]
    return default


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return None


def _base_url():
    from services.messaging import _setting
    configured = (_setting('wa_api_url') or '').strip()
    if not configured:
        return DEFAULT_BASE
    # wa_api_url holds a SEND endpoint; the status endpoint is a sibling.
    marker = '/api/v1/'
    if marker in configured:
        return configured.split(marker)[0]
    return configured.rstrip('/').rsplit('/api', 1)[0] or DEFAULT_BASE


def run(older_than_minutes: int = 2, limit: int = 400) -> dict:
    """Poll WabAssist for every unfinished row and write back what it says."""
    from models import db, MessageLog
    from services.messaging import _setting

    counts = {'checked': 0, 'advanced': 0, 'still_pending': 0,
              'stuck': 0, 'errors': 0, 'skipped': 0}

    provider = (_setting('wa_provider', 'generic') or '').lower()
    if provider not in SUPPORTED_PROVIDERS:
        log.info('reconcile: provider %r has no status API; nothing to do', provider)
        counts['skipped'] = 1
        return counts

    token = (_setting('wa_api_token') or '').strip()
    if not token or requests is None:
        log.warning('reconcile: no api token, or requests missing')
        counts['skipped'] = 1
        return counts

    url = _base_url().rstrip('/') + STATUS_PATH
    headers = {'Authorization': f'Bearer {token}'}
    cutoff = datetime.utcnow() - timedelta(minutes=older_than_minutes)

    rows = (MessageLog.query
            .filter(~MessageLog.status.in_(TERMINAL))
            .filter(MessageLog.queue_id.isnot(None))
            .filter(MessageLog.created_at <= cutoff)
            .order_by(MessageLog.created_at.asc())
            .limit(limit).all())

    for row in rows:
        if (row.status_checks or 0) >= MAX_CHECKS:
            counts['stuck'] += 1
            continue

        counts['checked'] += 1
        try:
            resp = requests.get(url, params={'queueId': row.queue_id},
                                headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            log.warning('reconcile: lookup failed id=%s: %s', row.id, exc)
            counts['errors'] += 1
            continue

        delivery = str(_pick(payload, 'deliveryStatus', 'delivery_status') or '').lower()
        queue = str(_pick(payload, 'queueStatus', 'queue_status') or '').lower()
        meta_id = _pick(payload, 'metaMessageId', 'meta_message_id')

        row.status_checks = (row.status_checks or 0) + 1
        if meta_id and not row.meta_message_id:
            row.meta_message_id = str(meta_id)[:128]

        best = delivery or queue
        if not best or RANK.get(best, 0) <= RANK.get(row.status, 0):
            counts['still_pending'] += 1
            # Worth saying out loud: Meta took it, the handset may well have it,
            # and WabAssist's own queue row has not moved. Their bug, not ours.
            if meta_id and queue in ('pending', 'processing') and not delivery:
                log.info('reconcile: id=%s accepted by Meta (%s) but WabAssist '
                         'queue still %s after %s checks',
                         row.id, meta_id, queue, row.status_checks)
            continue

        row.status = 'delivered' if best == 'delivered' else (
            'read' if best == 'read' else best)
        row.delivered_at = row.delivered_at or _parse_dt(
            _pick(payload, 'deliveredAt', 'delivered_at'))
        row.read_at = row.read_at or _parse_dt(_pick(payload, 'readAt', 'read_at'))
        if best == 'failed':
            row.error = (str(_pick(payload, 'error', 'failureReason')
                             or 'gateway reported failed'))[:500]
        counts['advanced'] += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    log.info('reconcile: %s', counts)
    return counts


def summary() -> dict:
    """Counts by status -- for a settings screen or a health check."""
    from models import db, MessageLog
    from sqlalchemy import func
    rows = (db.session.query(MessageLog.status, func.count(MessageLog.id))
            .group_by(MessageLog.status).all())
    return {status: n for status, n in rows}
