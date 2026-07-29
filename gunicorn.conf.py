"""Gunicorn configuration for YASH Internet Services CRM."""

import os

# Render sets PORT automatically. Default is 5000 for local development.
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# APScheduler runs inside the Flask application.
# Keep a single worker unless the scheduler is moved to a separate process.
workers = int(os.environ.get("WEB_CONCURRENCY", 1))
threads = int(os.environ.get("WEB_THREADS", 4))
worker_class = "gthread"

# Timeouts
timeout = 120
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")

# Restart workers periodically to reduce memory growth
max_requests = 1000
max_requests_jitter = 100

# Must remain False when using APScheduler
preload_app = False

# Trust Render's reverse proxy
forwarded_allow_ips = "*"
secure_scheme_headers = {
    "X-Forwarded-Proto": "https",
}