"""Gunicorn configuration for YASH Internet Services CRM."""
import os

# ── Binding ─────────────────────────────────────────────────────────────────
# Render sets PORT automatically; default 5000 for local dev
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# ── Workers ──────────────────────────────────────────────────────────────────
# KEEP AT 1 — APScheduler runs inside the Flask process.
# More workers = duplicate auto-invoices and duplicate WhatsApp reminders.
workers = int(os.environ.get('WEB_CONCURRENCY', 1))
threads = int(os.environ.get('WEB_THREADS', 4))
worker_class = 'gthread'

# ── Timeouts ─────────────────────────────────────────────────────────────────
# 120 s covers slow first boot on Render free tier (cold start + db.create_all)
timeout = 120
graceful_timeout = 30
keepalive = 5

# ── Logging ──────────────────────────────────────────────────────────────────
accesslog = '-'   # stdout → visible in Render logs
errorlog  = '-'
loglevel  = os.environ.get('LOG_LEVEL', 'info')

# ── Request recycling (memory leak protection) ───────────────────────────────
max_requests        = 1000
max_requests_jitter = 100

# ── IMPORTANT: must be False when APScheduler is used ────────────────────────
# preload_app = True would start the scheduler BEFORE workers fork,
# causing the scheduler thread to die silently and never fire jobs.
preload_app = False

# ── Render / Railway: forward the real client IP ─────────────────────────────
# The app's ProxyFix middleware already handles this, but gunicorn
# needs to trust the proxy header too.
forwarded_allow_ips = '*'
secure_scheme_headers = {'X-Forwarded-Proto': 'https'}
