from datetime import datetime, timezone
from app import db

NIFTY_50_DEFAULT = [
    '^NSEI',         # NIFTY 50 Index
    'RELIANCE.NS',   # Reliance Industries
    'TCS.NS',        # Tata Consultancy Services
    'HDFCBANK.NS',   # HDFC Bank
    'INFY.NS',       # Infosys
    'ICICIBANK.NS',  # ICICI Bank
    'BHARTIARTL.NS', # Bharti Airtel
    'ITC.NS',        # ITC Limited
    'SBIN.NS',       # State Bank of India
    'LT.NS',         # Larsen & Toubro
    'TATAMOTORS.NS', # Tata Motors
    'HINDUNILVR.NS', # Hindustan Unilever
    'AXISBANK.NS',   # Axis Bank
    'BAJFINANCE.NS', # Bajaj Finance
    'MARUTI.NS',     # Maruti Suzuki
    'SUNPHARMA.NS',  # Sun Pharmaceutical
    'KOTAKBANK.NS',  # Kotak Mahindra Bank
    'TITAN.NS',      # Titan Company
    'M&M.NS',        # Mahindra & Mahindra
    'ADANIENT.NS',   # Adani Enterprises
    'TATASTEEL.NS',  # Tata Steel
    'WIPRO.NS',      # Wipro
    'POWERGRID.NS'   # Power Grid Corporation
]

class Account(db.Model):
    __tablename__ = 'accounts'
    id = db.Column(db.Integer, primary_key=True)
    cash_balance = db.Column(db.Float, nullable=False, default=1000000.0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Position(db.Model):
    __tablename__ = 'positions'
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), unique=True, nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=0.0)
    average_price = db.Column(db.Float, nullable=False, default=0.0)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class TradeHistory(db.Model):
    __tablename__ = 'trade_history'
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), nullable=False)
    side = db.Column(db.String(10), nullable=False)  # BUY or SELL
    quantity = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    total = db.Column(db.Float, nullable=False)
    realized_pnl = db.Column(db.Float, default=0.0)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class WatchlistItem(db.Model):
    __tablename__ = 'watchlist'
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), unique=True, nullable=False)
    added_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

def init_default_data(initial_cash=1000000.0):
    if not Account.query.first():
        db.session.add(Account(cash_balance=initial_cash))
        db.session.commit()

    existing = WatchlistItem.query.all()
    existing_symbols = {w.symbol for w in existing}
    
    # If old US stocks are present or watchlist is empty, reset to Nifty 50
    if 'AAPL' in existing_symbols or not existing:
        WatchlistItem.query.delete()
        for s in NIFTY_50_DEFAULT:
            db.session.add(WatchlistItem(symbol=s))
        db.session.commit()
