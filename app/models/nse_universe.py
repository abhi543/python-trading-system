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

NSE_2000_SYMBOLS = [item['symbol'] for item in _NSE_STOCKS_DATA] if _NSE_STOCKS_DATA else NIFTY_200_DEFAULT
NSE_2000_MAP = {item['symbol']: item for item in _NSE_STOCKS_DATA} if _NSE_STOCKS_DATA else {}

def get_nse_universe_batch(batch_index: int = 0, batch_size: int = 200) -> tuple:
    """
    Returns (batch_stocks, current_batch_num, total_batches)
    Allows continuous rolling scans across all 2,000+ NSE stocks in 200-stock chunks.
    """
    total = len(NSE_2000_SYMBOLS)
    if total == 0:
        return (NIFTY_200_DEFAULT, 1, 1)

    total_batches = (total + batch_size - 1) // batch_size
    valid_batch_idx = batch_index % total_batches
    start = valid_batch_idx * batch_size
    end = min(start + batch_size, total)
    return (NSE_2000_SYMBOLS[start:end], valid_batch_idx + 1, total_batches)

