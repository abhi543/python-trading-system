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
from app.services.paper_trader import PaperTradingEngine
from app.models.trading import Position, Account, NIFTY_50_DEFAULT, NIFTY_200_DEFAULT

def _get_bot_state_file():
    tmp = os.environ.get('TMPDIR', '/tmp')
    if os.path.exists(tmp) and os.access(tmp, os.W_OK):
        return os.path.join(tmp, 'trading_bot_state.json')
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    return os.path.join(base, 'trading_bot_state.json')

def _save_persistent_bot_state(data: dict):
    try:
        path = _get_bot_state_file()
        with open(path, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass

def _load_persistent_bot_state() -> dict:
    try:
        path = _get_bot_state_file()
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

_price_drift_state = {}

def is_nse_market_open() -> bool:
    from datetime import datetime, timezone, timedelta
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    # Check weekday (0 = Monday, 4 = Friday, 5 = Saturday, 6 = Sunday)
    if now_ist.weekday() >= 5:
        return False
    current_minutes = now_ist.hour * 60 + now_ist.minute
    # 09:15 to 15:30 IST
    return (9 * 60 + 15) <= current_minutes <= (15 * 60 + 30)

def get_after_hours_price(symbol: str, base_price: float, pos=None) -> float:
    import random
    global _price_drift_state
    if symbol not in _price_drift_state:
        # Bias held positions slightly upward so Breakeven & Trailing Stops showcase
        init_trend = 1 if pos else random.choice([-1, 1, 1])
        _price_drift_state[symbol] = {
            "drift_pct": 0.0,
            "trend": init_trend,
            "step_count": 0
        }
    
    st = _price_drift_state[symbol]
    st["step_count"] += 1
    
    # Position price walk: realistic drift with momentum
    drift_step = random.gauss(0.002 * st["trend"], 0.005)
    st["drift_pct"] = max(-0.045, min(0.065, st["drift_pct"] + drift_step))
    
    # 10% chance to flip drift direction
    if random.random() < 0.12:
        st["trend"] *= -1
        
    return round(base_price * (1.0 + st["drift_pct"]), 2)

class AutoTradingBot:
    def __init__(self):
        self.is_running = False
        self.scan_mode = "ALL_NIFTY_200"  # "ALL_NIFTY_200", "ALL_NIFTY_50", or "SINGLE"
        self.single_symbol = "RELIANCE.NS"
        self.strategy = "SMA_CROSSOVER"  # "SMA_CROSSOVER" or "RSI_MOMENTUM"
        self.allocation_per_trade = 15000.0  # ₹15,000 allocated per stock
        self.max_positions = 3               # Max 3 concurrent active positions
        self.interval_seconds = 60           # Scan interval: 1 minute (60s)
        self.fast_period = 9
        self.slow_period = 21
        self.rsi_period = 14
        self.rsi_oversold = 35
        self.rsi_overbought = 65
        self.stop_loss_pct = 1.0             # 1.0% Stop loss
        self.take_profit_pct = 1.0           # 1.0% Take profit
        
        # Option A: Macro Trend & Volume Confirmation
        self.use_ema200_filter = True        # Only BUY if price >= 200 EMA
        self.use_volume_filter = False       # Volume surge confirmation
        
        # Option B: Trailing Stop-Loss & Dynamic ATR Exits
        self.use_trailing_stop = True        # Breakeven & Trailing stop
        self.trailing_trigger_pct = 1.0      # Breakeven ratchet triggered at +1.0%
        self.use_atr_exits = False           # Dynamic ATR-based exits
        self.atr_multiplier_sl = 1.5         # 1.5x ATR initial stop
        self.atr_multiplier_tp = 3.0         # 3.0x ATR profit target
        
        self.logs = []
        self.last_run_time = None
        self.last_scan_summary = {
            "total_scanned": 0,
            "buys_found": 0,
            "sells_found": 0,
            "trades_executed": 0,
            "scanner_results": []
        }
        self.thread = None
        self.lock = threading.Lock()

    def add_log(self, message: str, level: str = "INFO"):
        now_str = datetime.now().strftime("%H:%M:%S")
        entry = {
            "timestamp": now_str,
            "message": message,
            "level": level
        }
        with self.lock:
            self.logs.insert(0, entry)
            if len(self.logs) > 100:
                self.logs.pop()

    def get_status(self) -> dict:
        persisted = _load_persistent_bot_state()
        is_active = self.is_running or persisted.get("is_running", False)
        after_hours = not is_nse_market_open()
        return {
            "is_running": is_active,
            "is_after_hours": after_hours,
            "market_status": "AFTER_HOURS_SIMULATION" if after_hours else "LIVE_NSE_MARKET",
            "scan_mode": self.scan_mode,
            "single_symbol": self.single_symbol,
            "strategy": self.strategy,
            "allocation_per_trade": self.allocation_per_trade,
            "max_positions": self.max_positions,
            "is_unlimited_positions": (self.strategy == "123_REVERSAL"),
            "interval_seconds": self.interval_seconds,
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "rsi_period": self.rsi_period,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "use_ema200_filter": self.use_ema200_filter,
            "use_volume_filter": self.use_volume_filter,
            "use_trailing_stop": self.use_trailing_stop,
            "trailing_trigger_pct": self.trailing_trigger_pct,
            "use_atr_exits": self.use_atr_exits,
            "atr_multiplier_sl": self.atr_multiplier_sl,
            "atr_multiplier_tp": self.atr_multiplier_tp,
            "last_run_time": self.last_run_time,
            "scan_summary": self.last_scan_summary,
            "logs": self.logs[:30]
        }

    def update_config(self, config: dict = None):
        """Dynamically updates trading strategy, parameters, and risk guards in real-time"""
        if not config:
            return
        if "scan_mode" in config:
            self.scan_mode = config["scan_mode"]
        if "single_symbol" in config and config["single_symbol"]:
            self.single_symbol = config["single_symbol"].strip().upper()
        if "strategy" in config and config["strategy"]:
            old_strat = self.strategy
            self.strategy = config["strategy"]
            if old_strat != self.strategy:
                self.add_log(f"🔄 Strategy updated: switched from {old_strat} to {self.strategy}", "INFO")
        if "allocation_per_trade" in config:
            self.allocation_per_trade = float(config["allocation_per_trade"])
        if "max_positions" in config:
            self.max_positions = int(config["max_positions"])
        if "fast_period" in config:
            self.fast_period = int(config["fast_period"])
        if "slow_period" in config:
            self.slow_period = int(config["slow_period"])
        if "rsi_period" in config:
            self.rsi_period = int(config["rsi_period"])
        if "stop_loss_pct" in config:
            self.stop_loss_pct = float(config["stop_loss_pct"])
        if "take_profit_pct" in config:
            self.take_profit_pct = float(config["take_profit_pct"])
        if "interval_seconds" in config:
            self.interval_seconds = int(config["interval_seconds"])
        if "use_ema200_filter" in config:
            self.use_ema200_filter = bool(config["use_ema200_filter"])
        if "use_volume_filter" in config:
            self.use_volume_filter = bool(config["use_volume_filter"])
        if "use_trailing_stop" in config:
            self.use_trailing_stop = bool(config["use_trailing_stop"])
        if "use_atr_exits" in config:
            self.use_atr_exits = bool(config["use_atr_exits"])

    def start(self, config: dict = None):
        if config:
            self.update_config(config)

        self.is_running = True
        after_hours = not is_nse_market_open()
        _save_persistent_bot_state({
            "is_running": True,
            "scan_mode": self.scan_mode,
            "strategy": self.strategy,
            "allocation_per_trade": self.allocation_per_trade,
            "use_ema200_filter": self.use_ema200_filter,
            "use_trailing_stop": self.use_trailing_stop,
            "use_atr_exits": self.use_atr_exits
        })
        mode_label = "ALL NIFTY 50 STOCKS" if self.scan_mode == "ALL_NIFTY_50" else self.single_symbol
        ema_tag = "200 EMA Filter [ON]" if self.use_ema200_filter else "200 EMA Filter [OFF]"
        trail_tag = "Trailing Stop [ON]" if self.use_trailing_stop else "Trailing Stop [OFF]"
        session_tag = "🌙 AFTER-MARKET HOURS (ACTIVE 24/7)" if after_hours else "☀️ LIVE NSE MARKET SESSION"
        self.add_log(f"Auto-Bot ACTIVATED in {session_tag} on {mode_label} | {self.strategy} | {ema_tag} | {trail_tag} | Alloc: ₹{self.allocation_per_trade:,.0f}", "SUCCESS")

        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

        return {"success": True, "message": f"Bot activated on {mode_label}", "status": self.get_status()}

    def stop(self):
        self.is_running = False
        _save_persistent_bot_state({"is_running": False})
        self.add_log("🛑 Auto-Bot PAUSED and STOPPED by operator. All automated sweeps and order executions are strictly halted.", "WARNING")
        return {"success": True, "message": "Bot stopped", "status": self.get_status()}

    def evaluate_single_stock(self, symbol: str, held_positions_map: dict, stock_data: dict = None, allow_order_execution: bool = True) -> dict:
        """Evaluates indicators, checks risk exits, and computes buy/sell signal for a stock"""
        if stock_data is None:
            stock_data = MarketDataService.get_stock_analysis_data(symbol)
            
        if "error" in stock_data:
            return {"symbol": symbol, "signal": "ERROR", "error": stock_data["error"]}

        current_price = stock_data["price"]
        change_pct = stock_data.get("percent_change", 0.0)
        closes = stock_data.get("closes", [])
        ema_200 = stock_data.get("ema_200")
        trend_200 = stock_data.get("trend_200", "NEUTRAL")
        vol_ratio = stock_data.get("vol_ratio", 1.0)
        atr_14 = stock_data.get("atr_14", round(current_price * 0.025, 2))
        display_name = symbol.replace('.NS', '')

        # Check existing position
        pos = held_positions_map.get(symbol)
        
        # 1. OPTION B: Dynamic Trailing Stop-Loss & Take-Profit check
        if pos and pos.quantity > 0:
            avg_price = pos.average_price
            pnl_pct = ((current_price - avg_price) / avg_price) * 100

            # Track peak price attained
            peak = max(getattr(pos, 'peak_price', 0.0) or avg_price, current_price)
            if hasattr(pos, 'peak_price'):
                pos.peak_price = peak

            # OPTION B: Trailing Stop-Loss & Breakeven Shield (Only for SMA / RSI strategies)
            if self.use_trailing_stop and self.strategy != "123_REVERSAL":
                # Stage 1: Breakeven Shield at +2.0%
                if pnl_pct >= self.trailing_trigger_pct and not getattr(pos, 'trailing_active', False):
                    pos.trailing_active = True
                    if (getattr(pos, 'stop_loss', 0.0) or 0.0) < avg_price:
                        pos.stop_loss = avg_price
                        try:
                            db.session.commit()
                        except Exception:
                            pass
                        self.add_log(f"🛡️ [BREAKEVEN SHIELD] {display_name}: Gain reached +{pnl_pct:.2f}%. Stop-loss raised to Breakeven (₹{avg_price:.2f}). Trade is 100% Risk-Free!", "SUCCESS")

                # Stage 2: Trail upward behind higher peaks
                if getattr(pos, 'trailing_active', False):
                    trail_distance = (self.atr_multiplier_sl * atr_14) if self.use_atr_exits else (peak * 0.02)
                    dynamic_stop = round(peak - trail_distance, 2)
                    current_sl = getattr(pos, 'stop_loss', 0.0) or 0.0
                    if dynamic_stop > current_sl and dynamic_stop > avg_price:
                        pos.stop_loss = dynamic_stop
                        try:
                            db.session.commit()
                        except Exception:
                            pass
                        self.add_log(f"📈 [TRAILING STOP TRAIL] {display_name}: High ₹{peak:.2f}! Stop raised to ₹{dynamic_stop:.2f} (Locking +{((dynamic_stop-avg_price)/avg_price*100):.1f}%)", "SUCCESS")

            # Check if Trailing / Dynamic Stop-Loss triggered
            sl_level = getattr(pos, 'stop_loss', 0.0) or 0.0
            if sl_level > 0 and current_price <= sl_level:
                lock_pnl = ((current_price - avg_price) / avg_price) * 100
                if allow_order_execution:
                    self.add_log(f"🛑 [STOP HIT] {display_name}: Liquidated @ ₹{current_price:.2f} (Stop was ₹{sl_level:.2f}, Result: {lock_pnl:+.2f}%)", "ERROR" if lock_pnl < 0 else "SUCCESS")
                    exec_res = PaperTradingEngine.execute_order(symbol, "SELL", pos.quantity, execution_price=current_price)
                return {
                    "symbol": symbol, "displayName": display_name, "price": current_price,
                    "change_pct": change_pct, "signal": "STOP_LOSS", "reason": f"Indicator Stop-Loss hit @ ₹{current_price:.2f} (SL: ₹{sl_level:.2f}, {lock_pnl:+.2f}%)",
                    "action_executed": "STOP_EXIT" if allow_order_execution else "OFFLINE (No orders placed)", "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14
                }

            # Check if Fixed % Stop-Loss triggered (DISABLED for 123_REVERSAL strategy - strictly uses indicator stop)
            if self.strategy != "123_REVERSAL" and pnl_pct <= -abs(self.stop_loss_pct):
                if allow_order_execution:
                    self.add_log(f"[STOP-LOSS] {display_name}: Hit -{abs(pnl_pct):.2f}% <= -{self.stop_loss_pct}%. Liquidating {pos.quantity} shares @ ₹{current_price:.2f}", "ERROR")
                    exec_res = PaperTradingEngine.execute_order(symbol, "SELL", pos.quantity, execution_price=current_price)
                return {
                    "symbol": symbol, "displayName": display_name, "price": current_price,
                    "change_pct": change_pct, "signal": "STOP_LOSS", "reason": f"Stop-Loss triggered at {pnl_pct:.2f}%",
                    "action_executed": "LIQUIDATED" if allow_order_execution else "OFFLINE (No orders placed)", "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14
                }

            # Check Take-Profit:
            tp_level = getattr(pos, 'take_profit', 0.0) or 0.0
            is_tp_hit = False
            tp_reason = ""
            if tp_level > 0 and current_price >= tp_level:
                is_tp_hit = True
                tp_reason = f"Indicator Target achieved @ ₹{current_price:.2f} (Target: ₹{tp_level:.2f}, +{pnl_pct:.2f}%)"
            elif self.strategy != "123_REVERSAL" and (pnl_pct >= abs(self.take_profit_pct)):
                is_tp_hit = True
                tp_reason = f"Take-Profit target achieved at +{pnl_pct:.2f}%"

            if is_tp_hit:
                if allow_order_execution:
                    self.add_log(f"🎯 [TAKE-PROFIT] {display_name}: Hit +{pnl_pct:.2f}%. Selling {pos.quantity} shares @ ₹{current_price:.2f} to lock gains! ({tp_reason})", "SUCCESS")
                    exec_res = PaperTradingEngine.execute_order(symbol, "SELL", pos.quantity, execution_price=current_price)
                return {
                    "symbol": symbol, "displayName": display_name, "price": current_price,
                    "change_pct": change_pct, "signal": "TAKE_PROFIT", "reason": tp_reason,
                    "action_executed": "PROFIT_LOCKED" if allow_order_execution else "OFFLINE (No orders placed)", "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14
                }

        # 2. Strategy Indicator Evaluation
        if not closes or len(closes) < max(self.slow_period, self.rsi_period) + 2:
            return {
                "symbol": symbol, "displayName": display_name, "price": current_price,
                "change_pct": change_pct, "signal": "HOLD", "reason": "Insufficient candles",
                "fast_sma": None, "slow_sma": None, "rsi": None, "action_executed": None,
                "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14
            }

        s_closes = pd.Series(closes)

        signal = "HOLD"
        reason = ""
        fast_sma = None
        slow_sma = None
        rsi_val = None
        suggested_sl = None
        suggested_tp = None
        reversal_score = None
        reversal_grade = None

        if self.strategy == "123_REVERSAL":
            from app.services.reversal_123 import Reversal123Engine
            highs = stock_data.get("highs") or closes
            lows = stock_data.get("lows") or closes
            opens = stock_data.get("opens") or closes
            vols = stock_data.get("volumes") or [1.0] * len(closes)

            df_pat = pd.DataFrame({
                "high": highs,
                "low": lows,
                "close": closes,
                "open": opens,
                "volume": vols
            })
            pat_res = Reversal123Engine.evaluate_123_reversal(df_pat)
            signal = pat_res.get("signal", "HOLD")
            reason = pat_res.get("reason", "1-2-3 Scanning")
            reversal_score = pat_res.get("score")
            reversal_grade = pat_res.get("grade")
            suggested_sl = pat_res.get("stop_loss_price")
            suggested_tp = pat_res.get("target_price")

        elif self.strategy == "SMA_CROSSOVER":
            sma_fast = s_closes.rolling(window=self.fast_period).mean()
            sma_slow = s_closes.rolling(window=self.slow_period).mean()
            
            fast_curr = sma_fast.iloc[-1]
            fast_prev = sma_fast.iloc[-2]
            slow_curr = sma_slow.iloc[-1]
            slow_prev = sma_slow.iloc[-2]

            fast_sma = round(float(fast_curr), 2)
            slow_sma = round(float(slow_curr), 2)

            if fast_curr > slow_curr and fast_prev <= slow_prev:
                signal = "BUY"
                reason = f"Golden Cross (SMA9: {fast_curr:.1f} > SMA21: {slow_curr:.1f})"
            elif fast_curr < slow_curr and fast_prev >= slow_prev:
                signal = "SELL"
                reason = f"Death Cross (SMA9: {fast_curr:.1f} < SMA21: {slow_curr:.1f})"
            else:
                trend = "Bullish" if fast_curr > slow_curr else "Bearish"
                reason = f"{trend} trend (Fast: {fast_curr:.1f}, Slow: {slow_curr:.1f})"

        elif self.strategy == "RSI_MOMENTUM":
            delta = s_closes.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
            rs = gain / (loss + 1e-9)
            rsi = 100 - (100 / (1 + rs))
            current_rsi = float(rsi.iloc[-1])
            rsi_val = round(current_rsi, 1)

            if current_rsi < self.rsi_oversold:
                signal = "BUY"
                reason = f"Oversold Bounce (RSI: {current_rsi:.1f} < {self.rsi_oversold})"
            elif current_rsi > self.rsi_overbought:
                signal = "SELL"
                reason = f"Overbought Exhaustion (RSI: {current_rsi:.1f} > {self.rsi_overbought})"
            else:
                reason = f"Neutral Momentum (RSI: {current_rsi:.1f})"

        # OPTION A: Filter BUY signals with 200 EMA Macro Trend & Volume Surge
        filter_status = None
        if signal == "BUY":
            if self.use_ema200_filter and trend_200 == "BEARISH":
                signal = "HOLD"
                reason = f"BUY Filtered: Bearish Trend (Price ₹{current_price:.1f} < 200 EMA ₹{ema_200})"
                filter_status = "FILTERED_200EMA"
            elif self.use_volume_filter and vol_ratio < 1.0:
                signal = "HOLD"
                reason = f"BUY Filtered: Low Volume Surge ({vol_ratio:.2f}x < 1.0x avg)"
                filter_status = "FILTERED_VOLUME"

        return {
            "symbol": symbol,
            "displayName": display_name,
            "price": current_price,
            "change_pct": change_pct,
            "signal": signal,
            "reason": reason,
            "fast_sma": fast_sma,
            "slow_sma": slow_sma,
            "rsi": rsi_val,
            "reversal_score": reversal_score,
            "reversal_grade": reversal_grade,
            "suggested_sl": suggested_sl,
            "suggested_tp": suggested_tp,
            "ema_200": ema_200,
            "trend_200": trend_200,
            "vol_ratio": vol_ratio,
            "atr_14": atr_14,
            "filter_status": filter_status,
            "action_executed": None
        }

    def evaluate_signals(self, simulation_event: str = None, target_symbol: str = None) -> dict:
        """Runs the scanner across all Nifty 50 constituents (or single stock) and auto-trades"""
        import random
        self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Handle After-Hours Instant Test Triggers
        if simulation_event == "FORCE_BUY":
            force_sym = (target_symbol or "RELIANCE.NS").strip().upper()
            quote = MarketDataService.get_quote(force_sym)
            p = quote.get("price", 1500.0) if "error" not in quote else 1500.0
            trade_qty = max(1, int(self.allocation_per_trade / p))
            PaperTradingEngine.execute_order(force_sym, "BUY", trade_qty, execution_price=p)
            pos_rec = Position.query.filter_by(symbol=force_sym).first()
            if pos_rec:
                pos_rec.stop_loss = round(p * (1 - self.stop_loss_pct / 100), 2)
                pos_rec.take_profit = round(p * (1 + self.take_profit_pct / 100), 2)
                pos_rec.peak_price = p
                pos_rec.trailing_active = False
                try:
                    db.session.commit()
                except Exception:
                    pass
            self.add_log(f"⚡ [TEST SIGNAL] Instant BUY executed: {trade_qty} shares of {force_sym.replace('.NS', '')} @ ₹{p:.2f} (Total: ₹{trade_qty*p:,.2f})", "SUCCESS")

        elif simulation_event in ["BULL_SURGE", "TRAIL_PUSH", "STOP_SHOCK"]:
            # If no open positions exist, automatically seed one so the user can immediately observe the risk exit!
            held_positions = Position.query.filter(Position.quantity > 0).all()
            if not held_positions:
                seed_sym = "RELIANCE.NS"
                quote = MarketDataService.get_quote(seed_sym)
                p = quote.get("price", 1500.0) if "error" not in quote else 1500.0
                trade_qty = max(1, int(self.allocation_per_trade / p))
                PaperTradingEngine.execute_order(seed_sym, "BUY", trade_qty, execution_price=p)
                pos_rec = Position.query.filter_by(symbol=seed_sym).first()
                if pos_rec:
                    pos_rec.stop_loss = round(p * (1 - self.stop_loss_pct / 100), 2)
                    pos_rec.take_profit = round(p * (1 + self.take_profit_pct / 100), 2)
                    pos_rec.peak_price = p
                    pos_rec.trailing_active = False
                    try:
                        db.session.commit()
                    except Exception:
                        pass
                self.add_log(f"💡 [SIMULATOR] Auto-seeded 1 test position ({seed_sym.replace('.NS','')}) for instant guard verification.", "INFO")

        # Get existing positions & cash balance
        held_positions = Position.query.filter(Position.quantity > 0).all()
        held_map = {p.symbol: p for p in held_positions}
        account = Account.query.first()
        available_cash = account.cash_balance if account else 0.0

        if self.scan_mode == "ALL_NIFTY_200":
            target_stocks = NIFTY_200_DEFAULT
        elif self.scan_mode == "ALL_NIFTY_50":
            target_stocks = NIFTY_50_DEFAULT
        else:
            target_stocks = [self.single_symbol]
        
        # Parallel high-speed data fetch for stock universe
        stock_data_map = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_stock = {
                executor.submit(MarketDataService.get_stock_analysis_data, sym): sym 
                for sym in target_stocks
            }
            for future in concurrent.futures.as_completed(future_to_stock):
                sym = future_to_stock[future]
                try:
                    data = future.result()
                    if "error" not in data:
                        stock_data_map[sym] = data
                except Exception:
                    pass

        # Apply Simulated Price Ticks if Simulation Mode / Event is active or in after-market hours
        after_hours = not is_nse_market_open()
        for symbol, stock_data in stock_data_map.items():
            pos = held_map.get(symbol)
            orig_p = stock_data["price"]
            
            if simulation_event == "BULL_SURGE" and pos:
                # Force price up to +2.5% above entry to trigger Breakeven Shield
                surged_price = round(pos.average_price * 1.025, 2)
                stock_data["price"] = surged_price
                stock_data["percent_change"] = round(((surged_price - pos.average_price) / pos.average_price) * 100, 2)
            elif simulation_event == "TRAIL_PUSH" and pos:
                # Force price up to +4.5% to trail stop upward
                trailed_price = round(pos.average_price * 1.045, 2)
                stock_data["price"] = trailed_price
                stock_data["percent_change"] = round(((trailed_price - pos.average_price) / pos.average_price) * 100, 2)
            elif simulation_event == "STOP_SHOCK" and pos:
                # Drop price to -3.5% below stop loss level to trigger liquidation
                shocked_price = round(pos.average_price * 0.965, 2)
                stock_data["price"] = shocked_price
                stock_data["percent_change"] = -3.5
            elif after_hours or simulation_event in ["RANDOM_WALK", "AFTER_HOURS_WALK"]:
                # Continuous dynamic price drift across after-market hours
                sim_price = get_after_hours_price(symbol, orig_p, pos)
                stock_data["price"] = sim_price
                if "closes" in stock_data and len(stock_data["closes"]) > 0:
                    stock_data["closes"][-1] = sim_price
                drift_pct = ((sim_price - orig_p) / orig_p) * 100
                stock_data["percent_change"] = round((stock_data.get("percent_change", 0.0) or 0.0) + drift_pct, 2)
            elif simulation_event == "RANDOM_WALK":
                # Micro fluctuation: random drift between -0.4% and +0.5%
                drift = random.uniform(-0.004, +0.005)
                walk_price = round(orig_p * (1.0 + drift), 2)
                stock_data["price"] = walk_price
                stock_data["percent_change"] = round((stock_data.get("percent_change", 0.0) or 0.0) + (drift * 100), 2)

        scanner_results = []
        buys_found = 0
        sells_found = 0
        trades_executed = 0

        # Autonomous trade order execution strictly requires bot to be running (self.is_running is True)
        # Or explicit manual simulation triggers (FORCE_BUY, STOP_SHOCK)
        is_explicit_test = simulation_event in ["FORCE_BUY", "STOP_SHOCK"]
        can_execute_trades = bool(self.is_running or is_explicit_test)

        for symbol in target_stocks:
            stock_data = stock_data_map.get(symbol)
            if not stock_data:
                continue

            eval_res = self.evaluate_single_stock(symbol, held_map, stock_data, allow_order_execution=can_execute_trades)
            if eval_res.get("signal") == "ERROR":
                continue

            sig = eval_res["signal"]
            price = eval_res["price"]
            display_name = eval_res["displayName"]

            # AUTO-TRADE EXECUTION LOGIC:
            if sig == "BUY":
                buys_found += 1
                if can_execute_trades:
                    # For 123_REVERSAL strategy: UNLIMITED positions (no limit on positions)
                    # For other strategies: capped at self.max_positions
                    is_unlimited_positions = (self.strategy == "123_REVERSAL")
                    has_position_slot = is_unlimited_positions or (len(held_map) < self.max_positions)

                    if symbol not in held_map and has_position_slot:
                        # Calculate quantity based on capital allocation
                        trade_qty = max(1, int(self.allocation_per_trade / price))
                        total_cost = trade_qty * price

                        if available_cash >= total_cost:
                            exec_res = PaperTradingEngine.execute_order(symbol, "BUY", trade_qty, execution_price=price)
                            if exec_res.get("success"):
                                trades_executed += 1
                                available_cash -= total_cost
                                eval_res["action_executed"] = f"BOUGHT {trade_qty} SHARES"
                                
                                # Set Initial Stop-Loss and Take-Profit targets on position
                                pos_rec = Position.query.filter_by(symbol=symbol).first()
                                if pos_rec:
                                    atr_val = eval_res.get("atr_14") or (price * 0.025)
                                    if self.strategy == "123_REVERSAL":
                                        # Strictly follow the target and stop loss as given by the 1-2-3 indicator (NO 1% applied)
                                        pos_rec.stop_loss = eval_res.get("suggested_sl") or round(price * 0.98, 2)
                                        pos_rec.take_profit = eval_res.get("suggested_tp") or round(price * 1.04, 2)
                                    elif eval_res.get("suggested_sl") and eval_res.get("suggested_tp"):
                                        pos_rec.stop_loss = eval_res["suggested_sl"]
                                        pos_rec.take_profit = eval_res["suggested_tp"]
                                    elif self.use_atr_exits:
                                        pos_rec.stop_loss = round(price - (self.atr_multiplier_sl * atr_val), 2)
                                        pos_rec.take_profit = round(price + (self.atr_multiplier_tp * atr_val), 2)
                                    else:
                                        pos_rec.stop_loss = round(price * (1 - self.stop_loss_pct / 100), 2)
                                        pos_rec.take_profit = round(price * (1 + self.take_profit_pct / 100), 2)
                                    pos_rec.peak_price = price
                                    pos_rec.trailing_active = False
                                    try:
                                        db.session.commit()
                                    except Exception:
                                        pass

                                chg_str = f" (Charges: ₹{exec_res['charges']['total_charges']:.2f})" if "charges" in exec_res else ""
                                self.add_log(f"⚡ AUTO-BOUGHT {trade_qty} shares of {display_name} @ ₹{price:.2f} (Total: ₹{total_cost:,.2f}{chg_str}) | Target: ₹{pos_rec.take_profit:.2f} | Stop: ₹{pos_rec.stop_loss:.2f}", "SUCCESS")
                                # Add to local held map to prevent double-buys
                                held_map[symbol] = Position(
                                    symbol=symbol,
                                    quantity=trade_qty,
                                    average_price=price,
                                    peak_price=price,
                                    stop_loss=pos_rec.stop_loss if pos_rec else 0.0,
                                    take_profit=pos_rec.take_profit if pos_rec else 0.0
                                )
                        else:
                            self.add_log(f"BUY signal on {display_name}, but insufficient cash (Needed ₹{total_cost:,.2f}, Avail ₹{available_cash:,.2f})", "WARNING")
                else:
                    eval_res["action_executed"] = "OFFLINE (Bot Stopped)"

            elif sig == "SELL":
                sells_found += 1
                if can_execute_trades:
                    # If we hold this stock, exit position
                    if symbol in held_map:
                        pos = held_map[symbol]
                        exec_res = PaperTradingEngine.execute_order(symbol, "SELL", pos.quantity, execution_price=price)
                        if exec_res.get("success"):
                            trades_executed += 1
                            eval_res["action_executed"] = f"SOLD {pos.quantity} SHARES"
                            chg_str = f" (Charges: ₹{exec_res['charges']['total_charges']:.2f})" if "charges" in exec_res else ""
                            self.add_log(f"⚡ AUTO-SOLD {pos.quantity} shares of {display_name} @ ₹{price:.2f}{chg_str} | {eval_res['reason']}", "WARNING")
                            del held_map[symbol]
                else:
                    eval_res["action_executed"] = "OFFLINE (Bot Stopped)"

            scanner_results.append(eval_res)

        # Sort results: BUY signals first, then SELL, then highest volume/changes
        scanner_results.sort(key=lambda x: (0 if x["signal"] == "BUY" else (1 if x["signal"] in ("SELL", "STOP_LOSS", "TAKE_PROFIT") else 2), -abs(x.get("change_pct") or 0)))

        self.last_scan_summary = {
            "total_scanned": len(scanner_results),
            "buys_found": buys_found,
            "sells_found": sells_found,
            "trades_executed": trades_executed,
            "active_positions_count": len(held_map),
            "max_positions_limit": self.max_positions,
            "scanner_results": scanner_results
        }

        sweep_tag = "🌙 AFTER-MARKET SWEEP" if after_hours else "NIFTY 50 Scan Complete"
        self.add_log(f"{sweep_tag}: Scanned {len(scanner_results)} stocks | 🟢 {buys_found} BUY signals | 🔴 {sells_found} SELL signals | {trades_executed} Trades executed", "INFO")

        return self.last_scan_summary

    def _run_loop(self):
        from run import app
        while self.is_running:
            try:
                with app.app_context():
                    self.evaluate_signals()
            except Exception as e:
                self.add_log(f"Loop exception: {e}", "ERROR")

            for _ in range(self.interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

bot_instance = AutoTradingBot()
