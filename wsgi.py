"""
WSGI entry point for production servers.

    gunicorn -c gunicorn.conf.py wsgi:application

The scheduler is started here (not in app.py's __main__ block) so it runs
under gunicorn too, and only in the first worker to avoid duplicate jobs.
"""
import os

from app import app as application, init_database

# Create tables and seed the admin account on first boot.
init_database(application)

if __name__ == '__main__':
    application.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
