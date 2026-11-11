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

def _get_bot_state_file(filename="trading_bot_state.json"):
    tmp = os.environ.get('TMPDIR', '/tmp')
    if os.path.exists(tmp) and os.access(tmp, os.W_OK):
        return os.path.join(tmp, filename)
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    return os.path.join(base, filename)

def _save_persistent_bot_state(data: dict, filename="trading_bot_state.json"):
    try:
        path = _get_bot_state_file(filename)
        with open(path, 'w') as f:
            json.dump(data, f, default=str)
    except Exception:
        pass

def _load_persistent_bot_state(filename="trading_bot_state.json") -> dict:
    try:
        path = _get_bot_state_file(filename)
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
    base = (getattr(pos, 'average_price', None) or base_price) if pos else base_price
    if symbol not in _price_drift_state:
        # Held positions start with realistic initial drift so active P&L is immediately visible
        is_pos_short = (getattr(pos, 'side', 'LONG') == 'SHORT') if pos else False
        init_trend = -1 if is_pos_short else (1 if pos else random.choice([-1, 1, 1]))
        init_drift = random.uniform(0.004, 0.012) * init_trend if pos else random.uniform(-0.005, 0.005)
        _price_drift_state[symbol] = {
            "drift_pct": init_drift,
            "trend": init_trend,
            "step_count": 1
        }
    
    st = _price_drift_state[symbol]
    st["step_count"] += 1
    
    # Position price walk: realistic drift with momentum
    drift_step = random.gauss(0.002 * st["trend"], 0.005)
    st["drift_pct"] = max(-0.045, min(0.065, st["drift_pct"] + drift_step))
    
    # 12% chance to flip drift direction
    if random.random() < 0.12:
        st["trend"] *= -1
        
    return round(base * (1.0 + st["drift_pct"]), 2)

