"""
<<<<<<< HEAD
WSGI entry point — used by gunicorn and Render.

    gunicorn -c gunicorn.conf.py wsgi:application
=======
WSGI entry point for production servers.

    gunicorn -c gunicorn.conf.py wsgi:application

The scheduler is started here (not in app.py's __main__ block) so it runs
under gunicorn too, and only in the first worker to avoid duplicate jobs.
>>>>>>> eaddab7a9b6609413ac527248b3d5a68cc7057f5
"""
import os

from app import app as application, init_database

<<<<<<< HEAD
# Create all tables and seed the default admin account on first boot.
# This is safe to call on every startup: create_all() is a no-op when
# the schema already matches.
init_database(application)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'production') != 'production'
    application.run(host='0.0.0.0', port=port, debug=debug)
=======
# Create tables and seed the admin account on first boot.
init_database(application)

if __name__ == '__main__':
    application.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
>>>>>>> eaddab7a9b6609413ac527248b3d5a68cc7057f5
