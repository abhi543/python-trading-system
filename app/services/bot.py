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

def is_alpha_trading_hours() -> bool:
    """Returns True only during Alpha's active trading window: Mon-Fri 09:15 AM to 03:00 PM IST"""
    from datetime import datetime, timezone, timedelta
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    if now_ist.weekday() >= 5:
        return False
    current_minutes = now_ist.hour * 60 + now_ist.minute
    # Alpha active strictly from 09:15 AM to 03:00 PM IST (15:00 cutoff)
    return (9 * 60 + 15) <= current_minutes < (15 * 60)

def is_intraday_squareoff_time() -> bool:
    """Returns True if current IST time is 15:00 (3:00 PM) or later (Alpha 3:00 PM auto-squareoff cutoff)"""
    from datetime import datetime, timezone, timedelta
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    if now_ist.weekday() >= 5:
        return True
    current_minutes = now_ist.hour * 60 + now_ist.minute
    # 15:00 IST is 15*60 = 900 minutes. After 15:00 or before 09:15 IST
    return current_minutes >= (15 * 60) or current_minutes < (9 * 60 + 15)

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
    
    # Position price walk: realistic drift with momentum (bounded within standard NSE intraday band)
    drift_step = random.gauss(0.001 * st["trend"], 0.003)
    st["drift_pct"] = max(-0.035, min(0.040, st["drift_pct"] + drift_step))
    
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
            default_alloc = 20000.0  # ₹20,000 per trade for ₹10 Lakhs Desk (5x: ₹1,00,000 exposure)
            from app.models.trading import AfterHoursPosition, AfterHoursTradeHistory, AfterHoursAccount
            from app.services.paper_trader import AfterHoursPaperTradingEngine
            self.pos_model = AfterHoursPosition
            self.trade_model = AfterHoursTradeHistory
            self.account_model = AfterHoursAccount
            self.engine = AfterHoursPaperTradingEngine
        else:
            default_alloc = 3000.0   # ₹3,000 per trade (5x = ₹15,000 exposure; max 6 positions fits 1 Zerodha Basket)
            from app.models.trading import Position, TradeHistory, Account
            from app.services.paper_trader import PaperTradingEngine
            self.pos_model = Position
            self.trade_model = TradeHistory
            self.account_model = Account
            self.engine = PaperTradingEngine

        persisted = _load_persistent_bot_state(self.state_filename)
        self.is_running = persisted.get("is_running", False)
        default_scan = "TOP_200_TURNOVER"
        default_strat = "AUTO_ENSEMBLE"
        self.scan_mode = persisted.get("scan_mode", default_scan)
        if self.scan_mode in ("KRONOS_UNIVERSE", "CSV_WATCHLIST"):
            self.scan_mode = "TOP_200_TURNOVER"
        self.single_symbol = persisted.get("single_symbol", "RELIANCE.NS")
        self.strategy = persisted.get("strategy", default_strat)
        if self.name == "ALPHA":
            persisted_alloc = persisted.get("allocation_per_trade")
            self.allocation_per_trade = float(persisted_alloc) if persisted_alloc is not None and float(persisted_alloc) >= 1000 else default_alloc
            persisted_max = int(persisted.get("max_positions", 6))
            self.max_positions = persisted_max if 1 <= persisted_max <= 20 else 6
        else:
            self.allocation_per_trade = float(persisted.get("allocation_per_trade", default_alloc))
            self.max_positions = int(persisted.get("max_positions", 0))
        self.leverage = float(persisted.get("leverage", 5.0))  # 5x NSE Intraday MIS Leverage
        self.slippage_pct = float(persisted.get("slippage_pct", 0.0))
        self.batch_scan_index = 0
        self.ai_signals = {}
        self.ai_signals_metadata = {}
        self.use_ai_filter = False
        self.strict_ai_mode = False
        try:
            from app.services.paper_trader import AfterHoursPaperTradingEngine
            AfterHoursPaperTradingEngine.slippage_pct = self.slippage_pct
        except Exception:
            pass
        self.interval_seconds = 60  # 1 minute (60 seconds)
        self.fast_period = int(persisted.get("fast_period", 9))
        self.slow_period = int(persisted.get("slow_period", 21))
        self.rsi_period = int(persisted.get("rsi_period", 14))
        self.rsi_oversold = float(persisted.get("rsi_oversold", 20.0))
        self.rsi_overbought = float(persisted.get("rsi_overbought", 80.0))
        self.stop_loss_pct = min(float(persisted.get("stop_loss_pct", 1.0)), 1.0)
        self.take_profit_pct = 2.0 if self.name == "ALPHA" else float(persisted.get("take_profit_pct", 1.0))
        
        # Option A: Macro Trend & Volume Confirmation
        self.use_ema200_filter = persisted.get("use_ema200_filter", True)
        self.use_volume_filter = persisted.get("use_volume_filter", True)
        self.min_volume_ratio = float(persisted.get("min_volume_ratio", 1.3))
        self.use_nifty_filter = persisted.get("use_nifty_filter", False)
        self.nifty_market_regime = persisted.get("nifty_market_regime", {"regime": "BULLISH", "price": 0.0, "percent_change": 0.0, "ema_200": 0.0})
        
        # Option B: Trailing Stop-Loss & Dynamic ATR Exits
        self.use_trailing_stop = persisted.get("use_trailing_stop", True)
        self.trailing_trigger_pct = 1.0
        self.use_atr_exits = persisted.get("use_atr_exits", False)
        self.atr_multiplier_sl = 1.5
        self.atr_multiplier_tp = 3.0

        # Institutional Survival Mode & Apex Capital Shield
        self.survival_mode = persisted.get("survival_mode", True)
        self.daily_max_drawdown_pct = float(persisted.get("daily_max_drawdown_pct", 1.5))
        self.min_risk_reward_ratio = 2.0 if self.name == "ALPHA" else float(persisted.get("min_risk_reward_ratio", 2.5))
        self.survival_breakeven_pct = float(persisted.get("survival_breakeven_pct", 0.5))
        self.enable_partial_profits = persisted.get("enable_partial_profits", True)
        self.stagnation_timeout_mins = int(persisted.get("stagnation_timeout_mins", 45))
        self.daily_circuit_tripped = False
        self.daily_profit_target_pct = float(persisted.get("daily_profit_target_pct", 2.0))
        self.lock_daily_green_target = persisted.get("lock_daily_green_target", True)
        self.daily_green_locked = persisted.get("daily_green_locked", False)
        
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
                "fast_period": self.fast_period,
                "slow_period": self.slow_period,
                "rsi_period": self.rsi_period,
                "rsi_oversold": getattr(self, "rsi_oversold", 20.0),
                "rsi_overbought": getattr(self, "rsi_overbought", 80.0),
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "use_ema200_filter": self.use_ema200_filter,
                "use_volume_filter": self.use_volume_filter,
                "min_volume_ratio": self.min_volume_ratio,
                "use_nifty_filter": self.use_nifty_filter,
                "nifty_market_regime": self.nifty_market_regime,
                "use_trailing_stop": self.use_trailing_stop,
                "use_atr_exits": self.use_atr_exits,
                "use_ai_filter": getattr(self, "use_ai_filter", True),
                "strict_ai_mode": getattr(self, "strict_ai_mode", False),
                "survival_mode": getattr(self, "survival_mode", True),
                "daily_max_drawdown_pct": getattr(self, "daily_max_drawdown_pct", 1.5),
                "min_risk_reward_ratio": getattr(self, "min_risk_reward_ratio", 2.5),
                "survival_breakeven_pct": getattr(self, "survival_breakeven_pct", 0.5),
                "enable_partial_profits": getattr(self, "enable_partial_profits", True),
                "stagnation_timeout_mins": getattr(self, "stagnation_timeout_mins", 45),
                "daily_circuit_tripped": getattr(self, "daily_circuit_tripped", False),
                "daily_profit_target_pct": getattr(self, "daily_profit_target_pct", 2.0),
                "lock_daily_green_target": getattr(self, "lock_daily_green_target", True),
                "daily_green_locked": getattr(self, "daily_green_locked", False),
                "ai_signals": getattr(self, "ai_signals", {}),
                "ai_signals_metadata": getattr(self, "ai_signals_metadata", {}),
                "session_id": self.session_id,
                "session_count": self.session_count,
                "session_start_time": self.session_start_time,
                "session_stop_time": self.session_stop_time,
                "session_status": self.session_status,
                "session_trades": self.session_trades[:100],
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

    def update_ai_signals(self, signals: dict, metadata: dict = None) -> dict:
        self.ai_signals = signals or {}
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.ai_signals_metadata = metadata or {"updated_at": now_str}
        if "updated_at" not in self.ai_signals_metadata:
            self.ai_signals_metadata["updated_at"] = now_str
        self.persist_state()
        summary = self.get_ai_signals_summary()
        return summary

    def get_ai_signals_summary(self) -> dict:
        return {
            "active": False,
            "total_synced": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "updated_at": None,
            "use_ai_filter": False,
            "signals": {}
        }

    def record_session_trade(self, trade_data: dict):
        """Records an executed trade into the current bot session report and updates metrics (deduplicated)"""
        with self.lock:
            trade_id = trade_data.get("id")
            trade_sym = trade_data.get("symbol")
            trade_side = trade_data.get("side")
            trade_ts = (trade_data.get("timestamp") or "")[:16]
            for existing in self.session_trades:
                if trade_id and existing.get("id") == trade_id:
                    return
                if trade_sym and existing.get("symbol") == trade_sym and existing.get("side") == trade_side:
                    if trade_ts and (existing.get("timestamp") or "").startswith(trade_ts):
                        return

            if not trade_data.get("timestamp"):
                trade_data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if not trade_data.get("id"):
                trade_data["id"] = f"tr_{int(time.time()*1000)}_{len(self.session_trades)+1}"

            self.session_trades.insert(0, trade_data)
            if len(self.session_trades) > 100:
                self.session_trades = self.session_trades[:100]

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
        """Prevent duplicate intraday re-entries on Alpha using TradeHistory (Night Hawk remains unlimited)."""
        if self.is_after_hours_forced or self.name != "ALPHA":
            return set()
        traded = set()
        if isinstance(client_traded_symbols, list):
            for s in client_traded_symbols:
                if s:
                    traded.add(str(s).strip().upper())
        try:
            now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            start_of_ist_day_utc = datetime(now_ist.year, now_ist.month, now_ist.day, 0, 0, 0) - timedelta(hours=5, minutes=30)
            rows = self.trade_model.query.filter(
                self.trade_model.side.in_(["BUY", "SHORT"]),
                self.trade_model.timestamp >= start_of_ist_day_utc
            ).all()
            for r in rows:
                if r.symbol:
                    traded.add(r.symbol.strip().upper())
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
        alpha_open = is_alpha_trading_hours()
        can_run = alpha_open if self.name == "ALPHA" else True
        time_restriction_msg = ""
        if self.name == "ALPHA" and not alpha_open:
            time_restriction_msg = "ALPHA operates strictly between 09:15 AM – 03:00 PM IST (Mon–Fri). Alpha is closed for today (3:00 PM cutoff reached). Switch to NIGHTHAWK for after-hours trading."

        return {
            "name": self.name,
            "is_running": is_active,
            "can_run": can_run,
            "is_market_open": market_open,
            "is_alpha_open": alpha_open,
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
            "rsi_oversold": getattr(self, "rsi_oversold", 20.0),
            "rsi_overbought": getattr(self, "rsi_overbought", 80.0),
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "use_ema200_filter": self.use_ema200_filter,
            "use_volume_filter": self.use_volume_filter,
            "min_volume_ratio": getattr(self, "min_volume_ratio", 1.3),
            "use_nifty_filter": getattr(self, "use_nifty_filter", False),
            "nifty_market_regime": getattr(self, "nifty_market_regime", {"regime": "BULLISH", "price": 0.0, "percent_change": 0.0, "ema_200": 0.0}),
            "use_trailing_stop": self.use_trailing_stop,
            "trailing_trigger_pct": self.trailing_trigger_pct,
            "use_ai_filter": getattr(self, "use_ai_filter", True),
            "strict_ai_mode": getattr(self, "strict_ai_mode", False),
            "ai_signals_summary": self.get_ai_signals_summary(),
            "use_atr_exits": self.use_atr_exits,
            "atr_multiplier_sl": self.atr_multiplier_sl,
            "atr_multiplier_tp": self.atr_multiplier_tp,
            "survival_mode": getattr(self, "survival_mode", True),
            "daily_max_drawdown_pct": getattr(self, "daily_max_drawdown_pct", 1.5),
            "min_risk_reward_ratio": getattr(self, "min_risk_reward_ratio", 2.5),
            "survival_breakeven_pct": getattr(self, "survival_breakeven_pct", 0.5),
            "enable_partial_profits": getattr(self, "enable_partial_profits", True),
            "stagnation_timeout_mins": getattr(self, "stagnation_timeout_mins", 45),
            "daily_circuit_tripped": getattr(self, "daily_circuit_tripped", False),
            "daily_profit_target_pct": getattr(self, "daily_profit_target_pct", 2.0),
            "lock_daily_green_target": getattr(self, "lock_daily_green_target", True),
            "daily_green_locked": getattr(self, "daily_green_locked", False),
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
        if "rsi_oversold" in config:
            self.rsi_oversold = float(config["rsi_oversold"])
        if "rsi_overbought" in config:
            self.rsi_overbought = float(config["rsi_overbought"])
        if "stop_loss_pct" in config:
            self.stop_loss_pct = min(float(config["stop_loss_pct"]), 1.0) # Strict 1.0% max ceiling rule
        if "take_profit_pct" in config:
            self.take_profit_pct = float(config["take_profit_pct"])
        if "interval_seconds" in config:
            self.interval_seconds = int(config["interval_seconds"])
        if "use_ema200_filter" in config:
            self.use_ema200_filter = bool(config["use_ema200_filter"])
        if "use_volume_filter" in config:
            self.use_volume_filter = bool(config["use_volume_filter"])
        if "min_volume_ratio" in config:
            self.min_volume_ratio = float(config["min_volume_ratio"])
        if "use_nifty_filter" in config:
            self.use_nifty_filter = bool(config["use_nifty_filter"])
        if "use_trailing_stop" in config:
            self.use_trailing_stop = bool(config["use_trailing_stop"])
        if "use_atr_exits" in config:
            self.use_atr_exits = bool(config["use_atr_exits"])
        if "use_ai_filter" in config:
            self.use_ai_filter = False
        if "strict_ai_mode" in config:
            self.strict_ai_mode = False
        if "survival_mode" in config:
            self.survival_mode = bool(config["survival_mode"])
        if "daily_max_drawdown_pct" in config:
            self.daily_max_drawdown_pct = float(config["daily_max_drawdown_pct"])
        if "min_risk_reward_ratio" in config:
            self.min_risk_reward_ratio = float(config["min_risk_reward_ratio"])
        if "survival_breakeven_pct" in config:
            self.survival_breakeven_pct = float(config["survival_breakeven_pct"])
        if "enable_partial_profits" in config:
            self.enable_partial_profits = bool(config["enable_partial_profits"])
        if "stagnation_timeout_mins" in config:
            self.stagnation_timeout_mins = int(config["stagnation_timeout_mins"])
        if "daily_profit_target_pct" in config:
            self.daily_profit_target_pct = float(config["daily_profit_target_pct"])
        if "lock_daily_green_target" in config:
            self.lock_daily_green_target = bool(config["lock_daily_green_target"])
        if "daily_green_locked" in config:
            self.daily_green_locked = bool(config["daily_green_locked"])
        self.persist_state()

    def start_new_session(self, config: dict = None):
        """Initializes a fresh new trading session report when the bot is started/restarted"""
        self.daily_green_locked = False
        self.daily_circuit_tripped = False
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
        # ALPHA runs ONLY during live market hours before 3:00 PM (09:15 AM – 03:00 PM IST Mon-Fri)
        # NIGHTHAWK is the 24/7 After-Hours Strategy Lab & Simulator (runs anytime)
        market_open = is_nse_market_open()
        alpha_open = is_alpha_trading_hours()

        if self.name == "ALPHA" and not alpha_open:
            msg = "ALPHA is restricted to LIVE Market hours (09:15 AM – 03:00 PM IST Mon-Fri). Trading session for Alpha closes at 3:00 PM IST. Please use NIGHTHAWK for after-hours trading!"
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
        mode_label = "TOP 200 TURNOVER STOCKS" if self.scan_mode == "TOP_200_TURNOVER" else ("ALL 2,000+ NSE STOCKS" if self.scan_mode == "ALL_NSE_2000" else ("ALL NIFTY 200 STOCKS" if self.scan_mode == "ALL_NIFTY_200" else ("ALL NIFTY 50 STOCKS" if self.scan_mode == "ALL_NIFTY_50" else self.single_symbol)))
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

    def _evaluate_ensemble(self, symbol: str, stock_data: dict, current_price: float, change_pct: float, closes: list, s_closes: pd.Series, ema_200: float, trend_200: str, vol_ratio: float, atr_14: float) -> dict:
        """
        Auto-Regime Multi-Strategy Ensemble:
        Simultaneously evaluates SMC Liquidity Sweep, VWAP+EMA+RSI Confluence, Volume Shocker,
        1-2-3 Reversal, and RSI Extreme. Dynamically weights setups based on market hour & NIFTY regime,
        and executes only the single highest-conviction setup (A+ Grade).
        """
        from datetime import datetime, timezone, timedelta
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        curr_mins = now_ist.hour * 60 + now_ist.minute
        # 09:15 = 555, 10:30 = 630, 14:00 = 840, 15:00 = 900
        if self.is_after_hours_forced:
            is_opening_session = False
            is_midday_trend = True       # Full institutional swing & trend scanning
            is_late_session = False
        else:
            is_opening_session = (555 <= curr_mins <= 630)   # 09:15 - 10:30 IST (Trap & Liquidity Sweeps)
            is_midday_trend = (630 < curr_mins <= 840)       # 10:30 - 14:00 IST (VWAP & Trend Continuation)
            is_late_session = (curr_mins > 840)              # 14:00 - 15:00 IST (Defensive / Quick Targets)

        strict_sl_pct = min(abs(self.stop_loss_pct or 1.0), 1.0)
        pdl = stock_data.get("prev_day_low") or (current_price * 0.985)
        pdh = stock_data.get("prev_day_high") or (current_price * 1.015)
        today_low = stock_data.get("today_low") or current_price
        today_high = stock_data.get("today_high") or current_price
        vwap_val = stock_data.get("vwap") or current_price
        ema9_val = stock_data.get("ema_9") or current_price
        ema21_val = stock_data.get("ema_21") or current_price
        rsi_curr = stock_data.get("rsi_14", 50.0)

        candidates = []

        # 1. Candidate: SMC Liquidity Sweep (PDL / PDH Institutional Trap Hunt)
        is_pdl_sweep_buy = (today_low < pdl) and (current_price >= pdl) and (current_price >= vwap_val * 0.997)
        is_pdh_sweep_short = (today_high > pdh) and (current_price <= pdh) and (current_price <= vwap_val * 1.003)

        if is_pdl_sweep_buy:
            raw_sl = round(today_low - 0.1 * atr_14, 2)
            strict_sl = round(current_price * (1.0 - strict_sl_pct / 100.0), 2)
            c_sl = max(raw_sl, strict_sl)
            risk_dist = max(current_price * 0.004, current_price - c_sl)
            c_tp = round(current_price + (risk_dist * 2.8), 2)
            base_score = 96 + (3 if is_opening_session else 0)
            candidates.append({
                "name": "SMC Liquidity Sweep",
                "signal": "BUY",
                "score": min(99, base_score),
                "grade": "A+",
                "suggested_sl": c_sl,
                "suggested_tp": c_tp,
                "reason": f"Swept PDL ₹{pdl:.2f} (Low ₹{today_low:.2f}) & reclaimed! Bear trap reversed | SL: ₹{c_sl:.2f} | TP: ₹{c_tp:.2f} (1:2.8 R:R)"
            })
        elif is_pdh_sweep_short:
            raw_sl = round(today_high + 0.1 * atr_14, 2)
            strict_sl = round(current_price * (1.0 + strict_sl_pct / 100.0), 2)
            c_sl = min(raw_sl, strict_sl)
            risk_dist = max(current_price * 0.004, c_sl - current_price)
            c_tp = round(current_price - (risk_dist * 2.8), 2)
            base_score = 94 + (3 if is_opening_session else 0)
            candidates.append({
                "name": "SMC Liquidity Sweep",
                "signal": "SELL",
                "score": min(99, base_score),
                "grade": "A+",
                "suggested_sl": c_sl,
                "suggested_tp": c_tp,
                "reason": f"Swept PDH ₹{pdh:.2f} (High ₹{today_high:.2f}) & rejected! Bull trap reversed | SL: ₹{c_sl:.2f} | TP: ₹{c_tp:.2f} (1:2.8 R:R)"
            })

        # 2. Candidate: VWAP + EMA + RSI Triple Confluence
        is_confluence_buy = (current_price >= vwap_val) and (ema9_val >= ema21_val) and (48.0 <= rsi_curr <= 68.0)
        is_confluence_sell = (current_price < vwap_val) and (ema9_val < ema21_val) and (32.0 <= rsi_curr <= 52.0)

        if is_confluence_buy:
            c_sl = max(round(vwap_val, 2), round(current_price * (1.0 - strict_sl_pct / 100.0), 2))
            risk_dist = max(current_price * 0.005, current_price - c_sl)
            c_tp = round(current_price + (risk_dist * 2.6), 2)
            base_score = 94 + (3 if is_midday_trend else 0)
            candidates.append({
                "name": "VWAP+EMA+RSI Confluence",
                "signal": "BUY",
                "score": min(99, base_score),
                "grade": "A+",
                "suggested_sl": c_sl,
                "suggested_tp": c_tp,
                "reason": f"Above VWAP (₹{vwap_val:.2f}) | 9 EMA > 21 EMA | RSI {rsi_curr:.1f} (Bullish Expansion)"
            })
        elif is_confluence_sell:
            c_sl = min(round(vwap_val, 2), round(current_price * (1.0 + strict_sl_pct / 100.0), 2))
            risk_dist = max(current_price * 0.005, c_sl - current_price)
            c_tp = round(current_price - (risk_dist * 2.6), 2)
            base_score = 92 + (3 if is_midday_trend else 0)
            candidates.append({
                "name": "VWAP+EMA+RSI Confluence",
                "signal": "SELL",
                "score": min(99, base_score),
                "grade": "A+",
                "suggested_sl": c_sl,
                "suggested_tp": c_tp,
                "reason": f"Below VWAP (₹{vwap_val:.2f}) | 9 EMA < 21 EMA | RSI {rsi_curr:.1f} (Bearish Pullback)"
            })

        # 3. Candidate: Volume Shocker (Institutional Surge)
        is_shocker_buy = (vol_ratio >= 1.75) and (0.35 <= change_pct <= 9.5) and (current_price >= (ema_200 * 0.98 if ema_200 else current_price * 0.95))
        if is_shocker_buy:
            c_sl = round(current_price * (1.0 - strict_sl_pct / 100.0), 2)
            c_tp = round(current_price * 1.045, 2)
            v_score = min(99, int(88 + min(11, (vol_ratio - 1.5) * 5)))
            candidates.append({
                "name": "Volume Shocker",
                "signal": "BUY",
                "score": v_score,
                "grade": "A+",
                "suggested_sl": c_sl,
                "suggested_tp": c_tp,
                "reason": f"Volume Shocker {vol_ratio:.1f}x surge with +{change_pct:.2f}% institutional expansion"
            })

        # 4. Candidate: 1-2-3 Reversal Map
        try:
            from app.services.reversal_123 import Reversal123Engine
            highs = stock_data.get("highs") or closes
            lows = stock_data.get("lows") or closes
            opens = stock_data.get("opens") or closes
            vols = stock_data.get("volumes") or [1.0] * len(closes)
            df_pat = pd.DataFrame({"high": highs, "low": lows, "close": closes, "open": opens, "volume": vols})
            pat_res = Reversal123Engine.evaluate_123_reversal(df_pat)
            p_sig = pat_res.get("signal", "HOLD")
            p_score = pat_res.get("score") or 0
            if p_sig in ["BUY", "SELL", "SHORT"] and p_score >= 88:
                raw_sl = pat_res.get("stop_loss_price")
                if p_sig == "BUY":
                    strict_sl = round(current_price * (1.0 - strict_sl_pct / 100.0), 2)
                    c_sl = max(raw_sl, strict_sl) if raw_sl else strict_sl
                else:
                    strict_sl = round(current_price * (1.0 + strict_sl_pct / 100.0), 2)
                    c_sl = min(raw_sl, strict_sl) if raw_sl else strict_sl
                c_tp = pat_res.get("target_price") or (round(current_price * 1.035, 2) if p_sig == "BUY" else round(current_price * 0.965, 2))
                candidates.append({
                    "name": "1-2-3 Reversal",
                    "signal": "BUY" if p_sig == "BUY" else "SELL",
                    "score": p_score,
                    "grade": pat_res.get("grade", "A"),
                    "suggested_sl": c_sl,
                    "suggested_tp": c_tp,
                    "reason": pat_res.get("reason", "1-2-3 Neckline Trigger")
                })
        except Exception:
            pass

        # 5. Candidate: RSI Extreme 20/80 Mean Reversion
        if rsi_curr <= 20.0:
            c_sl = round(current_price * (1.0 - strict_sl_pct / 100.0), 2)
            c_tp = round(current_price * 1.03, 2)
            candidates.append({
                "name": "RSI Extreme (20/80)",
                "signal": "BUY",
                "score": min(99, int(92 + max(0, (20.0 - rsi_curr) * 2))),
                "grade": "A+",
                "suggested_sl": c_sl,
                "suggested_tp": c_tp,
                "reason": f"RSI Extreme Oversold ({rsi_curr:.1f} <= 20) - Mean Reversion"
            })
        elif rsi_curr >= 80.0:
            c_sl = round(current_price * (1.0 + strict_sl_pct / 100.0), 2)
            c_tp = round(current_price * 0.97, 2)
            candidates.append({
                "name": "RSI Extreme (20/80)",
                "signal": "SELL",
                "score": min(99, int(92 + max(0, (rsi_curr - 80.0) * 2))),
                "grade": "A+",
                "suggested_sl": c_sl,
                "suggested_tp": c_tp,
                "reason": f"RSI Extreme Overbought ({rsi_curr:.1f} >= 80) - Mean Reversion"
            })

        actionable = [c for c in candidates if c["signal"] in ["BUY", "SELL"]]
        if is_late_session:
            actionable = [c for c in actionable if c["score"] >= 95]

        if actionable:
            actionable.sort(key=lambda x: x["score"], reverse=True)
            best = actionable[0]
            if self.is_after_hours_forced:
                session_label = "Night Hawk 24/7 Swing Scan"
            else:
                session_label = "Morning Sweep" if is_opening_session else ("Mid-Day Trend" if is_midday_trend else "Pre-Close Defense")
            return {
                "signal": best["signal"],
                "reason": f"🤖 [Auto-Pilot: {best['name']} | {session_label}] {best['reason']}",
                "suggested_sl": best["suggested_sl"],
                "suggested_tp": best["suggested_tp"],
                "reversal_score": best["score"],
                "reversal_grade": best["grade"],
                "strat_tag": f"Auto-Pilot ({best['name']})"
            }

        if self.is_after_hours_forced:
            session_label = "Night Hawk 24/7 Setup Scan"
        else:
            session_label = "Morning Sweep Hunt" if is_opening_session else ("Mid-Day Trend Scan" if is_midday_trend else "Pre-Close Capital Defense")
        pdl_dist = round(((current_price - pdl) / pdl) * 100, 2)
        pdh_dist = round(((current_price - pdh) / pdh) * 100, 2)
        return {
            "signal": "HOLD",
            "reason": f"🤖 Auto-Pilot [{session_label}]: PDL ₹{pdl:.1f} ({pdl_dist:+0.1f}%), PDH ₹{pdh:.1f} ({pdh_dist:+0.1f}%), VWAP ₹{vwap_val:.1f}, RSI {rsi_curr:.1f} | Awaiting A+ institutional setup",
            "suggested_sl": round(current_price * (1.0 - strict_sl_pct / 100.0), 2),
            "suggested_tp": round(current_price * 1.025, 2),
            "reversal_score": None,
            "reversal_grade": None,
            "strat_tag": "Auto-Pilot Ensemble"
        }

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

        # Confluence / Expected Move
        ai_signals_map = getattr(self, "ai_signals", {})
        ai_info = ai_signals_map.get(symbol) or ai_signals_map.get(display_name) or ai_signals_map.get(f"{display_name}.NS")
        ai_bias = str(ai_info.get("bias", "")).upper() if ai_info else None
        raw_exp = float(ai_info.get("expected_pct") or ai_info.get("expected_move_pct") or 0.0) if ai_info else float(change_pct or 0.0)
        # Enforce realistic moves (-5.0% to +5.0% max for equities)
        ai_exp_pct = max(-5.0, min(5.0, round(raw_exp, 2)))

        # Check existing position (match with or without .NS)
        clean_s = symbol.replace('.NS', '').strip().upper()
        pos = held_positions_map.get(symbol) or held_positions_map.get(clean_s) or held_positions_map.get(f"{clean_s}.NS")
        
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
            be_target = self.survival_breakeven_pct if getattr(self, "survival_mode", True) else self.trailing_trigger_pct
            if self.use_trailing_stop:
                # Stage 1: Breakeven Shield (+0.65% locks stop to breakeven + fees)
                if pnl_pct >= be_target and not getattr(pos, 'trailing_active', False):
                    pos.trailing_active = True
                    cur_sl = getattr(pos, 'stop_loss', 0.0) or 0.0
                    be_fee_buffer = 1.0015 if not is_short else 0.9985
                    be_price = round(avg_price * be_fee_buffer, 2)
                    should_raise_sl = (cur_sl < be_price) if not is_short else (cur_sl > be_price or cur_sl == 0.0)
                    if should_raise_sl:
                        pos.stop_loss = be_price
                        try:
                            db.session.commit()
                        except Exception:
                            pass
                        self.add_log(f"🛡️ [SURVIVAL BREAKEVEN] {display_name} ({pos_side}): Gain reached +{pnl_pct:.2f}%. Stop moved to Breakeven+Fees (₹{be_price:.2f}). Trade is 100% Risk-Free!", "SUCCESS")

                # Stage 1.5: Survival Mode Partial Profit Scale-Out (Bank 50% cash at +1.5%)
                if getattr(self, "enable_partial_profits", True) and pnl_pct >= 1.5 and not getattr(pos, 'partial_profit_taken', False) and pos.quantity >= 2:
                    scale_qty = int(pos.quantity // 2)
                    if scale_qty > 0 and allow_order_execution:
                        self.add_log(f"💰 [PARTIAL PROFIT SCALER] {display_name} ({pos_side}): Target 1 (+{pnl_pct:.2f}%) reached! Selling 50% ({scale_qty} shares) to bank cash into equity.", "SUCCESS")
                        partial_res = self.engine.execute_order(symbol, exit_order_side, scale_qty, execution_price=current_price)
                        if partial_res and partial_res.get("success"):
                            pos.partial_profit_taken = True
                            pos.trailing_active = True
                            pos.stop_loss = round(avg_price * (1.0015 if not is_short else 0.9985), 2)
                            try:
                                db.session.commit()
                            except Exception:
                                pass

                # Stage 1.8: Stagnation Timeout Exit (Stuck in chop for > 45 mins with 0 move)
                if getattr(self, "survival_mode", True) and hasattr(pos, 'updated_at') and pos.updated_at:
                    try:
                        from datetime import timezone
                        now_utc = datetime.now(timezone.utc)
                        entry_utc = pos.updated_at
                        if entry_utc.tzinfo is None:
                            entry_utc = entry_utc.replace(tzinfo=timezone.utc)
                        mins_held = (now_utc - entry_utc).total_seconds() / 60.0
                        if mins_held >= getattr(self, "stagnation_timeout_mins", 45) and abs(pnl_pct) < 0.25:
                            if allow_order_execution:
                                self.add_log(f"⏱️ [STAGNATION EXIT] {display_name} ({pos_side}): Inactive for {mins_held:.0f} mins with zero breakout move. Closing at market to free capital.", "INFO")
                                stag_res = self.engine.execute_order(symbol, exit_order_side, pos.quantity, execution_price=current_price)
                                if stag_res and stag_res.get("success"):
                                    held_positions_map.pop(symbol, None)
                                    return {
                                        "symbol": symbol,
                                        "displayName": display_name,
                                        "price": current_price,
                                        "change_pct": change_pct,
                                        "signal": "STAGNATION_EXIT",
                                        "reason": f"Stagnation timeout ({mins_held:.0f} mins)",
                                        "action_executed": f"STAGNATION CLOSED ({pos.quantity})"
                                    }
                    except Exception:
                        pass

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
                            locked_amt = round(abs(dynamic_stop - avg_price) * pos.quantity, 2)
                            self.add_log(f"📈 [TRAILING STOP TRAIL] {display_name} (LONG): High ₹{extreme:.2f}! Stop raised to ₹{dynamic_stop:.2f} (Locked: +₹{locked_amt:.2f})", "SUCCESS")
                    else:
                        dynamic_stop = round(extreme + trail_distance, 2)
                        if (current_sl == 0.0 or dynamic_stop < current_sl) and dynamic_stop < avg_price:
                            pos.stop_loss = dynamic_stop
                            try:
                                db.session.commit()
                            except Exception:
                                pass
                            locked_amt = round(abs(avg_price - dynamic_stop) * pos.quantity, 2)
                            self.add_log(f"📉 [TRAILING STOP TRAIL] {display_name} (SHORT): Low ₹{extreme:.2f}! Stop lowered to ₹{dynamic_stop:.2f} (Locked: +₹{locked_amt:.2f})", "SUCCESS")

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
                    if exec_res and exec_res.get("success"):
                        held_positions_map.pop(symbol, None)
                        held_positions_map.pop(clean_s, None)
                        held_positions_map.pop(f"{clean_s}.NS", None)
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
                is_win_lock = bool(closed_trade_dict and closed_trade_dict.get("is_win", False))
                action_exec = "PROFIT_LOCKED" if is_win_lock else ("STOP_EXIT" if closed_trade_dict else ("ORDER_FAILED" if allow_order_execution else "OFFLINE (No orders placed)"))
                return {
                    "symbol": symbol, "displayName": display_name, "price": current_price,
                    "change_pct": change_pct, "signal": "TAKE_PROFIT" if is_win_lock else "STOP_LOSS", 
                    "reason": f"Locked Profit achieved @ ₹{current_price:.2f} (SL: ₹{sl_level:.2f})" if is_win_lock else f"Indicator Stop-Loss hit @ ₹{current_price:.2f} (SL: ₹{sl_level:.2f}, {lock_pnl:+.2f}%)",
                    "action_executed": action_exec, "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14,
                    "closed_trade": closed_trade_dict,
                    "ai_bias": ai_bias or "NEUTRAL",
                    "ai_expected_pct": ai_exp_pct,
                    "suggested_sl": sl_level,
                    "suggested_tp": getattr(pos, 'take_profit', None) or round(current_price * 1.025, 2)
                }

            # Check if Strict 1.0% Stop-Loss Ceiling triggered (Protects ALL strategies including 1-2-3 Reversal)
            effective_sl_pct = min(abs(self.stop_loss_pct or 1.0), 1.0)
            if pnl_pct <= -effective_sl_pct:
                closed_trade_dict = None
                if allow_order_execution:
                    self.add_log(f"🛑 [STRICT 1% SL HIT] {display_name} ({pos_side}): Max risk limit reached at -{abs(pnl_pct):.2f}% (Strict 1.0% SL Ceiling). Liquidating {pos.quantity} shares @ ₹{current_price:.2f} to protect capital!", "ERROR")
                    exec_res = self.engine.execute_order(symbol, exit_order_side, pos.quantity, execution_price=current_price)
                    if exec_res and exec_res.get("success"):
                        held_positions_map.pop(symbol, None)
                        held_positions_map.pop(clean_s, None)
                        held_positions_map.pop(f"{clean_s}.NS", None)
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
                    "action_executed": "LIQUIDATED" if closed_trade_dict else ("ORDER_FAILED" if allow_order_execution else "OFFLINE (No orders placed)"), "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14,
                    "closed_trade": closed_trade_dict,
                    "ai_bias": ai_bias or "NEUTRAL",
                    "ai_expected_pct": ai_exp_pct,
                    "suggested_sl": getattr(pos, 'stop_loss', None) or round(current_price * 0.99, 2),
                    "suggested_tp": getattr(pos, 'take_profit', None) or round(current_price * 1.025, 2)
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
                    if exec_res and exec_res.get("success"):
                        held_positions_map.pop(symbol, None)
                        held_positions_map.pop(clean_s, None)
                        held_positions_map.pop(f"{clean_s}.NS", None)
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
                    "action_executed": "PROFIT_LOCKED" if closed_trade_dict else ("ORDER_FAILED" if allow_order_execution else "OFFLINE (No orders placed)"), "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14,
                    "closed_trade": closed_trade_dict,
                    "ai_bias": ai_bias or "NEUTRAL",
                    "ai_expected_pct": ai_exp_pct,
                    "suggested_sl": getattr(pos, 'stop_loss', None) or round(current_price * 0.99, 2),
                    "suggested_tp": tp_level if tp_level > 0 else (getattr(pos, 'take_profit', None) or round(current_price * 1.025, 2))
                }

        # 2. Strategy Indicator Evaluation
        req_bars = (self.rsi_period + 2) if self.strategy in ["RSI_20_80", "RSI_MOMENTUM", "RSI"] else (max(self.slow_period, self.rsi_period) + 2)
        if not closes or len(closes) < req_bars:
            return {
                "symbol": symbol, "displayName": display_name, "price": current_price,
                "change_pct": change_pct, "signal": "HOLD", "reason": "Insufficient candles",
                "fast_sma": None, "slow_sma": None, "rsi": None, "action_executed": None,
                "ema_200": ema_200, "trend_200": trend_200, "vol_ratio": vol_ratio, "atr_14": atr_14,
                "ai_bias": ai_bias or "NEUTRAL",
                "ai_expected_pct": ai_exp_pct,
                "suggested_sl": round(current_price * 0.99, 2),
                "suggested_tp": round(current_price * 1.025, 2)
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
        strat_tag = "Auto-Pilot Ensemble" if self.strategy in ["AUTO_ENSEMBLE", "AUTO_REGIME", "MULTI_STRATEGY"] else self.strategy

        if self.strategy in ["AUTO_ENSEMBLE", "AUTO_REGIME", "MULTI_STRATEGY"]:
            ens_res = self._evaluate_ensemble(symbol, stock_data, current_price, change_pct, closes, s_closes, ema_200, trend_200, vol_ratio, atr_14)
            signal = ens_res["signal"]
            reason = ens_res["reason"]
            suggested_sl = ens_res["suggested_sl"]
            suggested_tp = ens_res["suggested_tp"]
            reversal_score = ens_res["reversal_score"]
            reversal_grade = ens_res["reversal_grade"]
            strat_tag = ens_res["strat_tag"]

        elif self.strategy == "123_REVERSAL":
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

            # Strict 1.0% Stop-Loss Ceiling Rule for 1-2-3 Reversal (Both ALPHA and NIGHTHAWK)
            strict_sl_pct = min(abs(self.stop_loss_pct or 1.0), 1.0)
            if signal == "BUY":
                strict_sl = round(current_price * (1.0 - strict_sl_pct / 100.0), 2)
                suggested_sl = max(suggested_sl, strict_sl) if suggested_sl else strict_sl
                reason += f" | Strict 1.0% SL: ₹{suggested_sl:.2f}"
            elif signal in ["SELL", "SHORT"]:
                strict_sl = round(current_price * (1.0 + strict_sl_pct / 100.0), 2)
                suggested_sl = min(suggested_sl, strict_sl) if suggested_sl else strict_sl
                reason += f" | Strict 1.0% SL: ₹{suggested_sl:.2f}"

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

        elif self.strategy in ["RSI_20_80", "RSI_MOMENTUM", "RSI"]:
            delta = s_closes.diff()
            gain = (delta.where(delta > 0, 0.0)).rolling(window=self.rsi_period).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(window=self.rsi_period).mean()
            rs = gain / (loss + 1e-9)
            rsi = 100.0 - (100.0 / (1.0 + rs))
            current_rsi = float(rsi.iloc[-1])
            rsi_val = round(current_rsi, 1)

            oversold_th = getattr(self, "rsi_oversold", 20.0)
            overbought_th = getattr(self, "rsi_overbought", 80.0)

            if current_rsi <= oversold_th:
                signal = "BUY"
                suggested_sl = round(current_price * 0.99, 2)
                suggested_tp = round(current_price * 1.03, 2)
                reason = f"⚡ RSI Extreme Oversold (RSI: {current_rsi:.1f} <= {oversold_th:.0f}) - Strong BUY Signal"
                reversal_score = min(99, int(92 + max(0, (oversold_th - current_rsi) * 2)))
                reversal_grade = "A+"
            elif current_rsi >= overbought_th:
                signal = "SELL"
                suggested_sl = round(current_price * 1.01, 2)
                suggested_tp = round(current_price * 0.97, 2)
                reason = f"⚡ RSI Extreme Overbought (RSI: {current_rsi:.1f} >= {overbought_th:.0f}) - SELL / Exit Signal"
                reversal_score = min(99, int(92 + max(0, (current_rsi - overbought_th) * 2)))
                reversal_grade = "A+"
            else:
                reason = f"RSI Neutral ({current_rsi:.1f}) | Buy at <= {oversold_th:.0f}, Sell at >= {overbought_th:.0f}"

        elif self.strategy == "VOLUME_SHOCKER":
            # ⚡ VOLUME SHOCKER STRATEGY: Institutional Accumulation / Distribution (RVOL >= 1.75x, Anti-Chase <= 3.5%)
            is_shocker_buy = (vol_ratio >= 1.75) and (0.35 <= change_pct <= 3.5) and (current_price >= (ema_200 * 0.98 if ema_200 else current_price * 0.95))
            is_shocker_short = (vol_ratio >= 1.75) and (-3.5 <= change_pct <= -0.35) and (current_price <= (ema_200 * 1.02 if ema_200 else current_price * 1.05))
            if is_shocker_buy:
                signal = "BUY"
                reason = f"⚡ Volume Shocker: {vol_ratio:.1f}x volume surge with +{change_pct:.2f}% institutional expansion"
                reversal_score = min(99, int(88 + min(11, (vol_ratio - 1.5) * 5)))
                reversal_grade = "A+"
                suggested_sl = round(current_price * 0.99, 2)
                suggested_tp = round(current_price * 1.03, 2)
            elif is_shocker_short:
                signal = "SELL"
                reason = f"⚡ Volume Shocker Short: {vol_ratio:.1f}x volume surge with {change_pct:.2f}% institutional breakdown"
                reversal_score = min(99, int(88 + min(11, (vol_ratio - 1.5) * 5)))
                reversal_grade = "A+"
                suggested_sl = round(current_price * 1.01, 2)
                suggested_tp = round(current_price * 0.97, 2)
            else:
                reason = f"RVOL {vol_ratio:.1f}x below volume shock threshold (1.75x) or waiting for price confirmation"

        elif self.strategy == "DMA_200":
            # 📈 200-DAY MOVING AVERAGE BREAKOUT & REGIME STRATEGY
            dma_val = ema_200 or (current_price * 0.98)
            dist_pct = round(((current_price - dma_val) / dma_val) * 100, 2)
            if current_price >= dma_val:
                signal = "BUY"
                suggested_sl = round(dma_val, 2)
                sl_dist = max(current_price * 0.005, current_price - suggested_sl)
                suggested_tp = round(current_price + (sl_dist * 2.8), 2)
                reason = f"📈 200 DMA Bullish: Price ₹{current_price:.2f} above 200 DMA (₹{suggested_sl:.2f}, +{dist_pct:.1f}%) | Stop Loss = Exact 200 DMA"
                reversal_score = 92
                reversal_grade = "A+"
            else:
                signal = "SELL"
                suggested_sl = round(dma_val, 2)
                sl_dist = max(current_price * 0.005, suggested_sl - current_price)
                suggested_tp = round(current_price - (sl_dist * 2.8), 2)
                reason = f"📉 200 DMA Bearish Breakdown: Price ₹{current_price:.2f} below 200 DMA (₹{suggested_sl:.2f}, {dist_pct:.1f}%) | Stop Loss = Exact 200 DMA"
                reversal_score = 90
                reversal_grade = "A"

        elif self.strategy in ["SMC_LIQUIDITY_SWEEP", "PDL_SWEEP"]:
            # ⚡ SMC LIQUIDITY SWEEP STRATEGY (PDL / PDH Institutional Trap Reversal)
            pdl = stock_data.get("prev_day_low") or (current_price * 0.985)
            pdh = stock_data.get("prev_day_high") or (current_price * 1.015)
            today_low = stock_data.get("today_low") or current_price
            today_high = stock_data.get("today_high") or current_price
            vwap_val = stock_data.get("vwap") or current_price
            atr = stock_data.get("atr_14") or (current_price * 0.015)

            # Bullish PDL Sweep (Long): Price swept below Previous Day Low, then closed/reclaimed back above PDL
            # Confluence: Price is near or above intraday VWAP
            is_pdl_sweep_buy = (today_low < pdl) and (current_price >= pdl) and (current_price >= vwap_val * 0.997)

            # Bearish PDH Sweep (Short): Price swept above Previous Day High, then rejected/closed back below PDH
            # Confluence: Price is near or below intraday VWAP
            is_pdh_sweep_short = (today_high > pdh) and (current_price <= pdh) and (current_price <= vwap_val * 1.003)

            strict_sl_pct = min(abs(self.stop_loss_pct or 1.0), 1.0)

            if is_pdl_sweep_buy:
                signal = "BUY"
                raw_sl = round(today_low - 0.1 * atr, 2)
                # Strict 1.0% stop-loss ceiling protection
                strict_sl = round(current_price * (1.0 - strict_sl_pct / 100.0), 2)
                suggested_sl = max(raw_sl, strict_sl)
                risk_dist = max(current_price * 0.004, current_price - suggested_sl)
                suggested_tp = round(current_price + (risk_dist * 2.8), 2)
                reason = f"⚡ SMC PDL Sweep BUY: Swept PDL ₹{pdl:.2f} (Low ₹{today_low:.2f}) & reclaimed! Bear trap reversed | SL: ₹{suggested_sl:.2f} | TP: ₹{suggested_tp:.2f} (1:2.8 R:R)"
                reversal_score = 96
                reversal_grade = "A+"
            elif is_pdh_sweep_short:
                signal = "SELL"
                raw_sl = round(today_high + 0.1 * atr, 2)
                # Strict 1.0% stop-loss ceiling protection
                strict_sl = round(current_price * (1.0 + strict_sl_pct / 100.0), 2)
                suggested_sl = min(raw_sl, strict_sl)
                risk_dist = max(current_price * 0.004, suggested_sl - current_price)
                suggested_tp = round(current_price - (risk_dist * 2.8), 2)
                reason = f"⚡ SMC PDH Sweep SHORT: Swept PDH ₹{pdh:.2f} (High ₹{today_high:.2f}) & rejected! Bull trap reversed | SL: ₹{suggested_sl:.2f} | TP: ₹{suggested_tp:.2f} (1:2.8 R:R)"
                reversal_score = 94
                reversal_grade = "A+"
            else:
                pdl_dist = round(((current_price - pdl) / pdl) * 100, 2)
                pdh_dist = round(((current_price - pdh) / pdh) * 100, 2)
                reason = f"SMC Neutral: PDL ₹{pdl:.2f} ({pdl_dist:+0.2f}%), PDH ₹{pdh:.2f} ({pdh_dist:+0.2f}%) | Awaiting liquidity sweep outside range"

        elif self.strategy == "VWAP_EMA_RSI":
            # ⚡ TRIPLE CONFLUENCE STRATEGY: VWAP + EMA + RSI
            vwap_val = stock_data.get("vwap") or (current_price * 0.995)
            ema9_val = stock_data.get("ema_9") or current_price
            ema21_val = stock_data.get("ema_21") or current_price
            rsi_curr = stock_data.get("rsi_14", 50.0)
            rsi_val = round(rsi_curr, 1)

            # Long Setup: Price >= VWAP, 9 EMA > 21 EMA, RSI Bullish Support (48 to 68)
            is_confluence_buy = (current_price >= vwap_val) and (ema9_val >= ema21_val) and (48.0 <= rsi_curr <= 68.0)
            
            # Short Setup: Price < VWAP, 9 EMA < 21 EMA, RSI Bearish Breakdown (32 to 52)
            is_confluence_sell = (current_price < vwap_val) and (ema9_val < ema21_val) and (32.0 <= rsi_curr <= 52.0)

            if is_confluence_buy:
                signal = "BUY"
                suggested_sl = round(vwap_val, 2)
                risk_dist = max(current_price * 0.005, current_price - suggested_sl)
                suggested_tp = round(current_price + (risk_dist * 2.6), 2)
                reason = f"⚡ Triple Confluence BUY: Above VWAP (₹{vwap_val:.2f}) | 9 EMA > 21 EMA | RSI {rsi_curr:.1f} (Bullish Thrust) | Stop Loss = Exact VWAP"
                reversal_score = 94
                reversal_grade = "A+"
            elif is_confluence_sell:
                signal = "SELL"
                suggested_sl = round(vwap_val, 2)
                risk_dist = max(current_price * 0.005, suggested_sl - current_price)
                suggested_tp = round(current_price - (risk_dist * 2.6), 2)
                reason = f"⚡ Triple Confluence SHORT: Below VWAP (₹{vwap_val:.2f}) | 9 EMA < 21 EMA | RSI {rsi_curr:.1f} (Bearish Pullback) | Stop Loss = Exact VWAP"
                reversal_score = 92
                reversal_grade = "A+"
            else:
                bias_hint = "Price vs VWAP" if (current_price >= vwap_val) else "Sub-VWAP"
                reason = f"Triple Confluence Neutral: {bias_hint} | 9 EMA: ₹{ema9_val:.2f}, 21 EMA: ₹{ema21_val:.2f}, RSI: {rsi_curr:.1f}"

        # Filter BUY/SELL signals with 200 EMA Macro Trend, NIFTY 50 Regime & Volume Breakout
        filter_status = None
        nifty_reg = getattr(self, "nifty_market_regime", {}).get("regime", "BULLISH")
        min_vol = getattr(self, "min_volume_ratio", 1.3)

        if signal == "BUY" and (not pos or getattr(pos, 'side', 'LONG') == 'SHORT'):
            if self.strategy in ["RSI_20_80", "RSI_MOMENTUM", "RSI"]:
                # Pure RSI Extreme mean-reversion strategy is built to buy deep oversold pullbacks (RSI <= 20)
                pass
            elif getattr(self, 'use_nifty_filter', False) and nifty_reg == "BEARISH":
                signal = "HOLD"
                reason = f"BUY Filtered: NIFTY 50 is BEARISH ({nifty_reg}) | Macro Market Trend Filter"
                filter_status = "FILTERED_NIFTY_REGIME"
            elif self.use_ema200_filter and trend_200 == "BEARISH":
                signal = "HOLD"
                reason = f"BUY Filtered: Bearish Stock Trend (Price ₹{current_price:.1f} < 200 EMA ₹{ema_200})"
                filter_status = "FILTERED_200EMA"
            elif self.use_volume_filter and vol_ratio < min_vol:
                signal = "HOLD"
                reason = f"BUY Filtered: Weak Volume Surge ({vol_ratio:.2f}x < {min_vol:.1f}x threshold)"
                filter_status = "FILTERED_VOLUME"
        elif signal == "SELL" and not pos:
            # For new SHORT entries:
            if self.strategy in ["RSI_20_80", "RSI_MOMENTUM", "RSI"]:
                pass
            elif getattr(self, 'use_nifty_filter', False) and nifty_reg == "BULLISH":
                signal = "HOLD"
                reason = f"SHORT Filtered: NIFTY 50 is BULLISH ({nifty_reg}) | Macro Market Trend Filter"
                filter_status = "FILTERED_NIFTY_REGIME"
            elif self.use_ema200_filter and trend_200 == "BULLISH":
                signal = "HOLD"
                reason = f"SHORT Filtered: Bullish Stock Trend (Price ₹{current_price:.1f} > 200 EMA ₹{ema_200})"
                filter_status = "FILTERED_200EMA"
            elif self.use_volume_filter and vol_ratio < min_vol:
                signal = "HOLD"
                reason = f"SHORT Filtered: Weak Volume Surge ({vol_ratio:.2f}x < {min_vol:.1f}x threshold)"
                filter_status = "FILTERED_VOLUME"

        # Anti-chase guard for Bot ALPHA: never enter new trades on stocks already extended > 3.5% on the day
        if self.name == "ALPHA" and not self.is_after_hours_forced and signal in ["BUY", "SELL"] and not pos and abs(change_pct) > 3.5:
            signal = "HOLD"
            reason = f"🛡️ Anti-Chase Guard: Day move ({change_pct:+.2f}%) already extended (> ±3.5%)"
            filter_status = "FILTERED_EXTENDED"

        # Intraday 1:2.0 Set & Forget Risk-to-Reward for Bot ALPHA (1% SL / 2% TP)
        if self.name == "ALPHA" and not self.is_after_hours_forced and signal in ["BUY", "SELL"] and not pos:
            pot_sl = suggested_sl or (round(current_price * (1 - min(abs(self.stop_loss_pct or 1.0), 1.0) / 100.0), 2) if signal == "BUY" else round(current_price * (1 + min(abs(self.stop_loss_pct or 1.0), 1.0) / 100.0), 2))
            suggested_sl = pot_sl
            risk_dist = max(current_price * 0.002, abs(current_price - pot_sl))
            suggested_tp = round(current_price + (risk_dist * 2.0), 2) if signal == "BUY" else round(current_price - (risk_dist * 2.0), 2)

        # Survival Mode Asymmetric Risk-to-Reward Floor (1:2.0 for Alpha Intraday, 1:2.5 for Night Hawk)
        if signal in ["BUY", "SELL"] and not pos and getattr(self, "survival_mode", True):
            pot_sl = suggested_sl or (current_price * 0.99 if signal == "BUY" else current_price * 1.01)
            pot_tp = suggested_tp or (current_price * 1.02 if self.name == "ALPHA" else current_price * 1.025 if signal == "BUY" else current_price * 0.98 if self.name == "ALPHA" else current_price * 0.975)
            pot_risk = abs(current_price - pot_sl)
            pot_reward = abs(pot_tp - current_price)
            rr_val = round(pot_reward / pot_risk, 2) if pot_risk > 0 else 0.0
            min_rr = 2.0 if (self.name == "ALPHA" and not self.is_after_hours_forced) else getattr(self, "min_risk_reward_ratio", 2.5)
            if rr_val < min_rr:
                signal = "HOLD"
                reason = f"🛡️ Survival Shield: Setup R:R (1:{rr_val:.1f}) below institutional 1:{min_rr:.1f} floor"
                filter_status = "FILTERED_SURVIVAL_RR"

        return {
            "symbol": symbol,
            "displayName": display_name,
            "price": current_price,
            "change_pct": change_pct,
            "vol_ratio": round(float(vol_ratio), 2),
            "signal": signal,
            "reason": reason,
            "ai_bias": ai_bias,
            "ai_expected_pct": ai_exp_pct,
            "fast_sma": fast_sma,
            "slow_sma": slow_sma,
            "rsi": rsi_val,
            "vwap": stock_data.get("vwap"),
            "ema_9": stock_data.get("ema_9"),
            "ema_21": stock_data.get("ema_21"),
            "reversal_score": reversal_score,
            "reversal_grade": reversal_grade,
            "suggested_sl": suggested_sl,
            "suggested_tp": suggested_tp,
            "stop_loss": suggested_sl,
            "take_profit": suggested_tp,
            "risk_per_share": round(abs(current_price - (suggested_sl or (current_price * 0.99 if signal == "BUY" else current_price * 1.01))), 2),
            "max_loss_pct": 1.0,
            "ema_200": ema_200,
            "trend_200": trend_200,
            "vol_ratio": vol_ratio,
            "atr_14": atr_14,
            "filter_status": filter_status,
            "strat_tag": strat_tag,
            "action_executed": None
        }

    def auto_square_off_all_positions(self, reason: str = "3:00 PM EOD Auto Square-Off") -> list:
        """
        Automatically liquidates every open intraday position at current market prices
        at Alpha cutoff (15:00 IST / 3:00 PM) or when market is closed, returning margin to cash.
        """
        positions = self.pos_model.query.filter(self.pos_model.quantity > 0).all()
        if not positions:
            return []

        closed_trades = []
        for pos in positions:
            sym = pos.symbol
            qty = pos.quantity
            side = getattr(pos, 'side', 'LONG') or 'LONG'
            exit_side = "COVER" if side == "SHORT" else "SELL"
            display_name = sym.replace('.NS', '')

            exec_res = self.engine.execute_order(sym, exit_side, qty)
            if exec_res.get("success"):
                pnl = float(exec_res.get("realized_pnl", 0.0))
                chg_dict = exec_res.get("charges") if isinstance(exec_res.get("charges"), dict) else {}
                chg = float(chg_dict.get("total_charges", 0.0) if isinstance(chg_dict, dict) else 0.0)
                entry_p = float(exec_res.get("entry_price") or pos.average_price or exec_res.get("price", 0.0))
                exit_p = float(exec_res.get("price", 0.0))
                cost_basis = round(qty * (entry_p if entry_p > 0 else 1.0), 2)
                roi_pct = round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0

                trade_record = {
                    "id": exec_res.get("trade_id") or (int(datetime.now().timestamp() * 1000) + len(closed_trades)),
                    "symbol": sym,
                    "displayName": display_name,
                    "side": exit_side,
                    "quantity": qty,
                    "entry_price": entry_p,
                    "exit_price": exit_p,
                    "price": exit_p,
                    "total_value": round(qty * exit_p, 2),
                    "charges": chg,
                    "brokerage": round(float(chg_dict.get("brokerage", 0.0)), 2),
                    "stt": round(float(chg_dict.get("stt", 0.0)), 2),
                    "exchange_charges": round(float(chg_dict.get("exchange_charges", 0.0)), 2),
                    "sebi_charges": round(float(chg_dict.get("sebi_charges", 0.0)), 2),
                    "gst": round(float(chg_dict.get("gst", 0.0)), 2),
                    "stamp_duty": round(float(chg_dict.get("stamp_duty", 0.0)), 2),
                    "net_proceeds": round((qty * exit_p) - chg, 2),
                    "realized_pnl": pnl,
                    "roi_pct": roi_pct,
                    "is_win": pnl >= 0,
                    "action": exit_side,
                    "signal": "EOD_SQUAREOFF",
                    "reason": reason,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                self.record_session_trade(trade_record)
                closed_trades.append(trade_record)
                pnl_str = f"+₹{pnl:,.2f}" if pnl >= 0 else f"-₹{abs(pnl):,.2f}"
                self.add_log(f"🔔 [EOD AUTO SQUARE-OFF] Liquidated {qty} shares of {display_name} @ ₹{exit_p:.2f} ({reason}) | P&L: {pnl_str}", "SUCCESS" if pnl >= 0 else "WARNING")

        if closed_trades:
            self.persist_state()

        return closed_trades

    def evaluate_signals(self, simulation_event: str = None, target_symbol: str = None, client_traded_symbols: list = None) -> dict:
        """Runs the scanner across all Nifty 50 constituents (or single stock) and auto-trades"""
        import random
        self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 0. Enforce Operating Hours & 3:00 PM EOD Auto Square-Off:
        market_open = is_nse_market_open()
        alpha_open = is_alpha_trading_hours()
        square_off_time = is_intraday_squareoff_time()
        is_explicit_test = simulation_event in ["FORCE_BUY", "STOP_SHOCK", "FLASH_CRASH", "BULL_SURGE", "BULL_RUNNER", "TRAIL_PUSH", "WHIPSAW_CHOP"]

        if self.name == "ALPHA" and not is_explicit_test:
            # Check if any positions need to be auto-squared off at 3:00 PM cutoff or after hours
            eod_closed = []
            if square_off_time or not alpha_open:
                eod_closed = self.auto_square_off_all_positions(reason="3:00 PM EOD Auto Square-Off")
                if eod_closed:
                    self.add_log(f"🏁 [EOD COMPLETE] Auto-squared off all {len(eod_closed)} intraday positions at 3:00 PM cutoff. Desk 100% in cash.", "SUCCESS")

            if not alpha_open:
                if self.is_running:
                    self.is_running = False
                    self.session_status = "MARKET_CLOSED_PAUSED"
                    self.persist_state()
                    self.add_log("⏸️ [ALPHA SLEEP] 3:00 PM Cutoff Reached. ALPHA auto-squared off and paused until 09:15 AM tomorrow. Use NIGHTHAWK for after-market trading.", "WARNING")
                return {
                    "total_scanned": 0, "buys_found": 0, "sells_found": 0, "trades_executed": len(eod_closed),
                    "message": "Market Closed: ALPHA auto-squares off all positions by 03:00 PM IST. Re-opens at 09:15 AM tomorrow.",
                    "is_market_closed": True,
                    "eod_closed_trades": eod_closed,
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

        # Master Market Regime: NIFTY 50 trend check
        nifty_regime_info = {"regime": "BULLISH", "price": 0.0, "percent_change": 0.0, "ema_200": 0.0}
        try:
            nifty_data = MarketDataService.get_stock_analysis_data('^NSEI')
            if nifty_data and "error" not in nifty_data:
                np_price = nifty_data.get("price", 0.0)
                np_ema = nifty_data.get("ema_200", 0.0)
                np_pct = nifty_data.get("percent_change", 0.0)
                if np_price >= np_ema and np_pct > -1.2:
                    reg = "BULLISH"
                elif np_price < np_ema or np_pct <= -1.2:
                    reg = "BEARISH"
                else:
                    reg = "NEUTRAL"
                nifty_regime_info = {
                    "regime": reg,
                    "price": np_price,
                    "percent_change": np_pct,
                    "ema_200": np_ema
                }
                self.nifty_market_regime = nifty_regime_info
        except Exception:
            pass

        batch_label = None
        if self.scan_mode in ["TOP_200_TURNOVER", "VOLUME_SHOCKERS_200"]:
            from app.models.nse_universe import get_volume_shocker_stocks
            target_stocks = get_volume_shocker_stocks(200)
            batch_label = "⚡ Top 200 Volume Shockers & Institutional Momentum"
        elif self.scan_mode == "ALL_NSE_2000":
            from app.models.nse_universe import get_nse_universe_batch
            batch_stocks, curr_batch, total_batches = get_nse_universe_batch(self.batch_scan_index, batch_size=200)
            self.batch_scan_index = (self.batch_scan_index + 1) % total_batches
            target_stocks = batch_stocks
            batch_label = f"Smart Sieve Batch {curr_batch}/{total_batches} (50 Core + 150 Discovery Stocks)"
        elif self.scan_mode == "KRONOS_UNIVERSE" or self.strategy == "KRONOS_AI":
            target_stocks = NIFTY_50_DEFAULT
            batch_label = "NIFTY 50 Universe (50 Stocks)"
        elif self.scan_mode == "ALL_NIFTY_200":
            target_stocks = NIFTY_200_DEFAULT
        elif self.scan_mode == "ALL_NIFTY_50":
            target_stocks = NIFTY_50_DEFAULT
        else:
            target_stocks = [self.single_symbol]
        
        # Always include all held stocks so their SL/TP/Trailing stop is evaluated in every cycle!
        if held_map:
            target_stocks = list(dict.fromkeys(list(target_stocks) + list(held_map.keys())))
        
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
                # Set percent change cleanly from reference price without mutating and compounding
                drift_pct = round(((sim_price - orig_p) / orig_p) * 100, 2)
                stock_data["percent_change"] = max(-3.5, min(4.0, drift_pct))
            elif simulation_event == "RANDOM_WALK":
                # Micro fluctuation: random drift between -0.4% and +0.5%
                drift = random.uniform(-0.004, +0.005)
                walk_price = round(orig_p * (1.0 + drift), 2)
                stock_data["price"] = walk_price
                drift_pct = round(((walk_price - orig_p) / orig_p) * 100, 2)
                stock_data["percent_change"] = max(-3.5, min(4.0, drift_pct))

        scanner_results = []
        buys_found = 0
        sells_found = 0
        trades_executed = 0

        # Autonomous trade order execution strictly requires bot to be running (self.is_running is True or persisted)
        # Or explicit manual simulation triggers (FORCE_BUY, STOP_SHOCK)
        is_explicit_test = simulation_event in ["FORCE_BUY", "STOP_SHOCK"]
        persisted_running = _load_persistent_bot_state(self.state_filename).get("is_running", False)
        can_execute_trades = bool(self.is_running or persisted_running or is_explicit_test)

        # Check Daily Maximum Drawdown Circuit Breaker (Ceiling)
        if getattr(self, "survival_mode", True):
            try:
                realized_today = float(self.session_metrics.get("net_realized_pnl", 0.0))
                unrealized_today = 0.0
                for p in held_positions:
                    p_sym = p.symbol
                    p_data = stock_data_map.get(p_sym, {})
                    p_price = p_data.get("price", p.average_price)
                    if getattr(p, "side", "LONG") == "SHORT":
                        unrealized_today += (p.average_price - p_price) * p.quantity
                    else:
                        unrealized_today += (p_price - p.average_price) * p.quantity
                
                total_session_pnl = realized_today + unrealized_today
                base_capital = 1000000.0 if self.is_after_hours_forced else 50000.0
                session_pnl_pct = round((total_session_pnl / base_capital) * 100, 2)
                max_dd_limit = float(getattr(self, "daily_max_drawdown_pct", 1.5))

                if session_pnl_pct <= -abs(max_dd_limit):
                    if not getattr(self, "daily_circuit_tripped", False):
                        self.daily_circuit_tripped = True
                        self.add_log(f"🛡️ [SURVIVAL CIRCUIT BREAKER TRIPPED] Session loss hit {session_pnl_pct:+.2f}% (Limit: -{max_dd_limit}%). Halting new orders for today to preserve capital!", "ERROR")
                else:
                    self.daily_circuit_tripped = False

                # DAILY GREEN DAY TARGET LOCK (Goal & Vault):
                daily_target_pct = float(getattr(self, "daily_profit_target_pct", 2.0))
                lock_green = getattr(self, "lock_daily_green_target", True)
                if lock_green and session_pnl_pct >= daily_target_pct:
                    if not getattr(self, "daily_green_locked", False):
                        self.daily_green_locked = True
                        self.add_log(f"🏆 [DAILY GREEN DAY ACHIEVED] Target +{daily_target_pct:.1f}% hit! Desk P&L: ₹{total_session_pnl:+,.2f} (+{session_pnl_pct:.2f}%). Banking all profits and locking the day in GREEN!", "SUCCESS")
                        self.auto_square_off_all_positions(reason=f"Daily Green Target Hit (+{session_pnl_pct:.2f}%)")
                        self.persist_state()
            except Exception:
                pass

        if getattr(self, "daily_circuit_tripped", False) or getattr(self, "daily_green_locked", False):
            can_execute_trades = False

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
            # If max_positions is 0 or negative, allow unlimited holdings (No limit of holdings)
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
                            pnl = float(exec_res.get("realized_pnl", round((pos.average_price - price) * pos.quantity, 2)))
                            roi = round((pos.average_price - price) / pos.average_price * 100, 2) if pos.average_price > 0 else 0.0
                            chg_dict = exec_res.get("charges") if isinstance(exec_res.get("charges"), dict) else {}
                            chg_amt = float(chg_dict.get("total_charges", 0.0) if isinstance(chg_dict, dict) else 0.0)
                            cost_basis = round(pos.quantity * (pos.average_price if pos.average_price > 0 else 1.0), 2)
                            closed_trade_dict = {
                                "id": exec_res.get("trade_id") or int(datetime.now().timestamp() * 1000),
                                "symbol": symbol,
                                "displayName": display_name,
                                "side": "COVER",
                                "quantity": pos.quantity,
                                "entry_price": pos.average_price,
                                "exit_price": price,
                                "price": price,
                                "total_value": round(pos.quantity * price, 2),
                                "charges": chg_amt,
                                "brokerage": round(float(chg_dict.get("brokerage", 0.0)), 2),
                                "stt": round(float(chg_dict.get("stt", 0.0)), 2),
                                "exchange_charges": round(float(chg_dict.get("exchange_charges", 0.0)), 2),
                                "sebi_charges": round(float(chg_dict.get("sebi_charges", 0.0)), 2),
                                "gst": round(float(chg_dict.get("gst", 0.0)), 2),
                                "stamp_duty": round(float(chg_dict.get("stamp_duty", 0.0)), 2),
                                "net_proceeds": round((pos.quantity * price) - chg_amt, 2),
                                "realized_pnl": pnl,
                                "roi_pct": roi,
                                "is_win": pnl >= 0,
                                "action": "COVER",
                                "signal": "STRATEGY_EXIT",
                                "reason": eval_res.get("reason", "Bullish Reversal Cover Signal"),
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                            self.record_session_trade(closed_trade_dict)
                            eval_res["closed_trade"] = closed_trade_dict
                            chg_str = f" (Charges: ₹{chg_amt:.2f})" if chg_amt > 0 else ""
                            self.add_log(f"⚡ AUTO-COVERED {pos.quantity} shares of {display_name} @ ₹{price:.2f} [Short Exit]{chg_str} | P&L: ₹{pnl:+,.2f}", "SUCCESS" if pnl >= 0 else "WARNING")
                            del held_map[symbol]

                    # Otherwise, if we do not hold this stock and have a position slot, open a LONG trade
                    elif symbol not in held_map and has_position_slot:
                        if getattr(self, "daily_green_locked", False):
                            eval_res["action_executed"] = "LOCKED (Daily Green Day Target Secured)"
                            self.add_log(f"🏆 [GREEN DAY VAULT] BUY on {display_name} skipped: Daily profit target secured. Green day locked!", "INFO")
                        elif getattr(self, "daily_circuit_tripped", False):
                            eval_res["action_executed"] = "HALTED (Circuit Breaker Tripped)"
                            self.add_log(f"🛡️ [SURVIVAL SHIELD] BUY on {display_name} blocked: Daily Loss Circuit Breaker active", "WARNING")
                        else:
                            buying_power = self.allocation_per_trade * self.leverage
                            trade_qty = max(1, int(buying_power / price))
                            margin_needed = round((trade_qty * price) / self.leverage, 2)

                            alpha_margin_used = sum((p.quantity * p.average_price) / self.leverage for p in held_map.values()) if (self.name == "ALPHA" and not self.is_after_hours_forced) else 0.0
                            margin_cap_ok = True
                            if self.name == "ALPHA" and not self.is_after_hours_forced and (alpha_margin_used + margin_needed) > 25000.0:
                                margin_cap_ok = False
                                eval_res["action_executed"] = f"SKIPPED (50% Cap ₹25,000 Reached)"
                                self.add_log(f"🛡️ [50% CAPITAL CAP] Order on {display_name} skipped: Used ₹{alpha_margin_used:,.0f} + ₹{margin_needed:,.0f} > ₹25,000 (50% of ₹50k)", "INFO")

                            # Unlimited positions: never block new orders because of previous orders or margin pool depletion
                            if margin_cap_ok and (self.is_after_hours_forced or max_limit <= 0):
                                if available_cash < margin_needed:
                                    available_cash = margin_needed + 100000.0

                            if not margin_cap_ok:
                                pass
                            elif available_cash >= margin_needed:
                                exec_res = self.engine.execute_order(symbol, "BUY", trade_qty, execution_price=price, force_bypass_day_limit=True)
                                if exec_res.get("success"):
                                    if not self.is_after_hours_forced:
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
                                            strict_1pct_sl = round(price * 0.99, 2)
                                            pos_rec.stop_loss = max(raw_sl, strict_1pct_sl) if raw_sl else strict_1pct_sl
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
                                        if self.name == "ALPHA" and not self.is_after_hours_forced and pos_rec.stop_loss > 0:
                                            pos_rec.take_profit = round(price + (abs(price - pos_rec.stop_loss) * 2.0), 2)
                                        pos_rec.peak_price = price
                                        pos_rec.trailing_active = False
                                        try:
                                            db.session.commit()
                                        except Exception:
                                            db.session.rollback()

                                    chg_amt = exec_res.get("charges", {}).get("total_charges", 0.0) if isinstance(exec_res.get("charges"), dict) else 0.0
                                    strat_tag = eval_res.get("strat_tag") or ("SMC Liquidity Sweep" if self.strategy in ["SMC_LIQUIDITY_SWEEP", "PDL_SWEEP"] else ("1-2-3 Neckline Break" if self.strategy == "123_REVERSAL" else ("Dual SMA Cross" if self.strategy == "SMA_CROSSOVER" else ("RSI Extreme (20/80)" if self.strategy in ["RSI_20_80", "RSI_MOMENTUM", "RSI"] else self.strategy))))
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
                                    err_msg = exec_res.get("message") or "Order Rejected"
                                    eval_res["action_executed"] = f"ORDER REJECTED ({err_msg})"
                                    self.add_log(f"BUY order on {display_name} rejected: {err_msg}", "WARNING")
                            else:
                                eval_res["action_executed"] = f"SKIPPED (Margin ₹{margin_needed:,.0f} > Avail ₹{available_cash:,.0f})"
                                self.add_log(f"⚡ [MARGIN LIMIT] BUY on {display_name} skipped: Needed margin ₹{margin_needed:,.2f}, Available cash ₹{available_cash:,.2f}", "WARNING")
                    elif symbol not in held_map and not has_position_slot:
                        eval_res["action_executed"] = f"SKIPPED (Max Positions {max_limit} Reached)"
                        self.add_log(f"⚡ [MAX POSITIONS] BUY on {display_name} skipped: Max slots ({max_limit}) reached", "WARNING")
                    elif symbol in held_map:
                        eval_res["action_executed"] = f"ACTIVE ({getattr(held_map[symbol], 'side', 'LONG')})"
                else:
                    eval_res["action_executed"] = "LOCKED (Daily Green Day Target Secured)" if getattr(self, "daily_green_locked", False) else "OFFLINE (Bot Stopped)"

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
                            pnl = float(exec_res.get("realized_pnl", round((price - pos.average_price) * pos.quantity, 2)))
                            roi = round((price - pos.average_price) / pos.average_price * 100, 2) if pos.average_price > 0 else 0.0
                            chg_dict = exec_res.get("charges") if isinstance(exec_res.get("charges"), dict) else {}
                            chg_amt = float(chg_dict.get("total_charges", 0.0) if isinstance(chg_dict, dict) else 0.0)
                            cost_basis = round(pos.quantity * (pos.average_price if pos.average_price > 0 else 1.0), 2)
                            closed_trade_dict = {
                                "id": exec_res.get("trade_id") or int(datetime.now().timestamp() * 1000),
                                "symbol": symbol,
                                "displayName": display_name,
                                "side": "SELL",
                                "quantity": pos.quantity,
                                "entry_price": pos.average_price,
                                "exit_price": price,
                                "price": price,
                                "total_value": round(pos.quantity * price, 2),
                                "charges": chg_amt,
                                "brokerage": round(float(chg_dict.get("brokerage", 0.0)), 2),
                                "stt": round(float(chg_dict.get("stt", 0.0)), 2),
                                "exchange_charges": round(float(chg_dict.get("exchange_charges", 0.0)), 2),
                                "sebi_charges": round(float(chg_dict.get("sebi_charges", 0.0)), 2),
                                "gst": round(float(chg_dict.get("gst", 0.0)), 2),
                                "stamp_duty": round(float(chg_dict.get("stamp_duty", 0.0)), 2),
                                "net_proceeds": round((pos.quantity * price) - chg_amt, 2),
                                "realized_pnl": pnl,
                                "roi_pct": roi,
                                "is_win": pnl >= 0,
                                "action": "SELL",
                                "signal": "STRATEGY_EXIT",
                                "reason": eval_res.get("reason", "Strategy Bearish Exit Signal"),
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                            self.record_session_trade(closed_trade_dict)
                            eval_res["closed_trade"] = closed_trade_dict

                            chg_str = f" (Charges: ₹{chg_amt:.2f})" if chg_amt > 0 else ""
                            self.add_log(f"⚡ AUTO-SOLD {pos.quantity} shares of {display_name} @ ₹{price:.2f} [Long Exit]{chg_str} | P&L: ₹{pnl:+,.2f}", "SUCCESS" if pnl >= 0 else "WARNING")
                            del held_map[symbol]

                    # Case B: If we do NOT hold this stock, enter a SHORT position!
                    elif symbol not in held_map and has_position_slot:
                        if getattr(self, "daily_green_locked", False):
                            eval_res["action_executed"] = "LOCKED (Daily Green Day Target Secured)"
                            self.add_log(f"🏆 [GREEN DAY VAULT] SHORT on {display_name} skipped: Daily profit target secured. Green day locked!", "INFO")
                        elif getattr(self, "daily_circuit_tripped", False):
                            eval_res["action_executed"] = "HALTED (Circuit Breaker Tripped)"
                            self.add_log(f"🛡️ [SURVIVAL SHIELD] SHORT on {display_name} blocked: Daily Loss Circuit Breaker active", "WARNING")
                        else:
                            buying_power = self.allocation_per_trade * self.leverage
                            trade_qty = max(1, int(buying_power / price))
                            margin_needed = round((trade_qty * price) / self.leverage, 2)

                            alpha_margin_used = sum((p.quantity * p.average_price) / self.leverage for p in held_map.values()) if (self.name == "ALPHA" and not self.is_after_hours_forced) else 0.0
                            margin_cap_ok = True
                            if self.name == "ALPHA" and not self.is_after_hours_forced and (alpha_margin_used + margin_needed) > 25000.0:
                                margin_cap_ok = False
                                eval_res["action_executed"] = f"SKIPPED (50% Cap ₹25,000 Reached)"
                                self.add_log(f"🛡️ [50% CAPITAL CAP] Order on {display_name} skipped: Used ₹{alpha_margin_used:,.0f} + ₹{margin_needed:,.0f} > ₹25,000 (50% of ₹50k)", "INFO")

                            # Unlimited positions: never block new orders because of previous orders or margin pool depletion
                            if margin_cap_ok and (self.is_after_hours_forced or max_limit <= 0):
                                if available_cash < margin_needed:
                                    available_cash = margin_needed + 100000.0

                            if not margin_cap_ok:
                                pass
                            elif available_cash >= margin_needed:
                                exec_res = self.engine.execute_order(symbol, "SHORT", trade_qty, execution_price=price, force_bypass_day_limit=True)
                                if exec_res.get("success"):
                                    if not self.is_after_hours_forced:
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
                                            strict_1pct_sl = round(price * 1.01, 2)
                                            pos_rec.stop_loss = min(raw_sl, strict_1pct_sl) if raw_sl else strict_1pct_sl
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
                                        if self.name == "ALPHA" and not self.is_after_hours_forced and pos_rec.stop_loss > 0:
                                            pos_rec.take_profit = round(price - (abs(pos_rec.stop_loss - price) * 2.0), 2)
                                        pos_rec.peak_price = price
                                        pos_rec.trailing_active = False
                                        try:
                                            db.session.commit()
                                        except Exception:
                                            db.session.rollback()

                                    chg_amt = exec_res.get("charges", {}).get("total_charges", 0.0) if isinstance(exec_res.get("charges"), dict) else 0.0
                                    strat_tag = eval_res.get("strat_tag") or ("SMC Liquidity Sweep" if self.strategy in ["SMC_LIQUIDITY_SWEEP", "PDL_SWEEP"] else ("1-2-3 Neckline Breakdown" if self.strategy == "123_REVERSAL" else ("Dual SMA Death Cross" if self.strategy == "SMA_CROSSOVER" else "RSI Overbought")))
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
                                    err_msg = exec_res.get("message") or "Order Rejected"
                                    eval_res["action_executed"] = f"ORDER REJECTED ({err_msg})"
                                    self.add_log(f"SHORT order on {display_name} rejected: {err_msg}", "WARNING")
                            else:
                                eval_res["action_executed"] = f"SKIPPED (Margin ₹{margin_needed:,.0f} > Avail ₹{available_cash:,.0f})"
                                self.add_log(f"⚡ [MARGIN LIMIT] SHORT on {display_name} skipped: Needed margin ₹{margin_needed:,.2f}, Available cash ₹{available_cash:,.2f}", "WARNING")
                    elif symbol not in held_map and not has_position_slot:
                        eval_res["action_executed"] = f"SKIPPED (Max Positions {max_limit} Reached)"
                        self.add_log(f"⚡ [MAX POSITIONS] SHORT on {display_name} skipped: Max slots ({max_limit}) reached", "WARNING")
                    elif symbol in held_map:
                        eval_res["action_executed"] = f"ACTIVE ({getattr(held_map[symbol], 'side', 'SHORT')})"
                else:
                    eval_res["action_executed"] = "LOCKED (Daily Green Day Target Secured)" if getattr(self, "daily_green_locked", False) else "OFFLINE (Bot Stopped)"

            if symbol in already_traded_today and not eval_res.get("action_executed") and symbol not in held_map:
                eval_res["action_executed"] = "TRADED TODAY"

            scanner_results.append(eval_res)

        # Sort results: BUY signals first, then SELL, then highest RVOL volume shockers & day changes
        scanner_results.sort(key=lambda x: (
            0 if x["signal"] == "BUY" else (1 if x["signal"] in ("SELL", "STOP_LOSS", "TAKE_PROFIT") else 2), 
            -float(x.get("vol_ratio") or 1.0), 
            -abs(x.get("change_pct") or 0)
        ))

        sweep_closed_trades = [r["closed_trade"] for r in scanner_results if r.get("closed_trade")]

        self.last_scan_summary = {
            "total_scanned": len(scanner_results),
            "scan_mode": self.scan_mode,
            "batch_label": batch_label or ("Daily Top 200 Turnover Basket" if self.scan_mode == "TOP_200_TURNOVER" else None),
            "total_universe_count": 200 if self.scan_mode in ["TOP_200_TURNOVER", "ALL_NIFTY_200"] else (2391 if self.scan_mode == "ALL_NSE_2000" else (50 if self.scan_mode == "ALL_NIFTY_50" else 1)),
            "buys_found": buys_found,
            "sells_found": sells_found,
            "trades_executed": len(self.session_trades),
            "recent_sweep_trades": trades_executed,
            "active_positions_count": len(held_map),
            "max_positions_limit": self.max_positions,
            "nifty_market_regime": getattr(self, "nifty_market_regime", nifty_regime_info),
            "scanner_results": scanner_results,
            "closed_trades": sweep_closed_trades
        }

        sweep_tag = "🌙 AFTER-MARKET SWEEP (Simulated)" if after_hours else "☀️ LIVE NSE MARKET SWEEP (Real Quotes)"
        batch_log = f" [{batch_label}]" if batch_label else ""
        self.add_log(f"{sweep_tag}{batch_log}: Scanned {len(scanner_results)} stocks | 🟢 {buys_found} BUY signals | 🔴 {sells_found} SELL signals | {trades_executed} Trades executed", "INFO")

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
