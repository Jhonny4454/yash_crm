"""Gunicorn configuration for YASH Internet Services CRM."""
import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# APScheduler runs inside the app, so keep a single worker unless you move the
# scheduler to a separate process. More workers = duplicate scheduled jobs.
workers = int(os.environ.get('WEB_CONCURRENCY', 1))
threads = int(os.environ.get('WEB_THREADS', 4))
worker_class = 'gthread'

timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('LOG_LEVEL', 'info')

max_requests = 1000
max_requests_jitter = 100
preload_app = False
