"""
SWING TRADING BOT ENGINE (BOT TITAN)
=====================================
Multi-Day Delivery (CNC) Automated Trading Desk with:
- 4-in-1 Strategy Suite: VORTEX, ANCHOR, TITAN, SURGE & CONFLUENCE
- Multi-Day Position Tracking with "Days Held" Counter
- Trailing Stop & Breakeven Shield
- Automated Zerodha Kite 3-Leg GTT (OCO) Basket Generator
- Strategy Comparison Leaderboard
"""

import threading
import time
import os
import json
import concurrent.futures
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

from app import db
from app.models.trading import (
    SwingAccount, SwingPosition, SwingTradeHistory,
    NIFTY_50_DEFAULT, NIFTY_200_DEFAULT
)
from app.services.market_data import MarketDataService
from app.services.swing_strategies import UnifiedSwingScanner

is_serverless = any(k in os.environ for k in ('VERCEL', 'VERCEL_ENV', 'AWS_LAMBDA_FUNCTION_NAME'))
SWING_STATE_FILE = os.path.join("/tmp", "swing_bot_state.json") if is_serverless else "swing_bot_state.json"

class SwingPaperTradingEngine:
    @staticmethod
    def get_or_create_account(initial_cash: float = 1000000.0) -> SwingAccount:
        acc = SwingAccount.query.first()
        if not acc:
            acc = SwingAccount(cash_balance=initial_cash)
            db.session.add(acc)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                acc = SwingAccount.query.first()
        elif acc.cash_balance < 300000.0:
            acc.cash_balance = 1000000.0
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        return acc

    @classmethod
    def get_portfolio_summary(cls) -> Dict[str, Any]:
        acc = cls.get_or_create_account()
        positions = SwingPosition.query.filter(SwingPosition.quantity > 0).all()

        total_market_val = 0.0
        total_unrealized_pnl = 0.0
        pos_list = []

        now = datetime.now(timezone.utc)
        today_ist_str = (now + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")

        for p in positions:
            quote = MarketDataService.get_quote(p.symbol)
            curr_price = quote.get("price", p.average_price) if "error" not in quote else p.average_price
            market_val = round(p.quantity * curr_price, 2)
            cost_basis = round(p.quantity * p.average_price, 2)
            pnl = round(market_val - cost_basis, 2)
            pnl_pct = round((pnl / cost_basis * 100), 2) if cost_basis > 0 else 0.0

            # Calculate days held & IST entry date
            entry_dt = p.entry_date
            if entry_dt and entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
            days_held = max(1, (now - (entry_dt or now)).days)
            entry_ist_str = (entry_dt + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d") if entry_dt else ""
            added_today = bool(entry_ist_str and entry_ist_str == today_ist_str)

            total_market_val += market_val
            total_unrealized_pnl += pnl

            pos_list.append({
                "id": p.id,
                "symbol": p.symbol,
                "displayName": p.symbol.replace('.NS', '').replace('.BO', ''),
                "side": p.side,
                "quantity": p.quantity,
                "average_price": p.average_price,
                "current_price": curr_price,
                "market_value": market_val,
                "unrealized_pnl": pnl,
                "unrealized_pnl_pct": pnl_pct,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "trailing_active": p.trailing_active,
                "strategy": p.strategy,
                "confluence_score": p.confluence_score,
                "days_held": days_held,
                "added_today": added_today,
                "entry_date_ist": entry_ist_str,
                "entry_date": p.entry_date.strftime("%Y-%m-%d %H:%M") if p.entry_date else ""
            })

        cash = acc.cash_balance if acc else 100000.0
        total_equity = round(cash + total_market_val, 2)

        return {
            "cash_balance": cash,
            "market_value": round(total_market_val, 2),
            "total_equity": total_equity,
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "positions_count": len(pos_list),
            "positions": pos_list
        }

    @classmethod
    def execute_order(cls, symbol: str, side: str, quantity: float, execution_price: float, strategy: str = "VORTEX", sl: float = 0.0, tp: float = 0.0, confluence: int = 85) -> Dict[str, Any]:
        acc = cls.get_or_create_account()
        turnover = round(quantity * execution_price, 2)
        side = side.upper()

        # Delivery charges (STT 0.1%, Brokerage ₹20 or 0.1%, etc.)
        charges = round(max(20.0, turnover * 0.001) + (turnover * 0.001), 2)

        if side == "BUY":
            if acc.cash_balance < (turnover + charges):
                # Auto-expand paper cash balance so Titan can execute every setup without artificial block
                acc.cash_balance = round(acc.cash_balance + max(500000.0, (turnover + charges) * 10), 2)
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            acc.cash_balance = round(acc.cash_balance - (turnover + charges), 2)

            pos = SwingPosition.query.filter_by(symbol=symbol).first()
            if not pos:
                pos = SwingPosition(
                    symbol=symbol,
                    side="LONG",
                    quantity=quantity,
                    average_price=execution_price,
                    peak_price=execution_price,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy=strategy,
                    confluence_score=confluence,
                    entry_date=datetime.now(timezone.utc)
                )
                db.session.add(pos)
            else:
                total_qty = pos.quantity + quantity
                pos.average_price = round(((pos.quantity * pos.average_price) + turnover) / total_qty, 2)
                pos.quantity = total_qty
                pos.stop_loss = sl
                pos.take_profit = tp

            db.session.commit()
            return {"success": True, "side": "BUY", "quantity": quantity, "price": execution_price, "charges": charges}

        elif side in ("SELL", "EXIT"):
            pos = SwingPosition.query.filter_by(symbol=symbol).first()
            if not pos or pos.quantity <= 0:
                return {"success": False, "message": "No open position held in this stock"}

            qty_to_sell = min(quantity, pos.quantity)
            cost_basis = round(qty_to_sell * pos.average_price, 2)
            proceeds = round(qty_to_sell * execution_price, 2)
            realized_pnl = round(proceeds - cost_basis - charges, 2)
            roi_pct = round((realized_pnl / cost_basis * 100), 2) if cost_basis > 0 else 0.0

            acc.cash_balance = round(acc.cash_balance + proceeds - charges, 2)

            # Days held calculation
            now = datetime.now(timezone.utc)
            entry_dt = pos.entry_date
            if entry_dt and entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
            days_held = max(1, (now - (entry_dt or now)).days)

            trade = SwingTradeHistory(
                symbol=symbol,
                side="SELL",
                quantity=qty_to_sell,
                price=execution_price,
                entry_price=pos.average_price,
                total=proceeds,
                realized_pnl=realized_pnl,
                roi_pct=roi_pct,
                charges=charges,
                brokerage=20.0,
                stt=round(turnover * 0.001, 2),
                strategy=pos.strategy or strategy,
                days_held=days_held,
                exit_reason="Take-Profit Target" if realized_pnl >= 0 else "Stop-Loss Cut"
            )
            db.session.add(trade)

            pos.quantity -= qty_to_sell
            if pos.quantity <= 0:
                db.session.delete(pos)

            db.session.commit()
            return {
                "success": True,
                "side": "SELL",
                "quantity": qty_to_sell,
                "price": execution_price,
                "realized_pnl": realized_pnl,
                "roi_pct": roi_pct,
                "charges": charges,
                "days_held": days_held,
                "strategy": trade.strategy
            }


class SwingTradingBot:
    def __init__(self):
        self.name = "TITAN_SWING"
        self.is_running = False
        self.selected_strategy = "ALL" # ALL, VORTEX, ANCHOR, TITAN, SURGE
        self.allocation_per_trade = 15000.0 # Min ₹15,000 per swing position
        self.max_positions = 10 # Maximum 10 concurrent swing positions
        self.scan_mode = "ALL_NIFTY_200"
        self.survival_mode = True
        self.survival_breakeven_pct = 2.0  # +2.0% locks stop-loss to entry + fees
        self.enable_partial_profits = True # Bank 50% cash at +5.0%
        self.portfolio_drawdown_limit_pct = 3.0 # -3.0% maximum portfolio drawdown halt
        self.circuit_tripped = False
        self.last_scan_time = None
        self.last_scan_summary = {
            "total_scanned": 0,
            "buys_found": 0,
            "scanner_results": []
        }
        self.logs = []
        self.lock = threading.Lock()
        self.ai_signals = {}
        self.ai_signals_metadata = {}
        self.use_ai_filter = True
        self.strict_ai_mode = False
        self._load_state()

    def _load_state(self):
        if os.path.exists(SWING_STATE_FILE):
            try:
                with open(SWING_STATE_FILE, 'r') as f:
                    data = json.load(f)
                    self.is_running = data.get("is_running", False)
                    self.selected_strategy = data.get("selected_strategy", "ALL")
                    self.allocation_per_trade = max(15000.0, float(data.get("allocation_per_trade", 15000.0)))
                    raw_max = int(data.get("max_positions", 10))
                    self.max_positions = 10 if (raw_max <= 0 or raw_max > 10) else raw_max
                    self.ai_signals = data.get("ai_signals", {})
                    self.ai_signals_metadata = data.get("ai_signals_metadata", {})
                    self.use_ai_filter = data.get("use_ai_filter", True)
                    self.strict_ai_mode = bool(data.get("strict_ai_mode", False))
                    self.survival_mode = bool(data.get("survival_mode", True))
                    self.survival_breakeven_pct = float(data.get("survival_breakeven_pct", 2.0))
                    self.enable_partial_profits = bool(data.get("enable_partial_profits", True))
                    self.portfolio_drawdown_limit_pct = float(data.get("portfolio_drawdown_limit_pct", 3.0))
            except Exception:
                pass
        if not self.ai_signals:
            try:
                from app.data.default_ai_signals import DEFAULT_AI_PAYLOAD
                self.ai_signals = DEFAULT_AI_PAYLOAD.get('signals', {})
                self.ai_signals_metadata = DEFAULT_AI_PAYLOAD.get('metadata', {})
            except Exception:
                pass

    def persist_state(self):
        try:
            with open(SWING_STATE_FILE, 'w') as f:
                json.dump({
                    "is_running": self.is_running,
                    "selected_strategy": self.selected_strategy,
                    "allocation_per_trade": self.allocation_per_trade,
                    "max_positions": self.max_positions,
                    "use_ai_filter": getattr(self, "use_ai_filter", True),
                    "strict_ai_mode": getattr(self, "strict_ai_mode", False),
            "survival_mode": getattr(self, "survival_mode", True),
            "survival_breakeven_pct": getattr(self, "survival_breakeven_pct", 2.0),
            "enable_partial_profits": getattr(self, "enable_partial_profits", True),
            "portfolio_drawdown_limit_pct": getattr(self, "portfolio_drawdown_limit_pct", 3.0),
            "circuit_tripped": getattr(self, "circuit_tripped", False),
                    "survival_mode": getattr(self, "survival_mode", True),
                    "survival_breakeven_pct": getattr(self, "survival_breakeven_pct", 2.0),
                    "enable_partial_profits": getattr(self, "enable_partial_profits", True),
                    "portfolio_drawdown_limit_pct": getattr(self, "portfolio_drawdown_limit_pct", 3.0),
                    "circuit_tripped": getattr(self, "circuit_tripped", False),
                    "ai_signals": getattr(self, "ai_signals", {}),
                    "ai_signals_metadata": getattr(self, "ai_signals_metadata", {})
                }, f, indent=2)
        except Exception:
            pass

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
            "updated_at": "Never",
            "model": "None",
            "filter_enabled": False
        }

    def start(self, config: dict = None) -> Dict[str, Any]:
        if config:
            if "selected_strategy" in config:
                self.selected_strategy = str(config["selected_strategy"]).upper()
            if "allocation_per_trade" in config:
                self.allocation_per_trade = max(15000.0, float(config["allocation_per_trade"]))
            if "max_positions" in config:
                raw_max = int(config["max_positions"])
                self.max_positions = 10 if (raw_max <= 0 or raw_max > 10) else raw_max
            if "scan_mode" in config:
                self.scan_mode = str(config["scan_mode"])
            if "strict_ai_mode" in config:
                self.strict_ai_mode = bool(config["strict_ai_mode"])
            if "survival_mode" in config:
                self.survival_mode = bool(config["survival_mode"])
            if "survival_breakeven_pct" in config:
                self.survival_breakeven_pct = float(config["survival_breakeven_pct"])
            if "enable_partial_profits" in config:
                self.enable_partial_profits = bool(config["enable_partial_profits"])
            if "portfolio_drawdown_limit_pct" in config:
                self.portfolio_drawdown_limit_pct = float(config["portfolio_drawdown_limit_pct"])

        self.is_running = True
        self.persist_state()
        slots_str = "UNLIMITED (No Limiter — Takes All Setups)" if (not self.max_positions or self.max_positions <= 0) else f"{self.max_positions} Slots"
        self.add_log(f"🚀 TITAN Swing Bot Desk ACTIVATED - Multi-Strategy Suite ({self.selected_strategy}) Online | Capital/Trade: ₹{self.allocation_per_trade:,.0f} | Position Limit: {slots_str}", "SUCCESS")
        return {"success": True, "is_running": True, "status": self.get_status()}

    def stop(self) -> Dict[str, Any]:
        self.is_running = False
        self.persist_state()
        self.add_log("⏸️ TITAN Swing Bot Desk PAUSED - Open positions preserved safely", "WARNING")
        return {"success": True, "is_running": False, "status": self.get_status()}

    def add_log(self, message: str, level: str = "INFO"):
        with self.lock:
            self.logs.insert(0, {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "message": message
            })
            if len(self.logs) > 60:
                self.logs.pop()

    def get_strategy_scorecard(self) -> Dict[str, Any]:
        """Calculates win rate, total P&L and average holding days for each of the 4 swing strategies"""
        trades = SwingTradeHistory.query.all()
        scorecard = {
            "RSI_20_80": {"trades": 0, "wins": 0, "pnl": 0.0, "days": []},
            "VWAP_EMA_RSI": {"trades": 0, "wins": 0, "pnl": 0.0, "days": []},
            "DMA_200": {"trades": 0, "wins": 0, "pnl": 0.0, "days": []},
            "VOLUME_SHOCKER": {"trades": 0, "wins": 0, "pnl": 0.0, "days": []},
            "VORTEX": {"trades": 0, "wins": 0, "pnl": 0.0, "days": []},
            "ANCHOR": {"trades": 0, "wins": 0, "pnl": 0.0, "days": []},
            "TITAN": {"trades": 0, "wins": 0, "pnl": 0.0, "days": []},
            "SURGE": {"trades": 0, "wins": 0, "pnl": 0.0, "days": []},
            "CONFLUENCE": {"trades": 0, "wins": 0, "pnl": 0.0, "days": []}
        }

        for t in trades:
            s = t.strategy or "VORTEX"
            key = "CONFLUENCE" if "CONFLUENCE" in s else (s if s in scorecard else "VORTEX")
            entry = scorecard[key]
            entry["trades"] += 1
            entry["pnl"] = round(entry["pnl"] + (t.realized_pnl or 0.0), 2)
            if (t.realized_pnl or 0.0) >= 0:
                entry["wins"] += 1
            if t.days_held:
                entry["days"].append(t.days_held)

        summary = {}
        for k, v in scorecard.items():
            tr = v["trades"]
            win_rate = round((v["wins"] / tr * 100), 1) if tr > 0 else 0.0
            avg_days = round(float(sum(v["days"]) / len(v["days"])), 1) if v["days"] else 0.0
            summary[k] = {
                "trades": tr,
                "wins": v["wins"],
                "win_rate": win_rate,
                "total_pnl": v["pnl"],
                "avg_days": avg_days
            }

        return summary

    def scan_and_evaluate(self) -> Dict[str, Any]:
        """Scans the swing universe, manages open position trailing stops & exits, and discovers setups"""
        now = datetime.now(timezone.utc)
        self.last_scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Manage Active Positions (Trailing Stops & Targets)
        active_positions = SwingPosition.query.filter(SwingPosition.quantity > 0).all()
        held_symbols = {p.symbol for p in active_positions}

        for pos in active_positions:
            q = MarketDataService.get_quote(pos.symbol)
            if "error" in q:
                continue
            curr_p = q.get("price", pos.average_price)
            pnl_pct = ((curr_p - pos.average_price) / pos.average_price) * 100

            # Breakeven Shield (+2.0% in Survival Mode, +4.0% normal)
            be_threshold = self.survival_breakeven_pct if getattr(self, "survival_mode", True) else 4.0
            if pnl_pct >= be_threshold and not pos.trailing_active:
                be_price = round(pos.average_price * 1.002, 2)
                pos.stop_loss = max(pos.stop_loss, be_price)
                pos.trailing_active = True
                db.session.commit()
                self.add_log(f"🛡️ [SWING BREAKEVEN SHIELD] {pos.symbol.replace('.NS','')}: +{pnl_pct:.1f}% reached. Stop-Loss raised to Breakeven+Fees (₹{be_price:.2f}). 100% Risk-Free!", "SUCCESS")

            # Full Profit Book on ANY day when profit >= 5.0%
            if pnl_pct >= 5.0:
                self.add_log(f"💰 [SWING 5%+ PROFIT BOOKED] {pos.symbol.replace('.NS','')}: +{pnl_pct:.2f}% reached! Booking full profit @ ₹{curr_p:.2f}.", "SUCCESS")
                SwingPaperTradingEngine.execute_order(pos.symbol, "SELL", pos.quantity, curr_p, strategy=pos.strategy)
                held_symbols.discard(pos.symbol)
                continue

            # Partial Profit Banking (Bank 50% profit at +3.5% & lock +1.5% SL)
            if getattr(self, "enable_partial_profits", True) and pnl_pct >= 3.5 and pos.quantity >= 2 and not getattr(pos, 'partial_taken', False):
                half_qty = int(pos.quantity // 2)
                if half_qty > 0:
                    self.add_log(f"💰 [SWING PARTIAL PROFIT] {pos.symbol.replace('.NS','')}: +{pnl_pct:.1f}% reached! Selling 50% ({half_qty} shares) & locking +1.5% SL.", "SUCCESS")
                    SwingPaperTradingEngine.execute_order(pos.symbol, "SELL", half_qty, curr_p, strategy=pos.strategy)
                    pos.partial_taken = True
                    pos.stop_loss = max(pos.stop_loss, round(pos.average_price * 1.015, 2))
                    db.session.commit()

            # Check Take-Profit
            if pos.take_profit > 0 and curr_p >= pos.take_profit:
                self.add_log(f"🎯 [SWING TARGET HIT] {pos.symbol.replace('.NS','')}: Target ₹{pos.take_profit:.2f} reached (+{pnl_pct:.1f}%). Closing trade!", "SUCCESS")
                SwingPaperTradingEngine.execute_order(pos.symbol, "SELL", pos.quantity, curr_p, strategy=pos.strategy)
                held_symbols.discard(pos.symbol)
                continue

            # Check Stop-Loss
            if pos.stop_loss > 0 and curr_p <= pos.stop_loss:
                self.add_log(f"🛑 [SWING STOP-LOSS CUT] {pos.symbol.replace('.NS','')}: Price ₹{curr_p:.2f} breached SL ₹{pos.stop_loss:.2f} ({pnl_pct:.1f}%). Liquidating!", "ERROR")
                SwingPaperTradingEngine.execute_order(pos.symbol, "SELL", pos.quantity, curr_p, strategy=pos.strategy)
                held_symbols.discard(pos.symbol)
                continue

            # Check RSI Overbought Exit (>= 80.0)
            analysis_data = MarketDataService.get_stock_analysis_data(pos.symbol)
            if "error" not in analysis_data:
                c_list = analysis_data.get("closes", [])
                p_rsi = SwingStrategyBase.calc_rsi(c_list, 14) if len(c_list) >= 15 else None
                if p_rsi and p_rsi >= 80.0:
                    self.add_log(f"⚡ [SWING RSI 80 EXIT] {pos.symbol.replace('.NS','')}: RSI reached {p_rsi:.1f} (>= 80.0 Overbought). Exiting swing position at market (+{pnl_pct:.1f}%)!", "SUCCESS")
                    SwingPaperTradingEngine.execute_order(pos.symbol, "SELL", pos.quantity, curr_p, strategy=pos.strategy)
                    held_symbols.discard(pos.symbol)
                    continue

            # 2-DAY GREEN SWING EXIT RULE:
            # If position has been held for 2 or more days and is in GREEN (>= +0.5%), exit and lock in profit!
            entry_dt = pos.entry_date
            if entry_dt and entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
            pos_days = max(1, (now - (entry_dt or now)).days) if entry_dt else 1
            if pos_days >= 2 and pnl_pct >= 0.5:
                self.add_log(f"🎯 [2-DAY GREEN SWING EXIT] {pos.symbol.replace('.NS','')}: Day {pos_days} reached in GREEN (+{pnl_pct:.2f}%). Banking profit & recycling capital!", "SUCCESS")
                SwingPaperTradingEngine.execute_order(pos.symbol, "SELL", pos.quantity, curr_p, strategy=pos.strategy)
                held_symbols.discard(pos.symbol)
                continue

        # 2. Parallel Data Fetch for Target Stocks
        if self.scan_mode == "VOLUME_SHOCKERS_200":
            from app.models.nse_universe import get_top_turnover_stocks
            target_stocks = get_top_turnover_stocks(200)
        elif self.scan_mode == "ALL_NIFTY_200":
            target_stocks = NIFTY_200_DEFAULT
        else:
            target_stocks = NIFTY_50_DEFAULT
        stock_data_map = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
            future_to_stock = {
                executor.submit(MarketDataService.get_stock_analysis_data, sym): sym 
                for sym in target_stocks
            }
            try:
                for future in concurrent.futures.as_completed(future_to_stock, timeout=10.0):
                    sym = future_to_stock[future]
                    try:
                        data = future.result()
                        if "error" not in data:
                            stock_data_map[sym] = data
                    except Exception:
                        pass
            except concurrent.futures.TimeoutError:
                pass

        # 3. Evaluate Stocks with Unified Multi-Strategy Scanner
        scanner_results = []
        buys_found = 0

        for sym in target_stocks:
            data = stock_data_map.get(sym)
            if not data:
                continue

            scan_res = UnifiedSwingScanner.scan_stock(sym, data, selected_strategy=self.selected_strategy)

            if scan_res.get("signal") == "BUY":
                buys_found += 1
            scanner_results.append(scan_res)

        # Sort: BUY signals with highest Volume Shock & Confluence Score first (e.g. 8.2x, 4.5x RVOL shockers first!)
        scanner_results.sort(key=lambda x: (
            0 if x["signal"] == "BUY" else 1, 
            -float(x.get("vol_ratio") or 1.0), 
            -x.get("confluence_score", 0)
        ))

        # Check Portfolio Drawdown Circuit Breaker
        summary = SwingPaperTradingEngine.get_portfolio_summary()
        current_equity = summary.get("total_equity", 1000000.0)
        base_swing_equity = 1000000.0
        drawdown_pct = round(((current_equity - base_swing_equity) / base_swing_equity) * 100, 2)
        if getattr(self, "survival_mode", True) and drawdown_pct <= -abs(getattr(self, "portfolio_drawdown_limit_pct", 3.0)):
            self.circuit_tripped = True
            self.add_log(f"🛡️ [SWING CIRCUIT BREAKER TRIPPED] Portfolio drawdown reached {drawdown_pct:+.2f}% (Limit: -{self.portfolio_drawdown_limit_pct}%). Halting new swing buys to protect capital!", "ERROR")
        else:
            self.circuit_tripped = False

        # 4. Auto-Execute Top-Scoring Setup (Max 10 positions total & strictly 1 new stock added per IST day)
        now_ist = now + timedelta(hours=5, minutes=30)
        today_ist_str = now_ist.strftime("%Y-%m-%d")
        start_of_ist_day_utc = datetime(now_ist.year, now_ist.month, now_ist.day, 0, 0, 0) - timedelta(hours=5, minutes=30)
        already_added_today = (getattr(self, "_last_entry_ist_date", None) == today_ist_str)
        if not already_added_today:
            for hp in active_positions:
                hp_dt = hp.entry_date
                if hp_dt and hp_dt.tzinfo is None:
                    hp_dt = hp_dt.replace(tzinfo=timezone.utc)
                if hp_dt and (hp_dt + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d") == today_ist_str:
                    already_added_today = True
                    break
        if not already_added_today:
            today_buy_trade = SwingTradeHistory.query.filter(
                SwingTradeHistory.side == "BUY",
                SwingTradeHistory.timestamp >= start_of_ist_day_utc
            ).first()
            if today_buy_trade:
                already_added_today = True

        if self.is_running and not getattr(self, "circuit_tripped", False):
            if already_added_today:
                self.add_log(f"⏸️ [1-STOCK/DAY LIMIT] 1 stock already added today ({today_ist_str}). Holding current positions.", "INFO")
            else:
                for scan_res in scanner_results:
                    if scan_res.get("signal") != "BUY":
                        continue
                    # Position limiter check: hard cap at maximum 10 concurrent positions
                    effective_max = 10 if (not self.max_positions or self.max_positions <= 0 or self.max_positions > 10) else self.max_positions
                    if len(held_symbols) >= effective_max:
                        self.add_log(f"⏸️ Max position limit ({effective_max}) reached. Skipping remaining setups.", "INFO")
                        break

                    sym = scan_res.get("symbol")
                    if sym in held_symbols:
                        continue

                    p = scan_res.get("price", 0.0)
                    if p <= 0:
                        continue

                    # Trade sizing: enforce at least ₹15,000 per trade
                    trade_alloc = max(15000.0, float(self.allocation_per_trade or 15000.0))
                    qty = max(1, int(trade_alloc / p))
                    needed = round(qty * p, 2)

                    exec_res = SwingPaperTradingEngine.execute_order(
                        sym, "BUY", qty, p,
                        strategy=scan_res.get("strategy_key", "VORTEX"),
                        sl=scan_res.get("stop_loss", 0.0),
                        tp=scan_res.get("target_price", 0.0),
                        confluence=scan_res.get("confluence_score", 85)
                    )
                    if exec_res.get("success"):
                        held_symbols.add(sym)
                        self._last_entry_ist_date = today_ist_str
                        scan_res["action_executed"] = f"BOUGHT {qty} SHARES (CNC)"
                        self.add_log(f"⚡ [TITAN AUTO-BUY] {sym.replace('.NS','')}: {scan_res.get('strategy')} | Bought {qty} shares @ ₹{p:.2f} (Value: ₹{needed:,.2f}) | Score: {scan_res.get('confluence_score')} | Target: ₹{scan_res.get('target_price'):.2f} (+{scan_res.get('target_pct')}%) | SL: ₹{scan_res.get('stop_loss'):.2f}", "SUCCESS")
                        break  # Strictly 1 new stock added per day
                    else:
                        self.add_log(f"⚠️ [TITAN SKIPPED] {sym.replace('.NS','')}: {exec_res.get('message', 'Execution failed')}", "WARNING")

        self.last_scan_summary = {
            "total_scanned": len(scanner_results),
            "buys_found": buys_found,
            "scanner_results": scanner_results,
            "last_scan_time": self.last_scan_time
        }

        self.add_log(f"🌌 [SWING SCAN COMPLETE] Scanned {len(scanner_results)} stocks | 🟢 {buys_found} Swing setups identified across {self.selected_strategy} strategy suite.", "INFO")
        return self.last_scan_summary

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "is_running": self.is_running,
            "selected_strategy": self.selected_strategy,
            "allocation_per_trade": self.allocation_per_trade,
            "max_positions": self.max_positions,
            "is_unlimited_positions": (not self.max_positions or self.max_positions <= 0),
            "scan_mode": self.scan_mode,
            "strict_ai_mode": getattr(self, "strict_ai_mode", False),
            "survival_mode": getattr(self, "survival_mode", True),
            "survival_breakeven_pct": getattr(self, "survival_breakeven_pct", 2.0),
            "enable_partial_profits": getattr(self, "enable_partial_profits", True),
            "portfolio_drawdown_limit_pct": getattr(self, "portfolio_drawdown_limit_pct", 3.0),
            "circuit_tripped": getattr(self, "circuit_tripped", False),
            "last_scan_time": self.last_scan_time,
            "last_scan_summary": self.last_scan_summary,
            "ai_signals_summary": self.get_ai_signals_summary(),
            "logs": self.logs[:30]
        }

swing_bot_instance = SwingTradingBot()

