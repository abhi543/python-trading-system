import time
import requests
from requests.adapters import HTTPAdapter
from datetime import datetime

_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=1)
_session.mount('https://', _adapter)
_session.mount('http://', _adapter)
_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json'
})

# In-memory quote & chart cache (20s TTL) to eliminate duplicate network calls and slash Vercel compute GB-hours
_CHART_CACHE = {}
_CACHE_TTL_SECONDS = 20.0

class MarketDataService:
    @staticmethod
    def _fetch_chart_data(symbol: str, interval: str = "1d", range_val: str = "5d") -> tuple:
        symbol = symbol.strip().upper()
        cache_key = f"{symbol}:{interval}:{range_val}"
        now_ts = time.time()

        # Instant Cache Hit: eliminates repeated external API latency and saves serverless execution time
        if cache_key in _CHART_CACHE:
            cached_res, resolved_sym, cached_at = _CHART_CACHE[cache_key]
            if (now_ts - cached_at) < _CACHE_TTL_SECONDS:
                return cached_res, resolved_sym

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={range_val}"
        try:
            res = _session.get(url, timeout=4)
            if res.status_code == 200:
                data = res.json()
                results = data.get('chart', {}).get('result')
                if results and len(results) > 0:
                    _CHART_CACHE[cache_key] = (results[0], symbol, now_ts)
                    if len(_CHART_CACHE) > 500:
                        _CHART_CACHE.clear()
                    return results[0], symbol
        except Exception:
            pass

        # If plain symbol without exchange extension failed, auto-fallback to NSE (.NS)
        if '.' not in symbol and not symbol.startswith('^'):
            fallback_symbol = f"{symbol}.NS"
            url_fallback = f"https://query1.finance.yahoo.com/v8/finance/chart/{fallback_symbol}?interval={interval}&range={range_val}"
            try:
                res = _session.get(url_fallback, timeout=4)
                if res.status_code == 200:
                    data = res.json()
                    results = data.get('chart', {}).get('result')
                    if results and len(results) > 0:
                        _CHART_CACHE[cache_key] = (results[0], fallback_symbol, now_ts)
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
        """Fetches quote, 1y historical bars, 200 EMA, volume ratio, and ATR-14 in a single fast call"""
        chart_res, resolved_symbol = MarketDataService._fetch_chart_data(symbol, interval="1d", range_val="1y")
        if not chart_res:
            return {"error": f"No data for {symbol}"}

        meta = chart_res.get('meta', {})
        current_price = meta.get('regularMarketPrice')
        prev_close = meta.get('chartPreviousClose') or meta.get('previousClose')
        
        quotes = chart_res.get('indicators', {}).get('quote', [{}])[0]
        raw_closes = quotes.get('close', [])
        raw_highs = quotes.get('high', [])
        raw_lows = quotes.get('low', [])
        raw_opens = quotes.get('open', [])
        raw_vols = quotes.get('volume', [])

        valid_data = []
        for i in range(len(raw_closes)):
            c = raw_closes[i]
            if c is not None:
                h = raw_highs[i] if i < len(raw_highs) and raw_highs[i] is not None else c
                l = raw_lows[i] if i < len(raw_lows) and raw_lows[i] is not None else c
                o = raw_opens[i] if i < len(raw_opens) and raw_opens[i] is not None else c
                v = raw_vols[i] if i < len(raw_vols) and raw_vols[i] is not None else 0
                valid_data.append({'close': float(c), 'high': float(h), 'low': float(l), 'open': float(o), 'volume': float(v)})

        if not valid_data:
            return {"error": f"No valid price series for {symbol}"}

        import pandas as pd
        df = pd.DataFrame(valid_data)
        valid_closes = df['close'].tolist()
        valid_highs = df['high'].tolist()
        valid_lows = df['low'].tolist()
        valid_opens = df['open'].tolist()
        valid_volumes = df['volume'].tolist()

        if current_price is None:
            current_price = valid_closes[-1]
            prev_close = valid_closes[-2] if len(valid_closes) > 1 else current_price

        if not prev_close:
            prev_close = valid_closes[-2] if len(valid_closes) > 1 else current_price

        change = current_price - prev_close
        percent_change = (change / prev_close * 100) if prev_close else 0.0

        # Option A: 200 EMA Macro Trend
        ema_200 = None
        trend_200 = "NEUTRAL"
        if len(df) >= 50:
            span_val = min(200, len(df))
            ema_series = df['close'].ewm(span=span_val, adjust=False).mean()
            ema_200 = round(float(ema_series.iloc[-1]), 2)
            trend_200 = "BULLISH" if current_price >= ema_200 else "BEARISH"

        # Option A: Volume Surge Ratio (vs 20-day Average Volume)
        vol_ratio = 1.0
        if len(df) >= 20:
            vol_avg20 = float(df['volume'].rolling(20).mean().iloc[-1])
            curr_vol = float(df['volume'].iloc[-1])
            if vol_avg20 > 0:
                vol_ratio = round(curr_vol / vol_avg20, 2)

        # Option B: ATR-14 (Average True Range for Dynamic Stops)
        atr_14 = round(float(current_price * 0.025), 2)  # 2.5% default fallback
        if len(df) >= 15:
            tr1 = df['high'] - df['low']
            tr2 = (df['high'] - df['close'].shift(1)).abs()
            tr3 = (df['low'] - df['close'].shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_calc = tr.rolling(14).mean().iloc[-1]
            if not pd.isna(atr_calc) and atr_calc > 0:
                atr_14 = round(float(atr_calc), 2)

        return {
            "symbol": resolved_symbol,
            "price": round(float(current_price), 2),
            "percent_change": round(float(percent_change), 2),
            "closes": valid_closes,
            "highs": valid_highs,
            "lows": valid_lows,
            "opens": valid_opens,
            "volumes": valid_volumes,
            "ema_200": ema_200,
            "trend_200": trend_200,
            "vol_ratio": vol_ratio,
            "atr_14": atr_14
        }

    @staticmethod
    def get_replay_data(symbol: str, range_val: str = "5d", interval: str = "5m") -> dict:
        """Fetches high-resolution intraday candles for market session replayer"""
        chart_res, resolved_symbol = MarketDataService._fetch_chart_data(symbol, interval=interval, range_val=range_val)
        if not chart_res:
            return {"error": f"No intraday data for {symbol}"}

        timestamps = chart_res.get('timestamp', [])
        quote_data = chart_res.get('indicators', {}).get('quote', [{}])[0]
        
        opens = quote_data.get('open', [])
        highs = quote_data.get('high', [])
        lows = quote_data.get('low', [])
        closes = quote_data.get('close', [])
        volumes = quote_data.get('volume', [])
        
        bars = []
        for i in range(len(timestamps)):
            c = closes[i] if i < len(closes) else None
            if c is not None:
                ts = timestamps[i]
                dt = datetime.fromtimestamp(ts)
                bars.append({
                    "time": int(ts),
                    "datetime": dt.strftime("%Y-%m-%d %H:%M"),
                    "date": dt.strftime("%Y-%m-%d"),
                    "open": round(float(opens[i] if opens[i] is not None else c), 2),
                    "high": round(float(highs[i] if highs[i] is not None else c), 2),
                    "low": round(float(lows[i] if lows[i] is not None else c), 2),
                    "close": round(float(c), 2),
                    "volume": int(volumes[i] or 0)
                })

        dates = sorted(list(set(b['date'] for b in bars)))
        
        # Also compute daily 200 EMA to provide macro trend context during replay
        daily_info = MarketDataService.get_stock_analysis_data(symbol)
        ema_200 = daily_info.get("ema_200")

        return {
            "symbol": resolved_symbol,
            "name": resolved_symbol.replace('.NS', ''),
            "interval": interval,
            "total_bars": len(bars),
            "dates": dates,
            "latest_date": dates[-1] if dates else None,
            "ema_200": ema_200,
            "bars": bars
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
