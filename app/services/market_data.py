import requests
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json'
}

class MarketDataService:
    @staticmethod
    def get_quote(symbol: str) -> dict:
        symbol = symbol.strip().upper()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
        try:
            res = requests.get(url, headers=HEADERS, timeout=8)
            if res.status_code != 200:
                return {"error": f"Failed to fetch {symbol} (HTTP {res.status_code})"}
            
            data = res.json()
            chart_res = data.get('chart', {}).get('result')
            if not chart_res or len(chart_res) == 0:
                return {"error": f"No data found for symbol {symbol}"}
                
            meta = chart_res[0].get('meta', {})
            current_price = meta.get('regularMarketPrice')
            prev_close = meta.get('chartPreviousClose') or meta.get('previousClose')
            
            if current_price is None:
                quotes = chart_res[0].get('indicators', {}).get('quote', [{}])[0]
                closes = [c for c in quotes.get('close', []) if c is not None]
                if closes:
                    current_price = closes[-1]
                    prev_close = closes[-2] if len(closes) > 1 else current_price
                else:
                    return {"error": f"No price available for {symbol}"}
                    
            if not prev_close:
                prev_close = current_price
                
            change = current_price - prev_close
            percent_change = (change / prev_close * 100) if prev_close else 0.0

            return {
                "symbol": symbol,
                "price": round(float(current_price), 2),
                "change": round(float(change), 2),
                "percent_change": round(float(percent_change), 2),
                "previous_close": round(float(prev_close), 2),
                "currency": meta.get('currency', 'USD')
            }
        except Exception as e:
            return {"error": str(e), "symbol": symbol}

    @staticmethod
    def get_historical_candles(symbol: str, period: str = "1mo", interval: str = "1d") -> list:
        symbol = symbol.strip().upper()
        
        # Yahoo Finance ranges: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y
        valid_ranges = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"]
        range_val = period if period in valid_ranges else "1mo"
        
        # Valid intervals: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo
        valid_intervals = ["1m", "5m", "15m", "1h", "1d", "1wk"]
        interval_val = interval if interval in valid_intervals else "1d"
        
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval_val}&range={range_val}"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code != 200:
                return []
                
            data = res.json()
            chart_res = data.get('chart', {}).get('result')
            if not chart_res or len(chart_res) == 0:
                return []
                
            timestamps = chart_res[0].get('timestamp', [])
            quote_data = chart_res[0].get('indicators', {}).get('quote', [{}])[0]
            
            opens = quote_data.get('open', [])
            highs = quote_data.get('high', [])
            lows = quote_data.get('low', [])
            closes = quote_data.get('close', [])
            volumes = quote_data.get('volume', [])
            
            candles = []
            for i in range(len(timestamps)):
                ts = timestamps[i]
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                l = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                v = volumes[i] if i < len(volumes) else 0
                
                # Filter out nulls
                if None in (o, h, l, c):
                    continue
                    
                if interval_val in ["1m", "5m", "15m", "1h"]:
                    time_val = int(ts)
                else:
                    time_val = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    
                candles.append({
                    "time": time_val,
                    "open": round(float(o), 2),
                    "high": round(float(h), 2),
                    "low": round(float(l), 2),
                    "close": round(float(c), 2),
                    "volume": int(v) if v else 0
                })
            return candles
        except Exception:
            return []
