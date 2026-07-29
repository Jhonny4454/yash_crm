import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


def _flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


IS_PRODUCTION = os.environ.get('FLASK_ENV', '').lower() == 'production'


class Config:
    # ---------------------------------------------------------------- core --
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-me'

    # Database URL – supports SQLite, PostgreSQL, and MySQL.
    # Normalises common prefix variants for SQLAlchemy 2.x compatibility.
    _db_url = os.environ.get('DATABASE_URL') or 'sqlite:///app.db'
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql+psycopg2://', 1)
    elif _db_url.startswith('mysql://'):
        _db_url = _db_url.replace('mysql://', 'mysql+pymysql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False

    # --------------------------------------------------- connection pooling --
    # Recycle connections before the managed database drops them, and check they
    # are alive before handing them to a request.
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
    }
    if not _db_url.startswith('sqlite'):
        SQLALCHEMY_ENGINE_OPTIONS.update({
            'pool_size': int(os.environ.get('DB_POOL_SIZE', 5)),
            'max_overflow': int(os.environ.get('DB_MAX_OVERFLOW', 5)),
            'pool_timeout': 20,
        })

    # ------------------------------------------------------------ sessions --
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = IS_PRODUCTION
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    PREFERRED_URL_SCHEME = 'https'
    WTF_CSRF_TIME_LIMIT = 3600
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024      # 16 MB uploads

    # ------------------------------------------------------- static assets --
    # Long cache lifetime for /static so repeat visits don't re-download the
    # CSS/JS on every page - a major cause of "slow on mobile".
    SEND_FILE_MAX_AGE_DEFAULT = timedelta(
        seconds=int(os.environ.get('STATIC_CACHE_SECONDS', 60 * 60 * 24 * 7)))
    TEMPLATES_AUTO_RELOAD = not IS_PRODUCTION

    # --------------------------------------------- Cashfree payment gateway --
    # Blank = online payment disabled; the portal hides the Pay button.
    CASHFREE_APP_ID = os.environ.get('CASHFREE_APP_ID', '')
    CASHFREE_SECRET_KEY = os.environ.get('CASHFREE_SECRET_KEY', '')
    CASHFREE_ENV = os.environ.get('CASHFREE_ENV', 'sandbox')   # sandbox|production

    # ---------------------------------------------- Razorpay payment gateway --
    # Set these to enable Razorpay payments (if you use Razorpay instead of/in addition to Cashfree).
    RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID')
    RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET')

    # -------------------------------------------- WhatsApp / SMS gateway ----
    # Seeded into the `settings` table on first boot so they can be edited from
    # the admin UI afterwards without a redeploy.
    WA_ENABLED = _flag('WA_ENABLED', False)
    WA_PROVIDER = os.environ.get('WA_PROVIDER', 'generic')
    WA_API_URL = os.environ.get('WA_API_URL', '')
    WA_API_TOKEN = os.environ.get('WA_API_TOKEN', '')
    WA_INSTANCE_ID = os.environ.get('WA_INSTANCE_ID', '')
    WA_COUNTRY_CODE = os.environ.get('WA_COUNTRY_CODE', '91')

    # --------------------------------------------------- links in templates --
    APP_LINK = os.environ.get('APP_LINK', 'https://bit.ly/4bBo8kd')
    WEB_LINK = os.environ.get('WEB_LINK', 'https://yashinternetservices.in')

    # ---------------------------------------------------------- scheduler ---
    # Set to 0 on extra workers so cron jobs only run once across the fleet.
    RUN_SCHEDULER = _flag('RUN_SCHEDULER', True)
    