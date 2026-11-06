import os
import json
import threading
import time
import concurrent.futures
from datetime import datetime
import pandas as pd
import numpy as np

from app import db
from app.services.market_data import MarketDataService
from app.services.crypto_trader import CryptoTradingEngine
from app.models.trading import CryptoPosition, CryptoAccount, CRYPTO_DEFAULT_PAIRS

def _get_crypto_bot_state_file():
    tmp = os.environ.get('TMPDIR', '/tmp')
    if os.path.exists(tmp) and os.access(tmp, os.W_OK):
        return os.path.join(tmp, 'crypto_bot_state.json')
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    return os.path.join(base, 'crypto_bot_state.json')

def _save_persistent_crypto_bot_state(data: dict):
    try:
        path = _get_crypto_bot_state_file()
        with open(path, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass

def _load_persistent_crypto_bot_state() -> dict:
    try:
        path = _get_crypto_bot_state_file()
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

class CryptoAutoTradingBot:
    def __init__(self):
        self.is_running = False
        self.scan_mode = "ALL_CRYPTO"  # "ALL_CRYPTO", "TOP_10", or "SINGLE"
        self.single_symbol = "BTC-USD"
        self.strategy = "123_REVERSAL"  # "123_REVERSAL", "SMA_CROSSOVER", "RSI_MOMENTUM"
        self.allocation_per_trade = 1000.0  # $1,000 USDT allocated per trade
        self.max_positions = 3              # Max 3 concurrent positions (unless 123_REVERSAL)
        self.interval_seconds = 60          # 1-minute scan interval
        self.leverage = 2                   # 2x Leverage default for crypto
        self.fast_period = 9
        self.slow_period = 21
        self.rsi_period = 14
        self.rsi_oversold = 35
        self.rsi_overbought = 65
        self.stop_loss_pct = 1.5            # 1.5% SL
        self.take_profit_pct = 2.0          # 2.0% TP
        
        # Macro & Risk Shields
        self.use_ema200_filter = True       # Only BUY if price > 200 EMA
        self.use_trailing_stop = True       # Breakeven Shield & Trailing Stop
        self.trailing_trigger_pct = 1.0     # Breakeven ratchet triggered at +1.0%

        self.last_scan_time = None
        self.total_scans = 0
        self.logs = []
        self.scanner_results = []
        self.thread = None
        self._lock = threading.Lock()

        # Restore persisted state
        saved = _load_persistent_crypto_bot_state()
        if saved:
            self.is_running = saved.get("is_running", False)
            self.scan_mode = saved.get("scan_mode", self.scan_mode)
            self.strategy = saved.get("strategy", self.strategy)
            self.allocation_per_trade = saved.get("allocation_per_trade", self.allocation_per_trade)
            self.max_positions = saved.get("max_positions", self.max_positions)
            self.leverage = saved.get("leverage", self.leverage)
            self.use_ema200_filter = saved.get("use_ema200_filter", self.use_ema200_filter)
            self.use_trailing_stop = saved.get("use_trailing_stop", self.use_trailing_stop)

        self.add_log("🤖 24/7 Autonomous Crypto Trading Bot engine loaded and initialized.", "INFO")

    def add_log(self, message: str, level: str = "INFO"):
        with self._lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self.logs.append({
                "timestamp": ts,
                "message": message,
                "level": level
            })
            if len(self.logs) > 100:
                self.logs.pop(0)

    def get_status(self):
        persisted = _load_persistent_crypto_bot_state()
        active_status = self.is_running or persisted.get("is_running", False)

        positions_count = 0
        try:
            positions_count = CryptoPosition.query.count()
        except Exception:
            pass

        return {
            "is_running": active_status,
            "scan_mode": self.scan_mode,
            "single_symbol": self.single_symbol,
            "strategy": self.strategy,
            "allocation_per_trade": self.allocation_per_trade,
            "max_positions": self.max_positions,
            "interval_seconds": self.interval_seconds,
            "leverage": self.leverage,
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "rsi_period": self.rsi_period,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "use_ema200_filter": self.use_ema200_filter,
            "use_trailing_stop": self.use_trailing_stop,
            "trailing_trigger_pct": self.trailing_trigger_pct,
            "last_scan_time": self.last_scan_time.strftime("%Y-%m-%d %H:%M:%S") if self.last_scan_time else None,
            "total_scans": self.total_scans,
            "active_positions_count": positions_count,
            "logs": self.logs[-35:]
        }

    def update_config(self, config: dict):
        if "scan_mode" in config: self.scan_mode = config["scan_mode"]
        if "single_symbol" in config: self.single_symbol = config["single_symbol"].strip().upper()
        if "strategy" in config: self.strategy = config["strategy"]
        if "allocation_per_trade" in config: self.allocation_per_trade = float(config["allocation_per_trade"])
        if "max_positions" in config: self.max_positions = int(config["max_positions"])
        if "leverage" in config: self.leverage = int(config["leverage"])
        if "stop_loss_pct" in config: self.stop_loss_pct = float(config["stop_loss_pct"])
        if "take_profit_pct" in config: self.take_profit_pct = float(config["take_profit_pct"])
        if "use_ema200_filter" in config: self.use_ema200_filter = bool(config["use_ema200_filter"])
        if "use_trailing_stop" in config: self.use_trailing_stop = bool(config["use_trailing_stop"])

        _save_persistent_crypto_bot_state({
            "is_running": self.is_running,
            "scan_mode": self.scan_mode,
            "strategy": self.strategy,
            "allocation_per_trade": self.allocation_per_trade,
            "max_positions": self.max_positions,
            "leverage": self.leverage,
            "use_ema200_filter": self.use_ema200_filter,
            "use_trailing_stop": self.use_trailing_stop
        })

    def start(self, config: dict = None):
        if config:
            self.update_config(config)
        self.is_running = True
        _save_persistent_crypto_bot_state({
            "is_running": True,
            "scan_mode": self.scan_mode,
            "strategy": self.strategy,
            "allocation_per_trade": self.allocation_per_trade,
            "max_positions": self.max_positions,
            "leverage": self.leverage,
            "use_ema200_filter": self.use_ema200_filter,
            "use_trailing_stop": self.use_trailing_stop
        })
        mode_label = "ALL CRYPTO BASKET" if self.scan_mode == "ALL_CRYPTO" else self.single_symbol
        ema_tag = "200 EMA Filter [ON]" if self.use_ema200_filter else "200 EMA Filter [OFF]"
        trail_tag = "Trailing Stop [ON]" if self.use_trailing_stop else "Trailing Stop [OFF]"
        self.add_log(f"🚀 Crypto Auto-Bot ACTIVATED (24/7 LIVE) on {mode_label} | {self.strategy} | {self.leverage}x Lev | {ema_tag} | {trail_tag} | Alloc: ${self.allocation_per_trade:,.0f} USDT", "SUCCESS")

        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

        return {"success": True, "message": f"Crypto Bot activated on {mode_label}", "status": self.get_status()}

    def stop(self):
        self.is_running = False
        _save_persistent_crypto_bot_state({"is_running": False})
        self.add_log("🛑 Crypto Auto-Bot PAUSED and STOPPED. Automated orders are halted.", "WARNING")
        return {"success": True, "message": "Crypto Bot stopped", "status": self.get_status()}

    def evaluate_single_coin(self, symbol: str, held_positions_map: dict, allow_order_execution: bool = True) -> dict:
        """Evaluates indicators, risk exits, and computes buy/sell signals for a crypto coin"""
        chart_res, resolved_symbol = MarketDataService._fetch_chart_data(symbol, interval="1d", range_val="1y")
        if not chart_res:
            return {"symbol": symbol, "signal": "ERROR", "error": f"No data for {symbol}"}

        meta = chart_res.get('meta', {})
        current_price = meta.get('regularMarketPrice')
        prev_close = meta.get('chartPreviousClose') or meta.get('previousClose')
        quotes = chart_res.get('indicators', {}).get('quote', [{}])[0]
        raw_closes = [c for c in quotes.get('close', []) if c is not None]
        raw_highs = [h for h in quotes.get('high', []) if h is not None]
        raw_lows = [l for l in quotes.get('low', []) if l is not None]

        if not raw_closes:
            return {"symbol": symbol, "signal": "ERROR", "error": "No price closes"}

        if current_price is None:
            current_price = raw_closes[-1]
            prev_close = raw_closes[-2] if len(raw_closes) > 1 else current_price

        change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close else 0.0
        display_name = symbol.replace('-USD', '')

        # 200 EMA calculation
        ema_200 = None
        trend_200 = "NEUTRAL"
        if len(raw_closes) >= 50:
            df = pd.DataFrame({"close": raw_closes})
            ema_s = df['close'].ewm(span=min(200, len(raw_closes)), adjust=False).mean()
            ema_200 = round(float(ema_s.iloc[-1]), 2)
            trend_200 = "BULLISH" if current_price >= ema_200 else "BEARISH"

        # Check existing position
        pos = held_positions_map.get(symbol)
        
        # 1. Trailing Stop-Loss & Breakeven Shield
        if pos and pos.quantity > 0:
            entry_price = pos.entry_price
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if pos.side == 'LONG' else ((entry_price - current_price) / entry_price * 100)

            # Breakeven Shield at +1.0%
            if self.use_trailing_stop:
                if pnl_pct >= self.trailing_trigger_pct and (pos.stop_loss or 0) < entry_price:
                    pos.stop_loss = entry_price
                    try:
                        db.session.commit()
                    except Exception:
                        pass
                    self.add_log(f"🛡️ [BREAKEVEN SHIELD] {display_name}: Gain reached +{pnl_pct:.2f}%. Stop-loss raised to Breakeven (${entry_price:,.2f}). Trade is 100% Risk-Free!", "SUCCESS")

            # Check if SL triggered
            sl_level = pos.stop_loss or 0.0
            if sl_level > 0:
                is_sl_hit = (pos.side == 'LONG' and current_price <= sl_level) or (pos.side == 'SHORT' and current_price >= sl_level)
                if is_sl_hit:
                    if allow_order_execution:
                        self.add_log(f"🛑 [STOP HIT] {display_name}: Closed @ ${current_price:,.2f} (SL: ${sl_level:,.2f}, P&L: {pnl_pct:+.2f}%)", "ERROR" if pnl_pct < 0 else "SUCCESS")
                        CryptoTradingEngine.close_position_by_id(pos.id)
                    return {
                        "symbol": symbol, "displayName": display_name, "price": current_price,
                        "change_pct": change_pct, "signal": "STOP_LOSS", "reason": f"Stop-Loss hit @ ${current_price:,.2f} ({pnl_pct:+.2f}%)",
                        "action_executed": "STOP_EXIT" if allow_order_execution else "OFFLINE", "ema_200": ema_200, "trend_200": trend_200
                    }

            # Check Take-Profit
            tp_level = pos.take_profit or 0.0
            is_tp_hit = (pos.side == 'LONG' and current_price >= tp_level > 0) or (pos.side == 'SHORT' and current_price <= tp_level > 0)
            if not is_tp_hit and self.strategy != "123_REVERSAL" and pnl_pct >= self.take_profit_pct:
                is_tp_hit = True

            if is_tp_hit:
                if allow_order_execution:
                    self.add_log(f"🎯 [TARGET ACHIEVED] {display_name}: Target hit @ ${current_price:,.2f}! Locked +{pnl_pct:.2f}%", "SUCCESS")
                    CryptoTradingEngine.close_position_by_id(pos.id)
                return {
                    "symbol": symbol, "displayName": display_name, "price": current_price,
                    "change_pct": change_pct, "signal": "TAKE_PROFIT", "reason": f"Target locked @ ${current_price:,.2f} (+{pnl_pct:.2f}%)",
                    "action_executed": "TARGET_PROFIT" if allow_order_execution else "OFFLINE", "ema_200": ema_200, "trend_200": trend_200
                }

        # 2. STRATEGY MODEL SIGNALS
        signal = "HOLD"
        reason = "No trade signal"
        rev_info = None

        if self.strategy == "123_REVERSAL":
            rev_info = self._compute_123_reversal(raw_closes, raw_highs, raw_lows, current_price)
            if rev_info and rev_info.get("detected"):
                side = rev_info.get("side", "BUY")
                rev_score = rev_info.get("reversal_score", 85)
                signal = side
                reason = f"1-2-3 Reversal Breakout (Score: {rev_score}/100) | SL: ${rev_info['stop_loss']:,.2f} | Target: ${rev_info['target']:,.2f}"

        elif self.strategy == "SMA_CROSSOVER":
            if len(raw_closes) >= 22:
                df = pd.DataFrame({"close": raw_closes})
                df["sma_fast"] = df["close"].rolling(self.fast_period).mean()
                df["sma_slow"] = df["close"].rolling(self.slow_period).mean()

                f_curr, f_prev = df["sma_fast"].iloc[-1], df["sma_fast"].iloc[-2]
                s_curr, s_prev = df["sma_slow"].iloc[-1], df["sma_slow"].iloc[-2]

                if f_prev <= s_prev and f_curr > s_curr:
                    signal = "BUY"
                    reason = f"Golden Cross: SMA({self.fast_period}) ${f_curr:,.2f} crossed above SMA({self.slow_period}) ${s_curr:,.2f}"
                elif f_prev >= s_prev and f_curr < s_curr:
                    signal = "SELL"
                    reason = f"Death Cross: SMA({self.fast_period}) ${f_curr:,.2f} crossed below SMA({self.slow_period}) ${s_curr:,.2f}"
                else:
                    signal = "BUY" if f_curr > s_curr else "HOLD"
                    reason = f"Fast SMA({self.fast_period}) is {'above' if f_curr > s_curr else 'below'} Slow SMA({self.slow_period})"

        elif self.strategy == "RSI_MOMENTUM":
            if len(raw_closes) >= 16:
                df = pd.DataFrame({"close": raw_closes})
                delta = df["close"].diff()
                gain = delta.where(delta > 0, 0).rolling(self.rsi_period).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(self.rsi_period).mean()
                rs = gain / loss.replace(0, 0.0001)
                rsi = 100 - (100 / (1 + rs))
                current_rsi = round(float(rsi.iloc[-1]), 1)

                if current_rsi <= self.rsi_oversold:
                    signal = "BUY"
                    reason = f"RSI Oversold ({current_rsi} <= {self.rsi_oversold})"
                elif current_rsi >= self.rsi_overbought:
                    signal = "SELL"
                    reason = f"RSI Overbought ({current_rsi} >= {self.rsi_overbought})"
                else:
                    signal = "HOLD"
                    reason = f"RSI Neutral ({current_rsi})"

        # 3. 200 EMA Macro Filter
        if signal == "BUY" and self.use_ema200_filter and ema_200 is not None:
            if current_price < ema_200:
                signal = "HOLD"
                reason = f"Macro Filter Active: Price (${current_price:,.2f}) below 200 EMA (${ema_200:,.2f}). Buy rejected."

        # 4. ORDER EXECUTION (If bot running and no active position)
        action_executed = "MONITORING"
        if allow_order_execution and not pos:
            # Check position limits
            total_held = len([p for p in held_positions_map.values() if p.quantity > 0])
            can_trade = (self.strategy == "123_REVERSAL") or (total_held < self.max_positions)

            if signal == "BUY" and can_trade:
                trade_sl = rev_info['stop_loss'] if rev_info else round(current_price * (1.0 - self.stop_loss_pct / 100), 2)
                trade_tp = rev_info['target'] if rev_info else round(current_price * (1.0 + self.take_profit_pct / 100), 2)

                # Sizing: quantity = notional / price
                notional = self.allocation_per_trade * self.leverage
                order_qty = notional / current_price

                res = CryptoTradingEngine.execute_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=order_qty,
                    leverage=self.leverage,
                    stop_loss=trade_sl,
                    take_profit=trade_tp
                )
                if res.get("success"):
                    action_executed = "BOUGHT"
                    self.add_log(f"⚡ [AUTO-BOUGHT] {display_name}: Opened {self.leverage}x LONG ({order_qty:.4f} coins @ ${current_price:,.2f}) | SL: ${trade_sl:,.2f} | TP: ${trade_tp:,.2f} | {reason}", "SUCCESS")
                else:
                    action_executed = "FAILED"
                    self.add_log(f"⚠️ [BUY REJECTED] {display_name}: {res.get('message')}", "WARNING")

        return {
            "symbol": symbol,
            "displayName": display_name,
            "price": round(float(current_price), 2),
            "change_pct": round(float(change_pct), 2),
            "signal": signal,
            "reason": reason,
            "action_executed": action_executed,
            "ema_200": ema_200,
            "trend_200": trend_200,
            "reversal_info": rev_info
        }

    def _compute_123_reversal(self, closes: list, highs: list, lows: list, current_price: float) -> dict:
        """Calculates 1-2-3 Reversal Pattern (Swing Point 1, Neckline Point 2, Higher Low Point 3)"""
        if len(closes) < 15:
            return None
        try:
            n = len(closes)
            # Find swing low in last 15 bars (Point 1)
            p1_idx = int(np.argmin(lows[-15:-5])) + (n - 15)
            p1_price = lows[p1_idx]

            # Find swing high following P1 (Point 2 Neckline)
            if p1_idx >= n - 3:
                return None
            p2_rel = int(np.argmax(highs[p1_idx:-2]))
            p2_idx = p1_idx + p2_rel
            p2_price = highs[p2_idx]

            # Find higher low after P2 (Point 3)
            if p2_idx >= n - 1:
                return None
            p3_rel = int(np.argmin(lows[p2_idx:]))
            p3_idx = p2_idx + p3_rel
            p3_price = lows[p3_idx]

            # Criteria for Bullish 1-2-3: Point 3 is higher than Point 1, and current price breaks above Point 2
            if p3_price > p1_price and current_price >= p2_price:
                height = p2_price - p1_price
                target = round(p2_price + (height * 1.0), 2)
                stop_loss = round(p3_price * 0.99, 2)
                score = min(98, int(75 + ((current_price - p2_price) / p2_price * 100 * 5)))
                return {
                    "detected": True,
                    "side": "BUY",
                    "p1": round(float(p1_price), 2),
                    "p2": round(float(p2_price), 2),
                    "p3": round(float(p3_price), 2),
                    "target": target,
                    "stop_loss": stop_loss,
                    "reversal_score": score
                }
        except Exception:
            pass
        return None

    def evaluate_signals(self, target_symbol: str = None) -> list:
        """Performs full basket sweep across crypto assets"""
        from app import create_app
        app = create_app()

        with app.app_context():
            held_map = {}
            try:
                positions = CryptoPosition.query.filter(CryptoPosition.quantity > 0).all()
                for p in positions:
                    held_map[p.symbol] = p
            except Exception:
                pass

            if target_symbol:
                symbols_to_scan = [target_symbol.strip().upper()]
            elif self.scan_mode == "SINGLE":
                symbols_to_scan = [self.single_symbol]
            elif self.scan_mode == "TOP_10":
                symbols_to_scan = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'BNB-USD', 'XRP-USD', 'DOGE-USD', 'ADA-USD', 'AVAX-USD', 'LINK-USD', 'DOT-USD']
            else:
                symbols_to_scan = CRYPTO_DEFAULT_PAIRS

            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                future_map = {
                    executor.submit(self.evaluate_single_coin, sym, held_map, self.is_running): sym
                    for sym in symbols_to_scan
                }
                for future in concurrent.futures.as_completed(future_map):
                    try:
                        res = future.result()
                        if res: results.append(res)
                    except Exception:
                        pass

            self.scanner_results = results
            self.last_scan_time = datetime.now()
            self.total_scans += 1
            return results

    def _run_loop(self):
        while self.is_running:
            try:
                self.evaluate_signals()
            except Exception as e:
                self.add_log(f"Scan loop error: {str(e)}", "ERROR")

            # Sleep in 1s slices to handle quick pause
            for _ in range(self.interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

crypto_bot_instance = CryptoAutoTradingBot()
