import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-me'
    
    # ====== CHANGE THIS LINE ======
    # By default, the app uses SQLite. To use MySQL, set the DATABASE_URL environment variable.
    # Example: DATABASE_URL=mysql://username:password@localhost/dbname
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///app.db'
    # ==============================
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = os.environ.get('FLASK_ENV') == 'production'
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = os.environ.get('FLASK_ENV') == 'production'
    PREFERRED_URL_SCHEME = 'https'
    WTF_CSRF_TIME_LIMIT = 3600

    # Razorpay credentials – set these via environment variables (e.g. in .env).
    # Left as None when unset so app.py can correctly detect "not configured"
    # and disable online payment instead of crashing with a bad-auth error.
    RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID')
    RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET')