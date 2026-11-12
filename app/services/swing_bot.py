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

SWING_STATE_FILE = "swing_bot_state.json"

class SwingPaperTradingEngine:
    @staticmethod
    def get_or_create_account(initial_cash: float = 100000.0) -> SwingAccount:
        acc = SwingAccount.query.first()
        if not acc:
            acc = SwingAccount(cash_balance=initial_cash)
            db.session.add(acc)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                acc = SwingAccount.query.first()
        return acc

    @classmethod
    def get_portfolio_summary(cls) -> Dict[str, Any]:
        acc = cls.get_or_create_account()
        positions = SwingPosition.query.filter(SwingPosition.quantity > 0).all()

        total_market_val = 0.0
        total_unrealized_pnl = 0.0
        pos_list = []

        now = datetime.now(timezone.utc)

        for p in positions:
            quote = MarketDataService.get_quote(p.symbol)
            curr_price = quote.get("price", p.average_price) if "error" not in quote else p.average_price
            market_val = round(p.quantity * curr_price, 2)
            cost_basis = round(p.quantity * p.average_price, 2)
            pnl = round(market_val - cost_basis, 2)
            pnl_pct = round((pnl / cost_basis * 100), 2) if cost_basis > 0 else 0.0

            # Calculate days held
            entry_dt = p.entry_date
            if entry_dt and entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
            days_held = max(1, (now - (entry_dt or now)).days)

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
                return {"success": False, "message": f"Insufficient cash (Needed ₹{turnover+charges:,.2f}, Available ₹{acc.cash_balance:,.2f})"}
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
        self.allocation_per_trade = 20000.0 # ₹20,000 per swing position (5 max positions = ₹100,000)
        self.max_positions = 5
        self.scan_mode = "ALL_NIFTY_200"
        self.last_scan_time = None
        self.last_scan_summary = {
            "total_scanned": 0,
            "buys_found": 0,
            "scanner_results": []
        }
        self.logs = []
        self.lock = threading.Lock()
        self._load_state()

    def _load_state(self):
        if os.path.exists(SWING_STATE_FILE):
            try:
                with open(SWING_STATE_FILE, 'r') as f:
                    data = json.load(f)
                    self.is_running = data.get("is_running", False)
                    self.selected_strategy = data.get("selected_strategy", "ALL")
                    self.allocation_per_trade = float(data.get("allocation_per_trade", 20000.0))
                    self.max_positions = int(data.get("max_positions", 5))
            except Exception:
                pass

    def persist_state(self):
        try:
            with open(SWING_STATE_FILE, 'w') as f:
                json.dump({
                    "is_running": self.is_running,
                    "selected_strategy": self.selected_strategy,
                    "allocation_per_trade": self.allocation_per_trade,
                    "max_positions": self.max_positions
                }, f, indent=2)
        except Exception:
            pass

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

            # Breakeven Shield (+4.0% gain locks Stop-Loss to Entry Price)
            if pnl_pct >= 4.0 and not pos.trailing_active:
                pos.stop_loss = max(pos.stop_loss, pos.average_price)
                pos.trailing_active = True
                db.session.commit()
                self.add_log(f"🛡️ [SWING BREAKEVEN] {pos.symbol.replace('.NS','')}: +{pnl_pct:.1f}% reached. Stop-Loss moved to Entry (₹{pos.average_price:.2f})", "SUCCESS")

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

        # 2. Parallel Data Fetch for Target Stocks
        target_stocks = NIFTY_200_DEFAULT if self.scan_mode == "ALL_NIFTY_200" else NIFTY_50_DEFAULT
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
        acc = SwingPaperTradingEngine.get_or_create_account()
        avail_cash = acc.cash_balance

        for sym in target_stocks:
            data = stock_data_map.get(sym)
            if not data:
                continue

            scan_res = UnifiedSwingScanner.scan_stock(sym, data, selected_strategy=self.selected_strategy)
            sig = scan_res.get("signal")
            p = scan_res.get("price", 0.0)

            if sig == "BUY":
                buys_found += 1
                # Auto-Enter if bot is running and capacity available
                if self.is_running and (len(held_symbols) < self.max_positions) and (sym not in held_symbols):
                    qty = max(1, int(self.allocation_per_trade / p))
                    needed = qty * p
                    if avail_cash >= needed:
                        exec_res = SwingPaperTradingEngine.execute_order(
                            sym, "BUY", qty, p,
                            strategy=scan_res.get("strategy_key", "VORTEX"),
                            sl=scan_res.get("stop_loss", 0.0),
                            tp=scan_res.get("target_price", 0.0),
                            confluence=scan_res.get("confluence_score", 85)
                        )
                        if exec_res.get("success"):
                            held_symbols.add(sym)
                            avail_cash -= needed
                            scan_res["action_executed"] = f"BOUGHT {qty} SHARES (CNC)"
                            self.add_log(f"⚡ [SWING ENTRY] {sym.replace('.NS','')}: {scan_res.get('strategy')} | Bought {qty} shares @ ₹{p:.2f} | Target: ₹{scan_res.get('target_price'):.2f} (+{scan_res.get('target_pct')}%) | SL: ₹{scan_res.get('stop_loss'):.2f}", "SUCCESS")

            scanner_results.append(scan_res)

        # Sort: BUY signals with highest Confluence Score first
        scanner_results.sort(key=lambda x: (0 if x["signal"] == "BUY" else 1, -x.get("confluence_score", 0)))

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
            "scan_mode": self.scan_mode,
            "last_scan_time": self.last_scan_time,
            "last_scan_summary": self.last_scan_summary,
            "logs": self.logs[:30]
        }

swing_bot_instance = SwingTradingBot()
