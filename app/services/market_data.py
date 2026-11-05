import requests
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json'
}

class MarketDataService:
    @staticmethod
    def _fetch_chart_data(symbol: str, interval: str = "1d", range_val: str = "5d") -> tuple:
        symbol = symbol.strip().upper()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={range_val}"
        try:
            res = requests.get(url, headers=HEADERS, timeout=8)
            if res.status_code == 200:
                data = res.json()
                results = data.get('chart', {}).get('result')
                if results and len(results) > 0:
                    return results[0], symbol
        except Exception:
            pass

        # If plain symbol without exchange extension failed, auto-fallback to NSE (.NS)
        if '.' not in symbol and not symbol.startswith('^'):
            fallback_symbol = f"{symbol}.NS"
            url_fallback = f"https://query1.finance.yahoo.com/v8/finance/chart/{fallback_symbol}?interval={interval}&range={range_val}"
            try:
                res = requests.get(url_fallback, headers=HEADERS, timeout=8)
                if res.status_code == 200:
                    data = res.json()
                    results = data.get('chart', {}).get('result')
                    if results and len(results) > 0:
                        return results[0], fallback_symbol
            except Exception:
                pass

        return None, symbol

    @staticmethod
    def get_quote(symbol: str) -> dict:
        chart_res, resolved_symbol = MarketDataService._fetch_chart_data(symbol, interval="1d", range_val="5d")
        if not chart_res:
            return {"error": f"No data found for symbol {symbol}"}

        meta = chart_res.get('meta', {})
        current_price = meta.get('regularMarketPrice')
        prev_close = meta.get('chartPreviousClose') or meta.get('previousClose')
        
        if current_price is None:
            quotes = chart_res.get('indicators', {}).get('quote', [{}])[0]
            closes = [c for c in quotes.get('close', []) if c is not None]
            if closes:
                current_price = closes[-1]
                prev_close = closes[-2] if len(closes) > 1 else current_price
            else:
                return {"error": f"No price available for {resolved_symbol}"}
                
        if not prev_close:
            prev_close = current_price
            
        change = current_price - prev_close
        percent_change = (change / prev_close * 100) if prev_close else 0.0
        currency = meta.get('currency', 'INR')

        return {
            "symbol": resolved_symbol,
            "price": round(float(current_price), 2),
            "change": round(float(change), 2),
            "percent_change": round(float(percent_change), 2),
            "previous_close": round(float(prev_close), 2),
            "currency": currency,
            "currency_symbol": "₹" if currency == "INR" else "$"
        }

    @staticmethod
    def get_stock_analysis_data(symbol: str) -> dict:
        """Fetches both current quote and 1mo historical closes in a single fast HTTP call"""
        chart_res, resolved_symbol = MarketDataService._fetch_chart_data(symbol, interval="1d", range_val="1mo")
        if not chart_res:
            return {"error": f"No data for {symbol}"}

        meta = chart_res.get('meta', {})
        current_price = meta.get('regularMarketPrice')
        prev_close = meta.get('chartPreviousClose') or meta.get('previousClose')
        
        quotes = chart_res.get('indicators', {}).get('quote', [{}])[0]
        raw_closes = quotes.get('close', [])
        valid_closes = [float(c) for c in raw_closes if c is not None]

        if not valid_closes:
            return {"error": f"No close data for {symbol}"}

        if current_price is None:
            current_price = valid_closes[-1]
            prev_close = valid_closes[-2] if len(valid_closes) > 1 else current_price

        if not prev_close:
            prev_close = valid_closes[-2] if len(valid_closes) > 1 else current_price

        change = current_price - prev_close
        percent_change = (change / prev_close * 100) if prev_close else 0.0

        return {
            "symbol": resolved_symbol,
            "price": round(float(current_price), 2),
            "percent_change": round(float(percent_change), 2),
            "closes": valid_closes
        }

    @staticmethod
    def get_historical_candles(symbol: str, period: str = "1mo", interval: str = "1d") -> list:
        valid_ranges = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"]
        range_val = period if period in valid_ranges else "1mo"
        
        valid_intervals = ["1m", "5m", "15m", "1h", "1d", "1wk"]
        interval_val = interval if interval in valid_intervals else "1d"
        
        chart_res, resolved_symbol = MarketDataService._fetch_chart_data(symbol, interval=interval_val, range_val=range_val)
        if not chart_res:
            return []
            
        timestamps = chart_res.get('timestamp', [])
        quote_data = chart_res.get('indicators', {}).get('quote', [{}])[0]
        
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