class AutoTradingBot:
    def __init__(self, state_filename="trading_bot_state.json", name="LiveBot", is_after_hours_forced=False):
        self.state_filename = state_filename
        self.name = name
        self.is_after_hours_forced = is_after_hours_forced

        if self.is_after_hours_forced:
            default_alloc = 200000.0  # ₹2 Lakhs per trade for ₹1 Crore Desk
            from app.models.trading import AfterHoursPosition, AfterHoursTradeHistory, AfterHoursAccount
            from app.services.paper_trader import AfterHoursPaperTradingEngine
            self.pos_model = AfterHoursPosition
            self.trade_model = AfterHoursTradeHistory
            self.account_model = AfterHoursAccount
            self.engine = AfterHoursPaperTradingEngine
        else:
            default_alloc = 5000.0   # ₹5,000 per trade for ₹50,000 Desk
            from app.models.trading import Position, TradeHistory, Account
            from app.services.paper_trader import PaperTradingEngine
            self.pos_model = Position
            self.trade_model = TradeHistory
            self.account_model = Account
            self.engine = PaperTradingEngine

        persisted = _load_persistent_bot_state(self.state_filename)
        self.is_running = persisted.get("is_running", False)
        self.scan_mode = persisted.get("scan_mode", "ALL_NIFTY_200")
        self.single_symbol = persisted.get("single_symbol", "RELIANCE.NS")
        self.strategy = persisted.get("strategy", "123_REVERSAL")
        self.allocation_per_trade = float(persisted.get("allocation_per_trade", default_alloc))
        self.leverage = float(persisted.get("leverage", 5.0))  # 5x NSE Intraday MIS Leverage
        self.max_positions = int(persisted.get("max_positions", 0))
        self.slippage_pct = float(persisted.get("slippage_pct", 0.0))
        try:
            from app.services.paper_trader import AfterHoursPaperTradingEngine
            AfterHoursPaperTradingEngine.slippage_pct = self.slippage_pct
        except Exception:
            pass
        self.interval_seconds = 60  # 1 minute (60 seconds)
        self.fast_period = 9
        self.slow_period = 21
        self.rsi_period = 14
        self.rsi_oversold = 35
        self.rsi_overbought = 65
        self.stop_loss_pct = float(persisted.get("stop_loss_pct", 1.0))
        self.take_profit_pct = float(persisted.get("take_profit_pct", 1.0))
        
        # Option A: Macro Trend & Volume Confirmation
        self.use_ema200_filter = persisted.get("use_ema200_filter", True)
        self.use_volume_filter = persisted.get("use_volume_filter", False)
        
        # Option B: Trailing Stop-Loss & Dynamic ATR Exits
        self.use_trailing_stop = persisted.get("use_trailing_stop", True)
        self.trailing_trigger_pct = 1.0
        self.use_atr_exits = persisted.get("use_atr_exits", False)
        self.atr_multiplier_sl = 1.5
        self.atr_multiplier_tp = 3.0
        
        # Session Management & Trade Report State (Preserved across stops & reboots)
        self.session_count = persisted.get("session_count", 1)
        self.session_id = persisted.get("session_id") or f"sess_{int(time.time())}"
        self.session_start_time = persisted.get("session_start_time")
        self.session_stop_time = persisted.get("session_stop_time")
        self.session_status = persisted.get("session_status", "STOPPED")
        self.session_trades = persisted.get("session_trades", [])
        self.session_metrics = persisted.get("session_metrics", {
            "total_trades": len(self.session_trades),
            "total_buys": sum(1 for t in self.session_trades if t.get("side") == "BUY"),
            "total_sells": sum(1 for t in self.session_trades if t.get("side") == "SELL"),
            "net_realized_pnl": round(sum(float(t.get("realized_pnl") or 0.0) for t in self.session_trades), 2),
            "total_charges": round(sum(float(t.get("charges") or 0.0) for t in self.session_trades), 2),
            "win_rate": 0.0,
            "wins": sum(1 for t in self.session_trades if float(t.get("realized_pnl") or 0.0) > 0),
            "losses": sum(1 for t in self.session_trades if float(t.get("realized_pnl") or 0.0) < 0)
        })
        self.past_sessions = persisted.get("past_sessions", [])

        self.logs = []
        self.last_run_time = None
        self.last_scan_summary = {
            "total_scanned": 0,
            "buys_found": 0,
            "sells_found": 0,
            "trades_executed": len(self.session_trades),
            "scanner_results": []
        }
        self.thread = None
        self.lock = threading.Lock()

    def persist_state(self):
        try:
            data = {
                "is_running": self.is_running,
                "scan_mode": self.scan_mode,
                "single_symbol": self.single_symbol,
                "strategy": self.strategy,
                "allocation_per_trade": self.allocation_per_trade,
                "leverage": self.leverage,
                "max_positions": self.max_positions,
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "use_ema200_filter": self.use_ema200_filter,
                "use_volume_filter": self.use_volume_filter,
                "use_trailing_stop": self.use_trailing_stop,
                "use_atr_exits": self.use_atr_exits,
                "session_id": self.session_id,
                "session_count": self.session_count,
                "session_start_time": self.session_start_time,
                "session_stop_time": self.session_stop_time,
                "session_status": self.session_status,
                "session_trades": self.session_trades[:200],
                "session_metrics": self.session_metrics,
                "past_sessions": self.past_sessions[:5]
            }
            _save_persistent_bot_state(data, self.state_filename)
        except Exception:
            pass

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

    def record_session_trade(self, trade_data: dict):
        """Records an executed trade into the current bot session report and updates metrics"""
        with self.lock:
            if not trade_data.get("timestamp"):
                trade_data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if not trade_data.get("id"):
                trade_data["id"] = f"tr_{int(time.time()*1000)}_{len(self.session_trades)+1}"

            self.session_trades.insert(0, trade_data)

            sm = self.session_metrics
            sm["total_trades"] = len(self.session_trades)
            side = trade_data.get("side")
            if side == "BUY":
                sm["total_buys"] = sm.get("total_buys", 0) + 1
            elif side == "SHORT":
                sm["total_shorts"] = sm.get("total_shorts", 0) + 1
            elif side in ("SELL", "COVER"):
                sm["total_sells"] = sm.get("total_sells", 0) + 1
                pnl = float(trade_data.get("realized_pnl") or 0.0)
                sm["net_realized_pnl"] = round(sm.get("net_realized_pnl", 0.0) + pnl, 2)
                if pnl > 0:
                    sm["wins"] = sm.get("wins", 0) + 1
                elif pnl < 0:
                    sm["losses"] = sm.get("losses", 0) + 1
                
                total_closed = sm.get("wins", 0) + sm.get("losses", 0)
                if total_closed > 0:
                    sm["win_rate"] = round((sm.get("wins", 0) / total_closed) * 100, 1)

            sm["total_charges"] = round(sm.get("total_charges", 0.0) + float(trade_data.get("charges") or 0.0), 2)
            self.last_scan_summary["trades_executed"] = len(self.session_trades)
            self.persist_state()

    def get_intraday_traded_symbols(self, client_traded_symbols: list = None) -> set:
        """Returns set of symbols that have already had an entry trade today during the current IST session"""
        # In NIGHTHAWK (24/7 Sandbox Lab), stocks can be traded multiple times once closed
        if self.name == "NIGHTHAWK":
            return set()

        from datetime import datetime, timezone, timedelta
        
        traded = set()
        # 0. Client-provided traded symbols (persisted in browser across Vercel serverless cold starts)
        if client_traded_symbols and isinstance(client_traded_symbols, list):
            for s in client_traded_symbols:
                if s and isinstance(s, str):
                    traded.add(s.strip().upper())

        # 1. Any currently held open position
        try:
            open_positions = self.pos_model.query.filter(self.pos_model.quantity > 0).all()
            for p in open_positions:
                traded.add(p.symbol)
        except Exception:
            pass

        # 2. Database TradeHistory for today's IST trading session
        try:
            now_utc = datetime.now(timezone.utc)
            now_ist = now_utc + timedelta(hours=5, minutes=30)
            start_of_ist_day_utc = datetime(now_ist.year, now_ist.month, now_ist.day, 0, 0, 0) - timedelta(hours=5, minutes=30)
            
            today_trades = self.trade_model.query.filter(
                self.trade_model.timestamp >= start_of_ist_day_utc,
                self.trade_model.side.in_(["BUY", "SHORT"])
            ).all()
            for t in today_trades:
                traded.add(t.symbol)
        except Exception:
            pass
            
        # 3. Session trades from current bot state
        try:
            for t in (self.session_trades or []):
                if t.get("side") in ["BUY", "SHORT"]:
                    sym = t.get("symbol")
                    if sym:
                        traded.add(sym)
        except Exception:
            pass
            
        return traded

    def get_status(self) -> dict:
        persisted = _load_persistent_bot_state(self.state_filename)
        is_active = self.is_running or persisted.get("is_running", False)
        after_hours = True if self.is_after_hours_forced else not is_nse_market_open()

        # Sync session state from persisted if memory was restarted
        if not self.session_trades and persisted.get("session_trades"):
            self.session_trades = persisted.get("session_trades", [])
            self.session_metrics = persisted.get("session_metrics", self.session_metrics)
            self.session_id = persisted.get("session_id", self.session_id)
            self.session_count = persisted.get("session_count", self.session_count)
            self.session_status = persisted.get("session_status", self.session_status)
            self.session_start_time = persisted.get("session_start_time", self.session_start_time)
            self.session_stop_time = persisted.get("session_stop_time", self.session_stop_time)

        market_open = is_nse_market_open()
        can_run = market_open if self.name == "ALPHA" else True
        time_restriction_msg = ""
        if self.name == "ALPHA" and not market_open:
            time_restriction_msg = "ALPHA runs exclusively during live NSE market hours (09:15 AM – 03:30 PM IST). Market is currently closed. Switch to NIGHTHAWK for after-hours trading."

        return {
            "name": self.name,
            "is_running": is_active,
            "can_run": can_run,
            "is_market_open": market_open,
            "time_restriction_msg": time_restriction_msg,
            "is_after_hours": after_hours,
            "is_after_hours_bot": self.is_after_hours_forced,
            "market_status": "AFTER_HOURS_SIMULATION" if after_hours else "LIVE_NSE_MARKET",
            "scan_mode": self.scan_mode,
            "single_symbol": self.single_symbol,
            "strategy": self.strategy,
            "allocation_per_trade": self.allocation_per_trade,
            "leverage": self.leverage,
            "buying_power_per_trade": self.allocation_per_trade * self.leverage,
            "max_positions": self.max_positions,
            "is_unlimited_positions": (self.max_positions <= 0),
            "slippage_pct": getattr(self, 'slippage_pct', 0.0),
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
            "session": {
                "session_id": self.session_id,
                "session_count": self.session_count,
                "status": "RUNNING" if is_active else self.session_status,
                "start_time": self.session_start_time,
                "stop_time": self.session_stop_time,
                "metrics": self.session_metrics,
                "trades": self.session_trades[:100]
            },
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
        if "leverage" in config:
            self.leverage = float(config["leverage"])
        if "max_positions" in config:
            self.max_positions = int(config["max_positions"])
        if "slippage_pct" in config:
            self.slippage_pct = float(config["slippage_pct"])
            try:
                from app.services.paper_trader import AfterHoursPaperTradingEngine
                AfterHoursPaperTradingEngine.slippage_pct = self.slippage_pct
            except Exception:
                pass
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

    def start_new_session(self, config: dict = None):
        """Initializes a fresh new trading session report when the bot is started/restarted"""
        if config:
            self.update_config(config)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Archive prior session if it contained trades
        if self.session_trades:
            self.past_sessions.insert(0, {
                "session_id": self.session_id,
                "session_count": self.session_count,
                "start_time": self.session_start_time,
                "stop_time": self.session_stop_time or now_str,
                "trades": list(self.session_trades),
                "metrics": dict(self.session_metrics)
            })
            if len(self.past_sessions) > 5:
                self.past_sessions.pop()
            self.session_count = (self.session_count or 1) + 1

        self.session_id = f"sess_{int(time.time())}"
        self.session_start_time = now_str
        self.session_stop_time = None
        self.session_status = "RUNNING"
        self.session_trades = []
        self.session_metrics = {
            "total_trades": 0,
            "total_buys": 0,
            "total_sells": 0,
            "net_realized_pnl": 0.0,
            "total_charges": 0.0,
            "win_rate": 0.0,
            "wins": 0,
            "losses": 0
        }
        self.is_running = True
        self.persist_state()

    def start(self, config: dict = None):
        if config:
            self.update_config(config)

        # Enforce Operating Hours:
        # ALPHA runs ONLY during live market hours (09:15 AM – 03:30 PM IST Mon-Fri)
        # NIGHTHAWK is the 24/7 After-Hours Strategy Lab & Simulator (runs anytime)
        market_open = is_nse_market_open()

        if self.name == "ALPHA" and not market_open:
            msg = "ALPHA is restricted to LIVE NSE Market hours (09:15 AM – 03:30 PM IST Mon-Fri). Market is currently closed. Please use NIGHTHAWK for after-hours trading!"
            self.add_log(f"🛑 [ALPHA BLOCKED] {msg}", "WARNING")
            return {
                "success": False,
                "error": msg,
                "is_market_closed": True,
                "allowed_engine": "NIGHTHAWK",
                "switch_url": "/bot-after-hours",
                "status": self.get_status()
            }

        # Start a brand new session report on restart/start
        self.start_new_session()

        after_hours = not is_nse_market_open()
        mode_label = "ALL NIFTY 200 STOCKS" if self.scan_mode == "ALL_NIFTY_200" else ("ALL NIFTY 50 STOCKS" if self.scan_mode == "ALL_NIFTY_50" else self.single_symbol)
        ema_tag = "200 EMA Filter [ON]" if self.use_ema200_filter else "200 EMA Filter [OFF]"
        trail_tag = "Trailing Stop [ON]" if self.use_trailing_stop else "Trailing Stop [OFF]"
        session_tag = "🌙 AFTER-MARKET HOURS (ACTIVE 24/7)" if after_hours else "☀️ LIVE NSE MARKET SESSION"
        self.add_log(f"Bot {self.name} ACTIVATED (Session #{self.session_count}) in {session_tag} on {mode_label} | {self.strategy} | {ema_tag} | {trail_tag} | Margin: ₹{self.allocation_per_trade:,.0f} [5X LEVERAGE: ₹{self.allocation_per_trade * self.leverage:,.0f} Exposure]", "SUCCESS")

        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

        return {"success": True, "message": f"Bot activated on {mode_label} (Session #{self.session_count})", "status": self.get_status()}

    def stop(self):
        self.is_running = False
        self.session_status = "STOPPED"
        self.session_stop_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.persist_state()
        trade_count = len(self.session_trades)
        self.add_log(f"🛑 Auto-Bot PAUSED and STOPPED by operator. Session #{self.session_count} report finalized ({trade_count} trades preserved).", "WARNING")
        return {"success": True, "message": f"Bot stopped. Session #{self.session_count} report preserved ({trade_count} trades).", "status": self.get_status()}

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
            pos_side = getattr(pos, 'side', 'LONG') or 'LONG'
            is_short = (pos_side == 'SHORT')

            # Calculate PnL based on position direction:
            if is_short:
                pnl_pct = ((avg_price - current_price) / avg_price) * 100
                exit_order_side = "COVER"
                exit_action_label = "COVER"
            else:
                pnl_pct = ((current_price - avg_price) / avg_price) * 100
                exit_order_side = "SELL"
                exit_action_label = "SELL"

            # Track extreme price attained (peak for LONG, trough for SHORT)
            if is_short:
                extreme = min(getattr(pos, 'peak_price', 999999.0) or avg_price, current_price)
            else:
                extreme = max(getattr(pos, 'peak_price', 0.0) or avg_price, current_price)

            if hasattr(pos, 'peak_price'):
                pos.peak_price = extreme

            # OPTION B: Trailing Stop-Loss & Breakeven Shield (Protects all strategies including 1-2-3 Reversal)
            if self.use_trailing_stop:
                # Stage 1: Breakeven Shield at +2.0%
                if pnl_pct >= self.trailing_trigger_pct and not getattr(pos, 'trailing_active', False):
                    pos.trailing_active = True
                    cur_sl = getattr(pos, 'stop_loss', 0.0) or 0.0
                    should_raise_sl = (cur_sl < avg_price) if not is_short else (cur_sl > avg_price or cur_sl == 0.0)
                    if should_raise_sl:
                        pos.stop_loss = avg_price
                        try:
                            db.session.commit()
                        except Exception:
                            pass
                        self.add_log(f"🛡️ [BREAKEVEN SHIELD] {display_name} ({pos_side}): Gain reached +{pnl_pct:.2f}%. Stop-loss set to Breakeven (₹{avg_price:.2f}). Trade is 100% Risk-Free!", "SUCCESS")

                # Stage 2: Trail behind extreme prices
                if getattr(pos, 'trailing_active', False):
                    trail_distance = (self.atr_multiplier_sl * atr_14) if self.use_atr_exits else (extreme * 0.02)
                    current_sl = getattr(pos, 'stop_loss', 0.0) or 0.0
                    if not is_short:
                        dynamic_stop = round(extreme - trail_distance, 2)
                        if dynamic_stop > current_sl and dynamic_stop > avg_price:
                            pos.stop_loss = dynamic_stop
                            try:
                                db.session.commit()
                            except Exception:
                                pass
                            self.add_log(f"📈 [TRAILING STOP TRAIL] {display_name} (LONG): High ₹{extreme:.2f}! Stop raised to ₹{dynamic_stop:.2f} (Locking +{((dynamic_stop-avg_price)/avg_price*100):.1f}%)", "SUCCESS")
                    else:
                        dynamic_stop = round(extreme + trail_distance, 2)
                        if (current_sl == 0.0 or dynamic_stop < current_sl) and dynamic_stop < avg_price:
                            pos.stop_loss = dynamic_stop
                            try:
                                db.session.commit()
                            except Exception:
                                pass
                            self.add_log(f"📉 [TRAILING STOP TRAIL] {display_name} (SHORT): Low ₹{extreme:.2f}! Stop lowered to ₹{dynamic_stop:.2f} (Locking +{((avg_price-dynamic_stop)/avg_price*100):.1f}%)", "SUCCESS")

            # Check if Trailing / Dynamic Stop-Loss triggered
            sl_level = getattr(pos, 'stop_loss', 0.0) or 0.0
            is_sl_hit = False
            if sl_level > 0:
                is_sl_hit = (current_price <= sl_level) if not is_short else (current_price >= sl_level)

            if is_sl_hit:
                lock_pnl = pnl_pct
                closed_trade_dict = None
                if allow_order_execution:
                    self.add_log(f"🛑 [STOP HIT] {display_name} ({pos_side}): Liquidated @ ₹{current_price:.2f} (Stop was ₹{sl_level:.2f}, Result: {lock_pnl:+.2f}%)", "ERROR" if lock_pnl < 0 else "SUCCESS")
                    exec_res = self.engine.execute_order(symbol, exit_order_side, pos.quantity, execution_price=current_price)
                    if exec_res.get("success"):
                        held_positions_map.pop(symbol, None)
                        pnl = float(exec_res.get("realized_pnl", round(((current_price - avg_price) if not is_short else (avg_price - current_price)) * pos.quantity, 2)))
                        chg_dict = exec_res.get("charges") if isinstance(exec_res.get("charges"), dict) else {}
                        chg = float(chg_dict.get("total_charges", 0.0) if isinstance(chg_dict, dict) else 0.0)
                        cost_basis = round(pos.quantity * (avg_price if avg_price > 0 else 1.0), 2)
                        closed_trade_dict = {
                            "id": exec_res.get("trade_id") or int(datetime.now().timestamp() * 1000),
                            "symbol": symbol,
                            "displayName": display_name,
                            "side": exit_order_side,
                            "quantity": pos.quantity,
                            "entry_price": avg_price,
                            "exit_price": current_price,
                            "price": current_price,
                            "total_value": round(pos.quantity * current_price, 2),
                            "charges": chg,
                            "brokerage": round(float(chg_dict.get("brokerage", 0.0)), 2),
                            "stt": round(float(chg_dict.get("stt", 0.0)), 2),
                            "exchange_charges": round(float(chg_dict.get("exchange_charges", 0.0)), 2),
                            "sebi_charges": round(float(chg_dict.get("sebi_charges", 0.0)), 2),
                            "gst": round(float(chg_dict.get("gst", 0.0)), 2),
                            "stamp_duty": round(float(chg_dict.get("stamp_duty", 0.0)), 2),
                            "net_proceeds": round((pos.quantity * current_price) - chg, 2),
                            "realized_pnl": pnl,
                            "roi_pct": round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0,
                            "is_win": pnl >= 0,
                            "action": exit_action_label,
                            "signal": "STOP_LOSS",
                            "reason": f"Stop-Loss hit @ ₹{current_price:.2f} (SL: ₹{sl_level:.2f})",
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        self.record_session_trade(closed_trade_dict)
                return {
                    "symbol": symbol, "displayName": display_name, "price": current_price,
                    "change_pct": change_pct, "signal": "STOP_LOSS", "reason": f"Indicator Stop-Loss hit @ ₹{current_price:.2f} (SL: ₹{sl_level:.2f}, {lock_pnl:+.2f}%)",
                    "action_executed": "STOP_EXIT" if allow_order_execution else "OFFLINE (No orders placed)", "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14,
                    "closed_trade": closed_trade_dict
                }

            # Check if Fixed % Stop-Loss Ceiling triggered (Protects ALL strategies including 1-2-3 Reversal)
            if pnl_pct <= -abs(self.stop_loss_pct):
                closed_trade_dict = None
                if allow_order_execution:
                    self.add_log(f"🛑 [SL CEILING HIT] {display_name} ({pos_side}): Max risk limit reached at -{abs(pnl_pct):.2f}% (Ceiling: -{abs(self.stop_loss_pct):.1f}%). Liquidating {pos.quantity} shares @ ₹{current_price:.2f} to protect capital!", "ERROR")
                    exec_res = self.engine.execute_order(symbol, exit_order_side, pos.quantity, execution_price=current_price)
                    if exec_res.get("success"):
                        held_positions_map.pop(symbol, None)
                        pnl = float(exec_res.get("realized_pnl", round(((current_price - avg_price) if not is_short else (avg_price - current_price)) * pos.quantity, 2)))
                        chg_dict = exec_res.get("charges") if isinstance(exec_res.get("charges"), dict) else {}
                        chg = float(chg_dict.get("total_charges", 0.0) if isinstance(chg_dict, dict) else 0.0)
                        cost_basis = round(pos.quantity * (avg_price if avg_price > 0 else 1.0), 2)
                        closed_trade_dict = {
                            "id": exec_res.get("trade_id") or int(datetime.now().timestamp() * 1000),
                            "symbol": symbol,
                            "displayName": display_name,
                            "side": exit_order_side,
                            "quantity": pos.quantity,
                            "entry_price": avg_price,
                            "exit_price": current_price,
                            "price": current_price,
                            "total_value": round(pos.quantity * current_price, 2),
                            "charges": chg,
                            "brokerage": round(float(chg_dict.get("brokerage", 0.0)), 2),
                            "stt": round(float(chg_dict.get("stt", 0.0)), 2),
                            "exchange_charges": round(float(chg_dict.get("exchange_charges", 0.0)), 2),
                            "sebi_charges": round(float(chg_dict.get("sebi_charges", 0.0)), 2),
                            "gst": round(float(chg_dict.get("gst", 0.0)), 2),
                            "stamp_duty": round(float(chg_dict.get("stamp_duty", 0.0)), 2),
                            "net_proceeds": round((pos.quantity * current_price) - chg, 2),
                            "realized_pnl": pnl,
                            "roi_pct": round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0,
                            "is_win": pnl >= 0,
                            "action": exit_action_label,
                            "signal": "STOP_LOSS",
                            "reason": f"Fixed Stop-Loss hit at {pnl_pct:.2f}%",
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        self.record_session_trade(closed_trade_dict)
                return {
                    "symbol": symbol, "displayName": display_name, "price": current_price,
                    "change_pct": change_pct, "signal": "STOP_LOSS", "reason": f"Stop-Loss triggered at {pnl_pct:.2f}%",
                    "action_executed": "LIQUIDATED" if allow_order_execution else "OFFLINE (No orders placed)", "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14,
                    "closed_trade": closed_trade_dict
                }

            # Check Take-Profit (Dual Mode: Indicator Measured Move Target OR Configured % Target):
            tp_level = getattr(pos, 'take_profit', 0.0) or 0.0
            is_tp_hit = False
            tp_reason = ""
            if tp_level > 0 and ((current_price >= tp_level) if not is_short else (current_price <= tp_level)):
                is_tp_hit = True
                tp_reason = f"Indicator Target achieved @ ₹{current_price:.2f} (Target: ₹{tp_level:.2f}, +{pnl_pct:.2f}%)"
            elif pnl_pct >= abs(self.take_profit_pct):
                is_tp_hit = True
                tp_reason = f"Take-Profit target achieved at +{pnl_pct:.2f}% (Target: +{self.take_profit_pct:.1f}%)"

            if is_tp_hit:
                closed_trade_dict = None
                if allow_order_execution:
                    self.add_log(f"🎯 [TAKE-PROFIT] {display_name} ({pos_side}): Hit +{pnl_pct:.2f}%. Closing {pos.quantity} shares @ ₹{current_price:.2f} to lock gains! ({tp_reason})", "SUCCESS")
                    exec_res = self.engine.execute_order(symbol, exit_order_side, pos.quantity, execution_price=current_price)
                    if exec_res.get("success"):
                        held_positions_map.pop(symbol, None)
                        pnl = float(exec_res.get("realized_pnl", round(((current_price - avg_price) if not is_short else (avg_price - current_price)) * pos.quantity, 2)))
                        chg_dict = exec_res.get("charges") if isinstance(exec_res.get("charges"), dict) else {}
                        chg = float(chg_dict.get("total_charges", 0.0) if isinstance(chg_dict, dict) else 0.0)
                        cost_basis = round(pos.quantity * (avg_price if avg_price > 0 else 1.0), 2)
                        closed_trade_dict = {
                            "id": exec_res.get("trade_id") or int(datetime.now().timestamp() * 1000),
                            "symbol": symbol,
                            "displayName": display_name,
                            "side": exit_order_side,
                            "quantity": pos.quantity,
                            "entry_price": avg_price,
                            "exit_price": current_price,
                            "price": current_price,
                            "total_value": round(pos.quantity * current_price, 2),
                            "charges": chg,
                            "brokerage": round(float(chg_dict.get("brokerage", 0.0)), 2),
                            "stt": round(float(chg_dict.get("stt", 0.0)), 2),
                            "exchange_charges": round(float(chg_dict.get("exchange_charges", 0.0)), 2),
                            "sebi_charges": round(float(chg_dict.get("sebi_charges", 0.0)), 2),
                            "gst": round(float(chg_dict.get("gst", 0.0)), 2),
                            "stamp_duty": round(float(chg_dict.get("stamp_duty", 0.0)), 2),
                            "net_proceeds": round((pos.quantity * current_price) - chg, 2),
                            "realized_pnl": pnl,
                            "roi_pct": round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0,
                            "is_win": pnl >= 0,
                            "action": exit_action_label,
                            "signal": "TAKE_PROFIT",
                            "reason": tp_reason,
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        self.record_session_trade(closed_trade_dict)
                return {
                    "symbol": symbol, "displayName": display_name, "price": current_price,
                    "change_pct": change_pct, "signal": "TAKE_PROFIT", "reason": tp_reason,
                    "action_executed": "PROFIT_LOCKED" if allow_order_execution else "OFFLINE (No orders placed)", "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14,
                    "closed_trade": closed_trade_dict
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

            # Hard SL Ceiling: clamp suggested_sl so downside risk never exceeds self.stop_loss_pct (e.g. 1.0% - 1.2%)
            if suggested_sl and self.stop_loss_pct and self.stop_loss_pct > 0:
                if signal == "BUY":
                    max_allowed_sl = round(current_price * (1.0 - abs(self.stop_loss_pct) / 100.0), 2)
                    if suggested_sl < max_allowed_sl:
                        suggested_sl = max_allowed_sl
                        reason += f" | SL Clamped at Max -{abs(self.stop_loss_pct):.1f}%"
                elif signal in ["SELL", "SHORT"]:
                    min_allowed_sl = round(current_price * (1.0 + abs(self.stop_loss_pct) / 100.0), 2)
                    if suggested_sl > min_allowed_sl:
                        suggested_sl = min_allowed_sl
                        reason += f" | SL Clamped at Max -{abs(self.stop_loss_pct):.1f}%"

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

        # OPTION A: Filter BUY/SELL signals with 200 EMA Macro Trend & Volume Surge
        filter_status = None
        if signal == "BUY" and (not pos or getattr(pos, 'side', 'LONG') == 'SHORT'):
            if self.use_ema200_filter and trend_200 == "BEARISH":
                signal = "HOLD"
                reason = f"BUY Filtered: Bearish Trend (Price ₹{current_price:.1f} < 200 EMA ₹{ema_200})"
                filter_status = "FILTERED_200EMA"
            elif self.use_volume_filter and vol_ratio < 1.0:
                signal = "HOLD"
                reason = f"BUY Filtered: Low Volume Surge ({vol_ratio:.2f}x < 1.0x avg)"
                filter_status = "FILTERED_VOLUME"
        elif signal == "SELL" and not pos:
            # For new SHORT entries:
            if self.use_ema200_filter and trend_200 == "BULLISH":
                signal = "HOLD"
                reason = f"SHORT Filtered: Bullish Trend (Price ₹{current_price:.1f} > 200 EMA ₹{ema_200})"
                filter_status = "FILTERED_200EMA"
            elif self.use_volume_filter and vol_ratio < 1.0:
                signal = "HOLD"
                reason = f"SHORT Filtered: Low Volume Surge ({vol_ratio:.2f}x < 1.0x avg)"
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

    def evaluate_signals(self, simulation_event: str = None, target_symbol: str = None, client_traded_symbols: list = None) -> dict:
        """Runs the scanner across all Nifty 50 constituents (or single stock) and auto-trades"""
        import random
        self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 0. Enforce Operating Hours: ALPHA strictly during Live Market, NIGHTHAWK strictly After Hours
        market_open = is_nse_market_open()
        is_explicit_test = simulation_event in ["FORCE_BUY", "STOP_SHOCK", "FLASH_CRASH", "BULL_SURGE", "BULL_RUNNER", "TRAIL_PUSH", "WHIPSAW_CHOP"]

        if self.name == "ALPHA" and not market_open and not is_explicit_test:
            if self.is_running:
                self.is_running = False
                self.session_status = "MARKET_CLOSED_PAUSED"
                self.persist_state()
                self.add_log("⏸️ [ALPHA SLEEP] NSE Market is closed. ALPHA paused until 09:15 AM tomorrow. Use NIGHTHAWK for after-market trading.", "WARNING")
            return {
                "total_scanned": 0, "buys_found": 0, "sells_found": 0, "trades_executed": 0,
                "message": "Market Closed: ALPHA runs exclusively during live NSE market hours (09:15 AM – 03:30 PM IST Mon-Fri).",
                "is_market_closed": True,
                "allowed_engine": "NIGHTHAWK",
                "switch_url": "/bot-after-hours"
            }



        # 1. Handle After-Hours Instant Test Triggers
        if simulation_event == "FORCE_BUY":
            force_sym = (target_symbol or "RELIANCE.NS").strip().upper()
            quote = MarketDataService.get_quote(force_sym)
            p = quote.get("price", 1500.0) if "error" not in quote else 1500.0
            trade_qty = max(1, int((self.allocation_per_trade * self.leverage) / p))
            exec_res = self.engine.execute_order(force_sym, "BUY", trade_qty, execution_price=p)
            pos_rec = self.pos_model.query.filter_by(symbol=force_sym).first()
            if pos_rec:
                pos_rec.stop_loss = round(p * (1 - self.stop_loss_pct / 100), 2)
                pos_rec.take_profit = round(p * (1 + self.take_profit_pct / 100), 2)
                pos_rec.peak_price = p
                pos_rec.trailing_active = False
                try:
                    db.session.commit()
                except Exception:
                    pass
            chg = exec_res.get("charges", {}).get("total_charges", 0.0) if isinstance(exec_res, dict) and isinstance(exec_res.get("charges"), dict) else 0.0
            self.record_session_trade({
                "symbol": force_sym,
                "displayName": force_sym.replace('.NS', ''),
                "side": "BUY",
                "quantity": trade_qty,
                "entry_price": p,
                "price": p,
                "total_value": round(trade_qty * p, 2),
                "charges": chg,
                "realized_pnl": 0.0,
                "roi_pct": 0.0,
                "action": "BUY",
                "signal": "FORCE_BUY",
                "suggested_sl": pos_rec.stop_loss if pos_rec else None,
                "suggested_tp": pos_rec.take_profit if pos_rec else None,
                "reason": f"Manual/Test Instant BUY Order | Stop: ₹{pos_rec.stop_loss if pos_rec else 0:.2f} | Target: ₹{pos_rec.take_profit if pos_rec else 0:.2f}",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            self.add_log(f"⚡ [TEST SIGNAL] Instant BUY executed: {trade_qty} shares of {force_sym.replace('.NS', '')} @ ₹{p:.2f} (Total: ₹{trade_qty*p:,.2f})", "SUCCESS")

        elif simulation_event in ["BULL_SURGE", "BULL_RUNNER", "TRAIL_PUSH", "STOP_SHOCK", "FLASH_CRASH", "WHIPSAW_CHOP"]:
            # If no open positions exist, automatically seed one so the user can immediately observe the risk exit!
            held_positions = self.pos_model.query.filter(self.pos_model.quantity > 0).all()
            if not held_positions:
                seed_sym = "RELIANCE.NS"
                quote = MarketDataService.get_quote(seed_sym)
                p = quote.get("price", 1500.0) if "error" not in quote else 1500.0
                trade_qty = max(1, int((self.allocation_per_trade * self.leverage) / p))
                exec_res = self.engine.execute_order(seed_sym, "BUY", trade_qty, execution_price=p)
                pos_rec = self.pos_model.query.filter_by(symbol=seed_sym).first()
                if pos_rec:
                    pos_rec.stop_loss = round(p * (1 - self.stop_loss_pct / 100), 2)
                    pos_rec.take_profit = round(p * (1 + self.take_profit_pct / 100), 2)
                    pos_rec.peak_price = p
                    pos_rec.trailing_active = False
                    try:
                        db.session.commit()
                    except Exception:
                        pass
                chg = exec_res.get("charges", {}).get("total_charges", 0.0) if isinstance(exec_res, dict) and isinstance(exec_res.get("charges"), dict) else 0.0
                self.record_session_trade({
                    "symbol": seed_sym,
                    "displayName": seed_sym.replace('.NS', ''),
                    "side": "BUY",
                    "quantity": trade_qty,
                    "entry_price": p,
                    "price": p,
                    "total_value": round(trade_qty * p, 2),
                    "charges": chg,
                    "realized_pnl": 0.0,
                    "roi_pct": 0.0,
                    "action": "BUY",
                    "signal": "TEST_SEED",
                    "suggested_sl": pos_rec.stop_loss if pos_rec else None,
                    "suggested_tp": pos_rec.take_profit if pos_rec else None,
                    "reason": f"Simulator Auto-Seeded Test Position | Target: ₹{pos_rec.take_profit if pos_rec else 0:.2f} | Stop: ₹{pos_rec.stop_loss if pos_rec else 0:.2f}",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                self.add_log(f"💡 [SIMULATOR] Auto-seeded 1 test position ({seed_sym.replace('.NS','')}) for instant guard verification.", "INFO")

        # Get existing positions & cash balance
        held_positions = self.pos_model.query.filter(self.pos_model.quantity > 0).all()
        held_map = {p.symbol: p for p in held_positions}
        account = self.engine.reconcile_account_balance()
        available_cash = account.cash_balance if account else 0.0
        already_traded_today = self.get_intraday_traded_symbols(client_traded_symbols=client_traded_symbols)

        if self.scan_mode == "ALL_NIFTY_200":
            target_stocks = NIFTY_200_DEFAULT
        elif self.scan_mode == "ALL_NIFTY_50":
            target_stocks = NIFTY_50_DEFAULT
        else:
            target_stocks = [self.single_symbol]
        
        # Parallel high-speed data fetch for stock universe
        stock_data_map = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=35) as executor:
            future_to_stock = {
                executor.submit(MarketDataService.get_stock_analysis_data, sym): sym 
                for sym in target_stocks
            }
            try:
                for future in concurrent.futures.as_completed(future_to_stock, timeout=8.0):
                    sym = future_to_stock[future]
                    try:
                        data = future.result()
                        if "error" not in data:
                            stock_data_map[sym] = data
                    except Exception:
                        pass
            except concurrent.futures.TimeoutError:
                pass

        # Apply Simulated Price Ticks if Simulation Mode / Event is active or in after-market hours
        after_hours = True if self.is_after_hours_forced else not is_nse_market_open()
        for symbol, stock_data in stock_data_map.items():
            pos = held_map.get(symbol)
            orig_p = stock_data["price"]
            
            if simulation_event in ["BULL_SURGE", "BULL_RUNNER"] and pos:
                # Best Scenario 1: Momentum Bull Surge (+3.5%) to trigger Breakeven Shield & Take-Profit
                surged_price = round(pos.average_price * 1.035, 2)
                stock_data["price"] = surged_price
                stock_data["percent_change"] = round(((surged_price - pos.average_price) / pos.average_price) * 100, 2)
            elif simulation_event == "TRAIL_PUSH" and pos:
                # Best Scenario 2: Strong trend push (+4.5%) to trail stop upward
                trailed_price = round(pos.average_price * 1.045, 2)
                stock_data["price"] = trailed_price
                stock_data["percent_change"] = round(((trailed_price - pos.average_price) / pos.average_price) * 100, 2)
            elif simulation_event in ["STOP_SHOCK", "FLASH_CRASH"] and pos:
                # Worst Scenario 1: Macro Flash Crash (-2.5%) across active holdings to test simultaneous SL liquidation
                shocked_price = round(pos.average_price * 0.975, 2)
                stock_data["price"] = shocked_price
                stock_data["percent_change"] = -2.5
            elif simulation_event == "WHIPSAW_CHOP":
                # Worst Scenario 2: Whipsaw False Breakout - drops -1.2% to test false breakout trap
                if pos:
                    whip_price = round(pos.average_price * 0.988, 2)
                    stock_data["price"] = whip_price
                    stock_data["percent_change"] = -1.2
                else:
                    stock_data["price"] = round(orig_p * 1.002, 2)
            elif self.is_after_hours_forced or (after_hours and (simulation_event in ["RANDOM_WALK", "AFTER_HOURS_WALK"] or not simulation_event)):
                # Continuous dynamic price drift in NIGHTHAWK 24/7 sandbox and after-market hours
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

        # Autonomous trade order execution strictly requires bot to be running (self.is_running is True or persisted)
        # Or explicit manual simulation triggers (FORCE_BUY, STOP_SHOCK)
        is_explicit_test = simulation_event in ["FORCE_BUY", "STOP_SHOCK"]
        persisted_running = _load_persistent_bot_state(self.state_filename).get("is_running", False)
        can_execute_trades = bool(self.is_running or persisted_running or is_explicit_test)

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

            # AUTO-TRADE EXECUTION LOGIC (TWO-WAY: LONG & SHORT):
            max_limit = getattr(self, 'max_positions', 0)
            # If max_positions is 0 or negative, allow unlimited. Otherwise strictly enforce the cap!
            has_position_slot = (max_limit <= 0) or (len(held_map) < max_limit)

            if sig == "BUY":
                buys_found += 1
                if can_execute_trades:
                    # If we currently hold a SHORT position, BUY is a BUY TO COVER (exit short)
                    if symbol in held_map and getattr(held_map[symbol], 'side', 'LONG') == 'SHORT':
                        pos = held_map[symbol]
                        exec_res = self.engine.execute_order(symbol, "COVER", pos.quantity, execution_price=price)
                        if exec_res.get("success"):
                            trades_executed += 1
                            eval_res["action_executed"] = f"COVERED {pos.quantity} SHARES"
                            pnl = exec_res.get("realized_pnl", round((pos.average_price - price) * pos.quantity, 2))
                            roi = round((pos.average_price - price) / pos.average_price * 100, 2) if pos.average_price > 0 else 0.0
                            chg_amt = exec_res.get("charges", {}).get("total_charges", 0.0) if isinstance(exec_res.get("charges"), dict) else 0.0
                            self.record_session_trade({
                                "symbol": symbol,
                                "displayName": display_name,
                                "side": "COVER",
                                "quantity": pos.quantity,
                                "entry_price": pos.average_price,
                                "exit_price": price,
                                "price": price,
                                "total_value": round(pos.quantity * price, 2),
                                "charges": chg_amt,
                                "realized_pnl": pnl,
                                "roi_pct": roi,
                                "action": "COVER",
                                "signal": "STRATEGY_EXIT",
                                "reason": eval_res.get("reason", "Bullish Reversal Cover Signal"),
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            })
                            chg_str = f" (Charges: ₹{chg_amt:.2f})" if chg_amt > 0 else ""
                            self.add_log(f"⚡ AUTO-COVERED {pos.quantity} shares of {display_name} @ ₹{price:.2f} [Short Exit]{chg_str} | P&L: ₹{pnl:+,.2f}", "SUCCESS" if pnl >= 0 else "WARNING")
                            del held_map[symbol]

                    # Otherwise, if we do not hold this stock and have a position slot, open a LONG trade
                    elif symbol not in held_map and has_position_slot:
                        if self.name != "NIGHTHAWK" and symbol in already_traded_today:
                            eval_res["action_executed"] = "SKIPPED (Already Traded Today)"
                            self.add_log(f"⏸️ {display_name} BUY skipped: Strict 1-trade per stock intraday limit reached", "INFO")
                        else:
                            buying_power = self.allocation_per_trade * self.leverage
                            trade_qty = max(1, int(buying_power / price))
                            margin_needed = round((trade_qty * price) / self.leverage, 2)

                            if available_cash >= margin_needed:
                                exec_res = self.engine.execute_order(symbol, "BUY", trade_qty, execution_price=price)
                                if exec_res.get("success"):
                                    already_traded_today.add(symbol)
                                    trades_executed += 1
                                    available_cash -= margin_needed
                                    eval_res["action_executed"] = f"BOUGHT {trade_qty} (5x)"
                                
                                pos_rec = self.pos_model.query.filter_by(symbol=symbol).first()
                                if pos_rec:
                                    pos_rec.side = "LONG"
                                    atr_val = eval_res.get("atr_14") or (price * 0.025)
                                    if self.strategy == "123_REVERSAL":
                                        raw_sl = eval_res.get("suggested_sl")
                                        max_sl_dist = round(price * (1.0 - abs(self.stop_loss_pct) / 100.0), 2) if self.stop_loss_pct > 0 else round(price * 0.98, 2)
                                        pos_rec.stop_loss = max(raw_sl, max_sl_dist) if raw_sl else max_sl_dist
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

                                chg_amt = exec_res.get("charges", {}).get("total_charges", 0.0) if isinstance(exec_res.get("charges"), dict) else 0.0
                                strat_tag = "1-2-3 Neckline Break" if self.strategy == "123_REVERSAL" else ("Dual SMA Cross" if self.strategy == "SMA_CROSSOVER" else "RSI Reversal")
                                self.record_session_trade({
                                    "symbol": symbol,
                                    "displayName": display_name,
                                    "side": "BUY",
                                    "quantity": trade_qty,
                                    "entry_price": price,
                                    "price": price,
                                    "total_value": round(trade_qty * price, 2),
                                    "charges": chg_amt,
                                    "realized_pnl": 0.0,
                                    "roi_pct": 0.0,
                                    "action": "BUY",
                                    "signal": self.strategy,
                                    "suggested_sl": pos_rec.stop_loss if pos_rec else None,
                                    "suggested_tp": pos_rec.take_profit if pos_rec else None,
                                    "reason": f"⚡ [{strat_tag}] Entry @ ₹{price:.2f} | Stop: ₹{pos_rec.stop_loss if pos_rec else 0:.2f} | Target: ₹{pos_rec.take_profit if pos_rec else 0:.2f}",
                                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                })

                                chg_str = f" (Charges: ₹{chg_amt:.2f})" if chg_amt > 0 else ""
                                self.add_log(f"⚡ AUTO-BOUGHT {trade_qty} shares of {display_name} @ ₹{price:.2f} (Margin: ₹{margin_needed:,.2f}{chg_str}) | Target: ₹{pos_rec.take_profit:.2f} | Stop: ₹{pos_rec.stop_loss:.2f}", "SUCCESS")
                                held_map[symbol] = self.pos_model(
                                    symbol=symbol,
                                    side="LONG",
                                    quantity=trade_qty,
                                    average_price=price,
                                    peak_price=price,
                                    stop_loss=pos_rec.stop_loss if pos_rec else 0.0,
                                    take_profit=pos_rec.take_profit if pos_rec else 0.0
                                )
                            else:
                                self.add_log(f"BUY signal on {display_name}, but insufficient cash (Needed ₹{margin_needed:,.2f}, Avail ₹{available_cash:,.2f})", "WARNING")
                else:
                    eval_res["action_executed"] = "OFFLINE (Bot Stopped)"

            elif sig == "SELL":
                sells_found += 1
                if can_execute_trades:
                    # Case A: If we already hold a LONG position in this stock, exit it
                    if symbol in held_map and getattr(held_map[symbol], 'side', 'LONG') == 'LONG':
                        pos = held_map[symbol]
                        exec_res = self.engine.execute_order(symbol, "SELL", pos.quantity, execution_price=price)
                        if exec_res.get("success"):
                            trades_executed += 1
                            eval_res["action_executed"] = f"SOLD {pos.quantity} SHARES"
                            pnl = exec_res.get("realized_pnl", round((price - pos.average_price) * pos.quantity, 2))
                            roi = round((price - pos.average_price) / pos.average_price * 100, 2) if pos.average_price > 0 else 0.0
                            chg_amt = exec_res.get("charges", {}).get("total_charges", 0.0) if isinstance(exec_res.get("charges"), dict) else 0.0
                            self.record_session_trade({
                                "symbol": symbol,
                                "displayName": display_name,
                                "side": "SELL",
                                "quantity": pos.quantity,
                                "entry_price": pos.average_price,
                                "exit_price": price,
                                "price": price,
                                "total_value": round(pos.quantity * price, 2),
                                "charges": chg_amt,
                                "realized_pnl": pnl,
                                "roi_pct": roi,
                                "action": "SELL",
                                "signal": "STRATEGY_EXIT",
                                "reason": eval_res.get("reason", "Strategy Bearish Exit Signal"),
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            })

                            chg_str = f" (Charges: ₹{chg_amt:.2f})" if chg_amt > 0 else ""
                            self.add_log(f"⚡ AUTO-SOLD {pos.quantity} shares of {display_name} @ ₹{price:.2f} [Long Exit]{chg_str} | P&L: ₹{pnl:+,.2f}", "SUCCESS" if pnl >= 0 else "WARNING")
                            del held_map[symbol]

                    # Case B: If we do NOT hold this stock, enter a SHORT position!
                    elif symbol not in held_map and has_position_slot:
                        if self.name != "NIGHTHAWK" and symbol in already_traded_today:
                            eval_res["action_executed"] = "SKIPPED (Already Traded Today)"
                            self.add_log(f"⏸️ {display_name} SHORT skipped: Strict 1-trade per stock intraday limit reached", "INFO")
                        else:
                            buying_power = self.allocation_per_trade * self.leverage
                            trade_qty = max(1, int(buying_power / price))
                            margin_needed = round((trade_qty * price) / self.leverage, 2)

                            if available_cash >= margin_needed:
                                exec_res = self.engine.execute_order(symbol, "SHORT", trade_qty, execution_price=price)
                                if exec_res.get("success"):
                                    already_traded_today.add(symbol)
                                    trades_executed += 1
                                    available_cash -= margin_needed
                                    eval_res["action_executed"] = f"SHORTED {trade_qty} (5x)"

                                pos_rec = self.pos_model.query.filter_by(symbol=symbol).first()
                                if pos_rec:
                                    pos_rec.side = "SHORT"
                                    atr_val = eval_res.get("atr_14") or (price * 0.025)
                                    if self.strategy == "123_REVERSAL":
                                        raw_sl = eval_res.get("suggested_sl")
                                        min_sl_dist = round(price * (1.0 + abs(self.stop_loss_pct) / 100.0), 2) if self.stop_loss_pct > 0 else round(price * 1.02, 2)
                                        pos_rec.stop_loss = min(raw_sl, min_sl_dist) if raw_sl else min_sl_dist
                                        pos_rec.take_profit = eval_res.get("suggested_tp") or round(price * 0.96, 2)
                                    elif eval_res.get("suggested_sl") and eval_res.get("suggested_tp"):
                                        pos_rec.stop_loss = eval_res["suggested_sl"]
                                        pos_rec.take_profit = eval_res["suggested_tp"]
                                    elif self.use_atr_exits:
                                        pos_rec.stop_loss = round(price + (self.atr_multiplier_sl * atr_val), 2)
                                        pos_rec.take_profit = round(price - (self.atr_multiplier_tp * atr_val), 2)
                                    else:
                                        pos_rec.stop_loss = round(price * (1 + self.stop_loss_pct / 100), 2)
                                        pos_rec.take_profit = round(price * (1 - self.take_profit_pct / 100), 2)
                                    pos_rec.peak_price = price
                                    pos_rec.trailing_active = False
                                    try:
                                        db.session.commit()
                                    except Exception:
                                        pass

                                chg_amt = exec_res.get("charges", {}).get("total_charges", 0.0) if isinstance(exec_res.get("charges"), dict) else 0.0
                                strat_tag = "1-2-3 Neckline Breakdown" if self.strategy == "123_REVERSAL" else ("Dual SMA Death Cross" if self.strategy == "SMA_CROSSOVER" else "RSI Overbought")
                                self.record_session_trade({
                                    "symbol": symbol,
                                    "displayName": display_name,
                                    "side": "SHORT",
                                    "quantity": trade_qty,
                                    "entry_price": price,
                                    "price": price,
                                    "total_value": round(trade_qty * price, 2),
                                    "charges": chg_amt,
                                    "realized_pnl": 0.0,
                                    "roi_pct": 0.0,
                                    "action": "SHORT",
                                    "signal": self.strategy,
                                    "suggested_sl": pos_rec.stop_loss if pos_rec else None,
                                    "suggested_tp": pos_rec.take_profit if pos_rec else None,
                                    "reason": f"⚡ [{strat_tag}] Entry @ ₹{price:.2f} | Stop: ₹{pos_rec.stop_loss if pos_rec else 0:.2f} | Target: ₹{pos_rec.take_profit if pos_rec else 0:.2f}",
                                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                })

                                chg_str = f" (Charges: ₹{chg_amt:.2f})" if chg_amt > 0 else ""
                                self.add_log(f"⚡ AUTO-SHORTED {trade_qty} shares of {display_name} @ ₹{price:.2f} (Margin: ₹{margin_needed:,.2f}{chg_str}) | Target: ₹{pos_rec.take_profit:.2f} | Stop: ₹{pos_rec.stop_loss:.2f}", "SUCCESS")
                                held_map[symbol] = self.pos_model(
                                    symbol=symbol,
                                    side="SHORT",
                                    quantity=trade_qty,
                                    average_price=price,
                                    peak_price=price,
                                    stop_loss=pos_rec.stop_loss if pos_rec else 0.0,
                                    take_profit=pos_rec.take_profit if pos_rec else 0.0
                                )
                            else:
                                self.add_log(f"SELL/SHORT signal on {display_name}, but insufficient cash (Needed ₹{margin_needed:,.2f}, Avail ₹{available_cash:,.2f})", "WARNING")
                else:
                    eval_res["action_executed"] = "OFFLINE (Bot Stopped)"

            if symbol in already_traded_today and not eval_res.get("action_executed") and symbol not in held_map:
                eval_res["action_executed"] = "TRADED TODAY"

            scanner_results.append(eval_res)

        # Sort results: BUY signals first, then SELL, then highest volume/changes
        scanner_results.sort(key=lambda x: (0 if x["signal"] == "BUY" else (1 if x["signal"] in ("SELL", "STOP_LOSS", "TAKE_PROFIT") else 2), -abs(x.get("change_pct") or 0)))

        sweep_closed_trades = [r["closed_trade"] for r in scanner_results if r.get("closed_trade")]

        self.last_scan_summary = {
            "total_scanned": len(scanner_results),
            "buys_found": buys_found,
            "sells_found": sells_found,
            "trades_executed": len(self.session_trades),
            "recent_sweep_trades": trades_executed,
            "active_positions_count": len(held_map),
            "max_positions_limit": self.max_positions,
            "scanner_results": scanner_results,
            "closed_trades": sweep_closed_trades
        }

        sweep_tag = "🌙 AFTER-MARKET SWEEP (Simulated)" if after_hours else "☀️ LIVE NSE MARKET SWEEP (Real Quotes)"
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

# 1. ALPHA: Dedicated Live Market Day Engine (/bot)
# 2. NIGHTHAWK: Dedicated 24/7 After-Hours Strategy Lab (/bot-after-hours)
bot_instance = AutoTradingBot(state_filename="trading_bot_state.json", name="ALPHA", is_after_hours_forced=False)
after_hours_bot_instance = AutoTradingBot(state_filename="after_hours_bot_state.json", name="NIGHTHAWK", is_after_hours_forced=True)
