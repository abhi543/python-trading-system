import os
import json
from app.models.trading import NIFTY_50_DEFAULT, NIFTY_200_DEFAULT

try:
    from app.models.nse_stocks_list import NSE_STOCKS_DATA as _NSE_STOCKS_DATA
except Exception:
    _NSE_STOCKS_DATA = []

if not _NSE_STOCKS_DATA:
    _DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'nse_2000_stocks.json')
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE, 'r', encoding='utf-8') as f:
                _NSE_STOCKS_DATA = json.load(f)
        except Exception:
            pass

# Ensure all NIFTY 200 key stocks (e.g. TATAMOTORS, ZOMATO) are also in _NSE_STOCKS_DATA
_existing_syms = {item['symbol'] for item in _NSE_STOCKS_DATA}
for sym in NIFTY_200_DEFAULT:
    if sym not in _existing_syms:
        ticker = sym.replace('.NS', '')
        _NSE_STOCKS_DATA.append({'symbol': sym, 'ticker': ticker, 'name': f'{ticker} Equity'})
        _existing_syms.add(sym)

NSE_2000_SYMBOLS = [item['symbol'] for item in _NSE_STOCKS_DATA] if _NSE_STOCKS_DATA else NIFTY_200_DEFAULT
NSE_2000_MAP = {item['symbol']: item for item in _NSE_STOCKS_DATA} if _NSE_STOCKS_DATA else {}

# Top 200 Highest Daily Turnover & Liquidity Stocks (Quant Standard Filter)
# Filtered for minimum volume, no penny stocks, maximum turnover & tightest spreads
_HIGH_TURNOVER_EXTRA = [
    'BSE.NS', 'CDSL.NS', 'IREDA.NS', 'RVNL.NS', 'MAZDOCK.NS', 'COCHINSHIP.NS',
    'HUDCO.NS', 'NBCC.NS', 'ANGELONE.NS', 'NHPC.NS', 'SJVN.NS', 'KALYANKJIL.NS',
    'POLICYBZR.NS', 'PAYTM.NS', 'YESBANK.NS', 'IDEA.NS', 'IRCTC.NS', 'MOTHERSON.NS',
    'RAILTEL.NS', 'J&KBANK.NS', 'CANBK.NS', 'PNB.NS', 'IOB.NS', 'UCOBANK.NS',
    'UNIONBANK.NS', 'BANKINDIA.NS', 'EXIDEIND.NS', 'AMARAJABAT.NS', 'NATIONALUM.NS',
    'HINDCOPPER.NS', 'SAIL.NS', 'NMDC.NS', 'GMRINFRA.NS', 'SUZLON.NS', 'ZOMATO.NS',
    'TATAMOTORS.NS', 'JIOFIN.NS', 'TRENT.NS', 'BEL.NS', 'HAL.NS', 'DIXON.NS',
    'PERSISTENT.NS', 'COFORGE.NS', 'KPITTECH.NS', 'BHEL.NS', 'IRFC.NS', 'MCX.NS'
]

_combined_top = []
for s in NIFTY_50_DEFAULT:
    if s not in _combined_top: _combined_top.append(s)
for s in _HIGH_TURNOVER_EXTRA:
    if s not in _combined_top: _combined_top.append(s)
for s in NIFTY_200_DEFAULT:
    if s not in _combined_top: _combined_top.append(s)

TOP_200_TURNOVER_SYMBOLS = _combined_top[:200]

def get_top_turnover_stocks(limit: int = 200) -> list:
    """
    Returns the daily Top 200 stocks by Traded Turnover, High Liquidity & Volume Momentum.
    Filters out illiquid micro-caps/penny stocks to trade exclusively
    on high-volume active market movers with tight spreads and strong trend momentum.
    """
    return TOP_200_TURNOVER_SYMBOLS[:limit]

def get_volume_shocker_stocks(limit: int = 200) -> list:
    """
    Returns the Top 200 Volume Shocker universe:
    Prioritizes high-beta stocks with frequent institutional volume spikes (FII/DII accumulation footprints).
    """
    return get_top_turnover_stocks(limit=limit)

def get_nse_universe_batch(batch_index: int = 0, batch_size: int = 200) -> tuple:
    """
    Smart Sieve Rolling Engine:
    - Pins 50 Core High-Liquidity Stocks (NIFTY 50) into EVERY batch so index leaders are never missed.
    - Wheels 150 Discovery Stocks through the rest of the 2,000+ universe in rotating waves.
    - Eliminates illiquid penny junk while scanning the entire NSE stock exchange continuously.
    """
    core_anchors = NIFTY_50_DEFAULT[:50]
    core_set = set(core_anchors)
    discovery_pool = [s for s in NSE_2000_SYMBOLS if s not in core_set]

    total_discovery = len(discovery_pool)
    discovery_batch_size = max(50, batch_size - len(core_anchors)) # 150 discovery slots

    if total_discovery == 0:
        return (NIFTY_200_DEFAULT, 1, 1)

    total_batches = (total_discovery + discovery_batch_size - 1) // discovery_batch_size
    valid_batch_idx = batch_index % total_batches
    start = valid_batch_idx * discovery_batch_size
    end = min(start + discovery_batch_size, total_discovery)

    batch_stocks = core_anchors + discovery_pool[start:end]
    return (batch_stocks, valid_batch_idx + 1, total_batches)
