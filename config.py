import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'trading-system-secret-key-change-me')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or f"sqlite:///{os.path.join(BASE_DIR, 'trading.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    INITIAL_CASH = float(os.environ.get('INITIAL_CASH', 100000.0))

