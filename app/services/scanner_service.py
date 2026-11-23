import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.services.market_data import MarketDataService
from app.models.trading import NIFTY_50_DEFAULT, NIFTY_200_DEFAULT
from app.models.nse_universe import (
    get_top_turnover_stocks,
    get_volume_shocker_stocks,
    NSE_2000_SYMBOLS,
    NSE_2000_MAP,
)

logger = logging.getLogger(__name__)

class StockScannerService:
    """
    High-Performance Multi-Universe Stock Scanner Engine.
    Filters stocks across multiple universes using technical indicators,
    with an extensible pluggable hook for custom user-defined rules.
    """

    # In-memory indicator cache: {symbol: {"data": dict, "cached_at": float}}
    _CACHE = {}
    CACHE_TTL_SECONDS = 90.0

    @classmethod
    def get_available_universes(cls) -> list:
        return [
            {
                "id": "NIFTY_50",
                "name": "NIFTY 50",
                "description": "50 Flagship Indian Bluechips & Index Leaders",
                "count": len(NIFTY_50_DEFAULT),
                "badge": "Bluechip"
            },
            {
                "id": "TOP_TURNOVER",
                "name": "Top 200 Turnover",
                "description": "Highest daily turnover & liquid momentum stocks",
                "count": 200,
                "badge": "High Liquidity"
            },
            {
                "id": "NIFTY_200",
                "name": "NIFTY 200",
                "description": "Top 200 large & midcap NSE corporations",
                "count": len(NIFTY_200_DEFAULT),
                "badge": "Broad Market"
            },
            {
                "id": "VOLUME_SHOCKERS",
                "name": "Volume Shockers",
                "description": "High institutional buying & abnormal activity",
                "count": 200,
                "badge": "Momentum"
            },
            {
                "id": "ALL_NSE",
                "name": "NSE 2,000+ Universe",
                "description": "Full universe across the entire National Stock Exchange",
                "count": len(NSE_2000_SYMBOLS),
                "badge": "All Exchange"
            }
        ]

    @classmethod
    def resolve_universe_symbols(cls, universe_id: str, limit: int = 200) -> list:
        uid = (universe_id or "NIFTY_50").upper()
        if uid == "NIFTY_50":
            return NIFTY_50_DEFAULT
        elif uid == "TOP_TURNOVER":
            return get_top_turnover_stocks(min(limit, 200))
        elif uid == "NIFTY_200":
            return NIFTY_200_DEFAULT[:limit]
        elif uid == "VOLUME_SHOCKERS":
            return get_volume_shocker_stocks(min(limit, 200))
        elif uid == "ALL_NSE":
            return NSE_2000_SYMBOLS[:limit]
        return NIFTY_50_DEFAULT

    @classmethod
    def fetch_stock_indicators(cls, symbol: str) -> dict:
        """Fetches technical analysis indicators with in-memory caching"""
        now = time.time()
        cached = cls._CACHE.get(symbol)
        if cached and (now - cached.get("cached_at", 0)) < cls.CACHE_TTL_SECONDS:
            return cached.get("data")

        try:
            analysis = MarketDataService.get_stock_analysis_data(symbol)
            if not analysis or analysis.get("error"):
                return None
            
            clean_ticker = symbol.replace('.NS', '').strip().upper()
            meta_info = NSE_2000_MAP.get(symbol, {})
            name = meta_info.get("name") or f"{clean_ticker} Equity"
            sector = meta_info.get("sector") or "General"

            price = float(analysis.get("price") or 0.0)
            vwap = float(analysis.get("vwap") or price)
            vwap_diff_pct = round(((price - vwap) / vwap) * 100, 2) if vwap > 0 else 0.0

            result = {
                "symbol": symbol,
                "ticker": clean_ticker,
                "name": name,
                "sector": sector,
                "price": price,
                "percent_change": float(analysis.get("percent_change") or 0.0),
                "rsi_14": float(analysis.get("rsi_14") or 50.0),
                "vol_ratio": float(analysis.get("vol_ratio") or 1.0),
                "ema_200": analysis.get("ema_200"),
                "trend_200": analysis.get("trend_200") or "NEUTRAL",
                "vwap": vwap,
                "vwap_diff_pct": vwap_diff_pct,
                "ema_9": float(analysis.get("ema_9") or price),
                "ema_21": float(analysis.get("ema_21") or price),
                "atr_14": float(analysis.get("atr_14") or 0.0),
                "updated_at": time.strftime("%H:%M:%S")
            }
            cls._CACHE[symbol] = {"data": result, "cached_at": now}
            return result
        except Exception as e:
            logger.warning(f"Error analyzing symbol {symbol}: {e}")
            return None

    @classmethod
    def apply_custom_user_rules(cls, stock: dict, custom_rules: dict) -> tuple:
        """
        PLUGGABLE USER RULES HOOK:
        Whenever you want to add custom filtering rules, they can be configured here.
        Returns: (is_match: bool, matched_tags: list[str])
        """
        if not custom_rules:
            return True, []

        tags = []
        # Example rule template placeholders (ready to activate when user provides rules):
        # 1. Custom RSI rule
        custom_min_rsi = custom_rules.get("custom_min_rsi")
        custom_max_rsi = custom_rules.get("custom_max_rsi")
        if custom_min_rsi is not None and stock["rsi_14"] < float(custom_min_rsi):
            return False, []
        if custom_max_rsi is not None and stock["rsi_14"] > float(custom_max_rsi):
            return False, []

        # 2. Custom Condition Expression or Named Rule Tag
        rule_mode = custom_rules.get("rule_mode")
        if rule_mode == "RSI_20_OVERSOLD":
            if stock["rsi_14"] <= 20.0:
                tags.append("RSI ≤ 20 (Deep Oversold)")
            else:
                return False, []
        elif rule_mode == "RSI_80_OVERBOUGHT":
            if stock["rsi_14"] >= 80.0:
                tags.append("RSI ≥ 80 (Extreme Overbought)")
            else:
                return False, []
        elif rule_mode == "GREEN_DAY_SETUP":
            # VWAP Bullish + EMA 9 > EMA 21 + RSI 40-70
            if stock["price"] >= stock["vwap"] and stock["ema_9"] >= stock["ema_21"] and 40 <= stock["rsi_14"] <= 70:
                tags.append("Green Day Scalp Candidate")
            else:
                return False, []

        return True, tags

    @classmethod
    def filter_stock(cls, stock: dict, criteria: dict) -> tuple:
        """
        Evaluates a single stock against all filter criteria.
        Returns: (passes: bool, matched_tags: list[str])
        """
        if not stock:
            return False, []

        tags = []

        # Search query (symbol, ticker, name)
        query = (criteria.get("query") or "").strip().upper()
        if query:
            match_sym = query in stock["symbol"].upper()
            match_tick = query in stock["ticker"].upper()
            match_name = query in stock["name"].upper()
            if not (match_sym or match_tick or match_name):
                return False, []

        # Price range
        price = stock["price"]
        min_p = criteria.get("min_price")
        max_p = criteria.get("max_price")
        if min_p is not None and str(min_p).strip() != "":
            try:
                if price < float(min_p): return False, []
            except ValueError: pass
        if max_p is not None and str(max_p).strip() != "":
            try:
                if price > float(max_p): return False, []
            except ValueError: pass

        # Percent change range
        chg = stock["percent_change"]
        min_chg = criteria.get("min_change")
        max_chg = criteria.get("max_change")
        if min_chg is not None and str(min_chg).strip() != "":
            try:
                if chg < float(min_chg): return False, []
            except ValueError: pass
        if max_chg is not None and str(max_chg).strip() != "":
            try:
                if chg > float(max_chg): return False, []
            except ValueError: pass

        # Change direction preset (gainers, losers, all)
        direction = (criteria.get("direction") or "ALL").upper()
        if direction == "GAINERS" and chg <= 0:
            return False, []
        elif direction == "LOSERS" and chg >= 0:
            return False, []

        # RSI filter
        rsi = stock["rsi_14"]
        min_rsi = criteria.get("min_rsi")
        max_rsi = criteria.get("max_rsi")
        if min_rsi is not None and str(min_rsi).strip() != "":
            try:
                if rsi < float(min_rsi): return False, []
            except ValueError: pass
        if max_rsi is not None and str(max_rsi).strip() != "":
            try:
                if rsi > float(max_rsi): return False, []
            except ValueError: pass

        if rsi <= 25.0:
            tags.append("RSI Oversold (≤25)")
        elif rsi <= 35.0:
            tags.append("RSI Low (≤35)")
        elif rsi >= 75.0:
            tags.append("RSI Overbought (≥75)")

        # Volume Surge Ratio
        vol_ratio = stock["vol_ratio"]
        min_vol_ratio = criteria.get("min_vol_ratio")
        if min_vol_ratio is not None and str(min_vol_ratio).strip() != "":
            try:
                if vol_ratio < float(min_vol_ratio): return False, []
            except ValueError: pass

        if vol_ratio >= 2.0:
            tags.append(f"🔥 Vol Surge {vol_ratio}x")
        elif vol_ratio >= 1.4:
            tags.append(f"Vol Uptick {vol_ratio}x")

        # 200 EMA Macro Trend
        trend_req = (criteria.get("trend") or "ANY").upper()
        if trend_req == "BULLISH" and stock["trend_200"] != "BULLISH":
            return False, []
        elif trend_req == "BEARISH" and stock["trend_200"] != "BEARISH":
            return False, []

        if stock["trend_200"] == "BULLISH":
            tags.append("Above 200 EMA")

        # VWAP Direction
        vwap_req = (criteria.get("vwap") or "ANY").upper()
        if vwap_req == "ABOVE" and stock["price"] < stock["vwap"]:
            return False, []
        elif vwap_req == "BELOW" and stock["price"] > stock["vwap"]:
            return False, []

        if stock["price"] >= stock["vwap"]:
            tags.append("Above VWAP")

        # EMA 9 vs EMA 21 Cross
        ema_cross_req = (criteria.get("ema_cross") or "ANY").upper()
        if ema_cross_req == "BULLISH" and stock["ema_9"] < stock["ema_21"]:
            return False, []
        elif ema_cross_req == "BEARISH" and stock["ema_9"] > stock["ema_21"]:
            return False, []

        if stock["ema_9"] >= stock["ema_21"]:
            tags.append("EMA 9 > 21")

        # Custom pluggable user rules check
        custom_rules = criteria.get("custom_rules") or {}
        passes_custom, custom_tags = cls.apply_custom_user_rules(stock, custom_rules)
        if not passes_custom:
            return False, []
        tags.extend(custom_tags)

        return True, tags

    @classmethod
    def scan_universe(cls, universe_id: str = "NIFTY_50", criteria: dict = None, max_workers: int = 12) -> dict:
        """
        Executes a multi-threaded scan across the given universe and returns filtered results.
        """
        criteria = criteria or {}
        limit = int(criteria.get("limit") or 200)
        symbols = cls.resolve_universe_symbols(universe_id, limit=limit)

        start_time = time.time()
        stocks_data = []

        # Multi-threaded parallel fetch
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_sym = {executor.submit(cls.fetch_stock_indicators, sym): sym for sym in symbols}
            for future in as_completed(future_to_sym):
                res = future.result()
                if res:
                    stocks_data.append(res)

        # Filter the stocks
        filtered_results = []
        for stock in stocks_data:
            passes, tags = cls.filter_stock(stock, criteria)
            if passes:
                item = dict(stock)
                item["matched_tags"] = tags
                filtered_results.append(item)

        # Default sort: Percent change descending (or custom sort)
        sort_by = criteria.get("sort_by") or "percent_change"
        reverse = criteria.get("sort_order", "desc").lower() == "desc"

        if sort_by in ("percent_change", "price", "rsi_14", "vol_ratio", "vwap_diff_pct"):
            filtered_results.sort(key=lambda s: s.get(sort_by, 0.0), reverse=reverse)
        elif sort_by == "ticker":
            filtered_results.sort(key=lambda s: s.get("ticker", ""), reverse=reverse)

        elapsed = round(time.time() - start_time, 2)

        return {
            "success": True,
            "universe": universe_id,
            "total_universe_stocks": len(symbols),
            "total_analyzed": len(stocks_data),
            "total_matched": len(filtered_results),
            "elapsed_seconds": elapsed,
            "criteria_applied": criteria,
            "results": filtered_results
        }
