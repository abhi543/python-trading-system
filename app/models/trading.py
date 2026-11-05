from datetime import datetime, timezone
from app import db

NIFTY_50_DEFAULT = [
    'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
    'BHARTIARTL.NS', 'ITC.NS', 'SBIN.NS', 'LT.NS', 'TATAMOTORS.NS',
    'HINDUNILVR.NS', 'AXISBANK.NS', 'BAJFINANCE.NS', 'MARUTI.NS', 'SUNPHARMA.NS',
    'KOTAKBANK.NS', 'TITAN.NS', 'M&M.NS', 'ADANIENT.NS', 'TATASTEEL.NS',
    'WIPRO.NS', 'POWERGRID.NS', 'NTPC.NS', 'ASIANPAINT.NS', 'COALINDIA.NS',
    'BAJAJFINSV.NS', 'ULTRACEMCO.NS', 'NESTLEIND.NS', 'JSWSTEEL.NS', 'GRASIM.NS',
    'TECHM.NS', 'HCLTECH.NS', 'HINDALCO.NS', 'CIPLA.NS', 'ONGC.NS',
    'DRREDDY.NS', 'BRITANNIA.NS', 'ADANIPORTS.NS', 'HEROMOTOCO.NS', 'EICHERMOT.NS',
    'DIVISLAB.NS', 'BPCL.NS', 'INDUSINDBK.NS', 'APOLLOHOSP.NS', 'SHRIRAMFIN.NS',
    'SBILIFE.NS', 'HDFCLIFE.NS', 'TRENT.NS', 'BEL.NS', 'TATACONSUM.NS'
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
    peak_price = db.Column(db.Float, default=0.0)
    stop_loss = db.Column(db.Float, default=0.0)
    take_profit = db.Column(db.Float, default=0.0)
    trailing_active = db.Column(db.Boolean, default=False)
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
    
    # If old watchlist has fewer than 20 items or old US stocks, reset to Nifty 50
    if 'AAPL' in existing_symbols or len(existing_symbols) < 20:
        WatchlistItem.query.delete()
        for s in NIFTY_50_DEFAULT:
            db.session.add(WatchlistItem(symbol=s))
        db.session.commit()
