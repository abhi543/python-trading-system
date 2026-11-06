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

NIFTY_200_DEFAULT = [
    # NIFTY 50 Constituents (1 - 50)
    'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
    'BHARTIARTL.NS', 'ITC.NS', 'SBIN.NS', 'LT.NS', 'TATAMOTORS.NS',
    'HINDUNILVR.NS', 'AXISBANK.NS', 'BAJFINANCE.NS', 'MARUTI.NS', 'SUNPHARMA.NS',
    'KOTAKBANK.NS', 'TITAN.NS', 'M&M.NS', 'ADANIENT.NS', 'TATASTEEL.NS',
    'WIPRO.NS', 'POWERGRID.NS', 'NTPC.NS', 'ASIANPAINT.NS', 'COALINDIA.NS',
    'BAJAJFINSV.NS', 'ULTRACEMCO.NS', 'NESTLEIND.NS', 'JSWSTEEL.NS', 'GRASIM.NS',
    'TECHM.NS', 'HCLTECH.NS', 'HINDALCO.NS', 'CIPLA.NS', 'ONGC.NS',
    'DRREDDY.NS', 'BRITANNIA.NS', 'ADANIPORTS.NS', 'HEROMOTOCO.NS', 'EICHERMOT.NS',
    'DIVISLAB.NS', 'BPCL.NS', 'INDUSINDBK.NS', 'APOLLOHOSP.NS', 'SHRIRAMFIN.NS',
    'SBILIFE.NS', 'HDFCLIFE.NS', 'TRENT.NS', 'BEL.NS', 'TATACONSUM.NS',
    
    # NIFTY NEXT 50 Constituents (51 - 100)
    'ABB.NS', 'ADANIENSOL.NS', 'ADANIGREEN.NS', 'AMBUJACEM.NS', 'ATGL.NS',
    'BAJAJHLDNG.NS', 'BANKBARODA.NS', 'BERGEPAINT.NS', 'BOSCHLTD.NS', 'CANBK.NS',
    'CHOLAFIN.NS', 'COLPAL.NS', 'DLF.NS', 'DMART.NS', 'GAIL.NS',
    'GODREJCP.NS', 'HAL.NS', 'HAVELLS.NS', 'ICICIGI.NS', 'ICICIPRULI.NS',
    'IOC.NS', 'IRCTC.NS', 'IRFC.NS', 'JINDALSTEL.NS', 'JIOFIN.NS',
    'LICI.NS', 'LTIM.NS', 'MARICO.NS', 'MOTHERSON.NS', 'NAUKRI.NS',
    'PIDILITIND.NS', 'PFC.NS', 'PNB.NS', 'RECLTD.NS', 'SBICARD.NS',
    'SIEMENS.NS', 'SRF.NS', 'SUNTV.NS', 'TATAPOWER.NS', 'TORNTPHARM.NS',
    'TVSMOTOR.NS', 'UNITDSPR.NS', 'VBL.NS', 'VEDL.NS', 'ZYDUSLIFE.NS',
    'POLYCAB.NS', 'MAXHEALTH.NS', 'MANKIND.NS', 'CGPOWER.NS', 'PERSISTENT.NS',

    # NIFTY MIDCAP TOP 100 Constituents (101 - 200)
    'ASTRAL.NS', 'AUROPHARMA.NS', 'BALKRISIND.NS', 'BATAINDIA.NS', 'BHARATFORG.NS',
    'BHEL.NS', 'BIOCON.NS', 'COFORGE.NS', 'CONCOR.NS', 'COROMANDEL.NS',
    'CROMPTON.NS', 'CUMMINSIND.NS', 'DALBHARAT.NS', 'DEEPAKNTR.NS', 'DELHIVERY.NS',
    'DIXON.NS', 'ESCORTS.NS', 'EXIDEIND.NS', 'FEDERALBNK.NS', 'FORTIS.NS',
    'GLENMARK.NS', 'GMRINFRA.NS', 'GODREJPROP.NS', 'GUJGASLTD.NS', 'HDFCAMC.NS',
    'HINDPETRO.NS', 'IDBI.NS', 'IDFCFIRSTB.NS', 'INDIANB.NS', 'INDHOTEL.NS',
    'IPCALAB.NS', 'JSL.NS', 'JUBLFOOD.NS', 'KALYANKJIL.NS', 'KPITTECH.NS',
    'L&TFH.NS', 'LALPATHLAB.NS', 'LICHSGFIN.NS', 'LUPIN.NS', 'M&MFIN.NS',
    'METROPOLIS.NS', 'MFSL.NS', 'MPHASIS.NS', 'MRF.NS', 'MUTHOOTFIN.NS',
    'NATIONALUM.NS', 'NMDC.NS', 'OBEROIRLTY.NS', 'OFSS.NS', 'PAGEIND.NS',
    'PATANJALI.NS', 'PETRONET.NS', 'PHOENIXLTD.NS', 'PIIND.NS', 'POLICYBZR.NS',
    'POONAWALLA.NS', 'PRESTIGE.NS', 'PVRINOX.NS', 'RAMCOCEM.NS', 'SAIL.NS',
    'SJVN.NS', 'SOLARINDS.NS', 'SONACOMS.NS', 'STARHEALTH.NS', 'SUMICHEM.NS',
    'SUNDARMFIN.NS', 'SUNTECK.NS', 'SUPREMEIND.NS', 'SUZLON.NS', 'SYNGENE.NS',
    'TATACHEM.NS', 'TATACOMM.NS', 'TATAELXSI.NS', 'TATATECH.NS', 'TORNTPOWER.NS',
    'TRIDENT.NS', 'UBL.NS', 'UCOBANK.NS', 'UNIONBANK.NS', 'UPL.NS',
    'VOLTAS.NS', 'YESBANK.NS', 'ZEEL.NS', 'ZOMATO.NS', 'ZYDUSWELL.NS',
    'APLAPOLLO.NS', 'ALKEM.NS', 'ASHOKLEY.NS', 'AUBANK.NS', 'BANDHANBNK.NS',
    'ABCAPITAL.NS', 'ABFRL.NS', 'ACC.NS', 'AJANTPHARM.NS', 'APLLTD.NS',
    'BDL.NS', 'BLUESTARCO.NS', 'CENTRALBK.NS', 'CHAMBLFERT.NS', 'CLEAN.NS'
]

