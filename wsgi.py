"""
WSGI entry point — used by gunicorn and Render.

    gunicorn -c gunicorn.conf.py wsgi:application
"""
import os

from app import app as application, init_database

# Create all tables and seed the default admin account on first boot.
# This is safe to call on every startup: create_all() is a no-op when
# the schema already matches.
init_database(application)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'production') != 'production'
    application.run(host='0.0.0.0', port=port, debug=debug)
