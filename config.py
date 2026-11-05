import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'trading-system-secret-key-change-me')
    
    # On Vercel serverless functions, the root filesystem is read-only; use /tmp for sqlite
    if os.environ.get('VERCEL'):
        default_db = "sqlite:////tmp/trading.db"
    else:
        default_db = f"sqlite:///{os.path.join(BASE_DIR, 'trading.db')}"

    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or default_db
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    INITIAL_CASH = float(os.environ.get('INITIAL_CASH', 100000.0))