class Account(db.Model):
    __tablename__ = 'accounts'
    id = db.Column(db.Integer, primary_key=True)
    cash_balance = db.Column(db.Float, nullable=False, default=50000.0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Position(db.Model):
    __tablename__ = 'positions'
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), unique=True, nullable=False)
    side = db.Column(db.String(10), nullable=False, default='LONG')  # 'LONG' or 'SHORT'
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
    charges = db.Column(db.Float, default=0.0)
    brokerage = db.Column(db.Float, default=0.0)
    stt = db.Column(db.Float, default=0.0)
    exchange_charges = db.Column(db.Float, default=0.0)
    sebi_charges = db.Column(db.Float, default=0.0)
    gst = db.Column(db.Float, default=0.0)
    stamp_duty = db.Column(db.Float, default=0.0)
    net_total = db.Column(db.Float, default=0.0)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class WatchlistItem(db.Model):
    __tablename__ = 'watchlist'
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), unique=True, nullable=False)
    added_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

# ==============================================================================
# NIGHTHAWK ISOLATED MODELS: 1 CRORE (₹10,000,000) DESK
# ==============================================================================
class AfterHoursAccount(db.Model):
    __tablename__ = 'after_hours_accounts'
    id = db.Column(db.Integer, primary_key=True)
    cash_balance = db.Column(db.Float, nullable=False, default=10000000.0) # ₹1,00,00,000 (1 Crore)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class AfterHoursPosition(db.Model):
    __tablename__ = 'after_hours_positions'
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), unique=True, nullable=False)
    side = db.Column(db.String(10), nullable=False, default='LONG')  # 'LONG' or 'SHORT'
    quantity = db.Column(db.Float, nullable=False, default=0.0)
    average_price = db.Column(db.Float, nullable=False, default=0.0)
    peak_price = db.Column(db.Float, default=0.0)
    stop_loss = db.Column(db.Float, default=0.0)
    take_profit = db.Column(db.Float, default=0.0)
    trailing_active = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class AfterHoursTradeHistory(db.Model):
    __tablename__ = 'after_hours_trade_history'
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), nullable=False)
    side = db.Column(db.String(10), nullable=False)  # BUY or SELL
    quantity = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    total = db.Column(db.Float, nullable=False)
    realized_pnl = db.Column(db.Float, default=0.0)
    charges = db.Column(db.Float, default=0.0)
    brokerage = db.Column(db.Float, default=0.0)
    stt = db.Column(db.Float, default=0.0)
    exchange_charges = db.Column(db.Float, default=0.0)
    sebi_charges = db.Column(db.Float, default=0.0)
    gst = db.Column(db.Float, default=0.0)
    stamp_duty = db.Column(db.Float, default=0.0)
    net_total = db.Column(db.Float, default=0.0)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

def init_default_data(initial_cash=50000.0, after_hours_initial_cash=10000000.0):
    if not Account.query.first():
        db.session.add(Account(cash_balance=initial_cash))
        db.session.commit()

    if not AfterHoursAccount.query.first():
        db.session.add(AfterHoursAccount(cash_balance=after_hours_initial_cash))
        db.session.commit()

    existing = WatchlistItem.query.all()
    existing_symbols = {w.symbol for w in existing}
    
    # If old watchlist has fewer than 20 items or old US stocks, reset to Nifty 50
    if 'AAPL' in existing_symbols or len(existing_symbols) < 20:
        WatchlistItem.query.delete()
        for s in NIFTY_50_DEFAULT:
            db.session.add(WatchlistItem(symbol=s))
        db.session.commit()
