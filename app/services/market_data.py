import os
import yfinance as yf
import pandas as pd
from datetime import datetime

# Prevent read-only filesystem crash on serverless runtimes
try:
    if hasattr(yf, 'set_tz_cache_location'):
        yf.set_tz_cache_location('/tmp/py-yfinance')
except Exception:
    pass

class MarketDataService:
    @staticmethod
    def get_quote(symbol: str) -> dict:
        symbol = symbol.strip().upper()
        ticker = yf.Ticker(symbol)
        
        # Try fast_info first for quick real-time price
        try:
            info = ticker.fast_info
            current_price = getattr(info, 'last_price', None)
            prev_close = getattr(info, 'previous_close', None)
            
            if current_price is None or pd.isna(current_price):
                # Fallback to 1-day history
                hist = ticker.history(period="5d", interval="1d")
                if not hist.empty:
                    current_price = float(hist['Close'].iloc[-1])
                    prev_close = float(hist['Close'].iloc[-2]) if len(hist) > 1 else current_price
                else:
                    return {"error": f"No data found for symbol {symbol}"}
            
            change = current_price - (prev_close if prev_close else current_price)
            percent_change = (change / prev_close * 100) if prev_close else 0.0

            return {
                "symbol": symbol,
                "price": round(float(current_price), 2),
                "change": round(float(change), 2),
                "percent_change": round(float(percent_change), 2),
                "previous_close": round(float(prev_close), 2) if prev_close else None,
                "currency": getattr(info, 'currency', 'USD') or 'USD'
            }
        except Exception as e:
            return {"error": str(e), "symbol": symbol}

    @staticmethod
    def get_historical_candles(symbol: str, period: str = "1mo", interval: str = "1d") -> list:
        symbol = symbol.strip().upper()
        ticker = yf.Ticker(symbol)
        
        valid_intervals = ["1m", "5m", "15m", "1h", "1d", "1wk"]
        if interval not in valid_intervals:
            interval = "1d"
            
        hist = ticker.history(period=period, interval=interval)
        if hist.empty:
            return []
            
        candles = []
        for index, row in hist.iterrows():
            # Format time for chart library
            if interval in ["1m", "5m", "15m", "1h"]:
                time_val = int(index.timestamp())
            else:
                time_val = index.strftime("%Y-%m-%d")
                
            candles.append({
                "time": time_val,
                "open": round(float(row['Open']), 2),
                "high": round(float(row['High']), 2),
                "low": round(float(row['Low']), 2),
                "close": round(float(row['Close']), 2),
                "volume": int(row['Volume'])
            })
        return candles

