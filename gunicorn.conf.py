"""Gunicorn configuration for YASH Internet Services CRM."""
<<<<<<< HEAD
import os

# ── Binding ─────────────────────────────────────────────────────────────────
# Render sets PORT automatically; default 5000 for local dev
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# ── Workers ──────────────────────────────────────────────────────────────────
# KEEP AT 1 — APScheduler runs inside the Flask process.
# More workers = duplicate auto-invoices and duplicate WhatsApp reminders.
=======
import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# APScheduler runs inside the app, so keep a single worker unless you move the
# scheduler to a separate process. More workers = duplicate scheduled jobs.
>>>>>>> eaddab7a9b6609413ac527248b3d5a68cc7057f5
workers = int(os.environ.get('WEB_CONCURRENCY', 1))
threads = int(os.environ.get('WEB_THREADS', 4))
worker_class = 'gthread'

<<<<<<< HEAD
# ── Timeouts ─────────────────────────────────────────────────────────────────
# 120 s covers slow first boot on Render free tier (cold start + db.create_all)
=======
>>>>>>> eaddab7a9b6609413ac527248b3d5a68cc7057f5
timeout = 120
graceful_timeout = 30
keepalive = 5

<<<<<<< HEAD
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
=======
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('LOG_LEVEL', 'info')

max_requests = 1000
max_requests_jitter = 100
preload_app = False
>>>>>>> eaddab7a9b6609413ac527248b3d5a68cc7057f5
