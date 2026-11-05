import threading
import time
import concurrent.futures
from datetime import datetime
import pandas as pd
import numpy as np

from app.services.market_data import MarketDataService
from app.services.paper_trader import PaperTradingEngine
from app.models.trading import Position, Account, NIFTY_50_DEFAULT

class AutoTradingBot:
    def __init__(self):
        self.is_running = False
        self.scan_mode = "ALL_NIFTY_50"  # "ALL_NIFTY_50" or "SINGLE"
        self.single_symbol = "RELIANCE.NS"
        self.strategy = "SMA_CROSSOVER"  # "SMA_CROSSOVER" or "RSI_MOMENTUM"
        self.allocation_per_trade = 50000.0  # ₹50,000 allocated per stock
        self.max_positions = 10              # Max 10 concurrent active positions
        self.interval_seconds = 20           # Scan interval
        self.fast_period = 9
        self.slow_period = 21
        self.rsi_period = 14
        self.rsi_oversold = 35
        self.rsi_overbought = 65
        self.stop_loss_pct = 2.5             # 2.5% Stop loss
        self.take_profit_pct = 5.0           # 5.0% Take profit
        
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
        return {
            "is_running": self.is_running,
            "scan_mode": self.scan_mode,
            "single_symbol": self.single_symbol,
            "strategy": self.strategy,
            "allocation_per_trade": self.allocation_per_trade,
            "max_positions": self.max_positions,
            "interval_seconds": self.interval_seconds,
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "rsi_period": self.rsi_period,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "last_run_time": self.last_run_time,
            "scan_summary": self.last_scan_summary,
            "logs": self.logs[:30]
        }

    def start(self, config: dict = None):
        if config:
            self.scan_mode = config.get("scan_mode", self.scan_mode)
            self.single_symbol = config.get("single_symbol", self.single_symbol).strip().upper()
            self.strategy = config.get("strategy", self.strategy)
            self.allocation_per_trade = float(config.get("allocation_per_trade", self.allocation_per_trade))
            self.max_positions = int(config.get("max_positions", self.max_positions))
            self.fast_period = int(config.get("fast_period", self.fast_period))
            self.slow_period = int(config.get("slow_period", self.slow_period))
            self.rsi_period = int(config.get("rsi_period", self.rsi_period))
            self.stop_loss_pct = float(config.get("stop_loss_pct", self.stop_loss_pct))
            self.take_profit_pct = float(config.get("take_profit_pct", self.take_profit_pct))
            self.interval_seconds = int(config.get("interval_seconds", self.interval_seconds))

        self.is_running = True
        mode_label = "ALL NIFTY 50 STOCKS" if self.scan_mode == "ALL_NIFTY_50" else self.single_symbol
        self.add_log(f"Auto-Bot ACTIVATED on {mode_label} | Model: {self.strategy} | Allocation/Trade: ₹{self.allocation_per_trade:,.0f} | Max Slots: {self.max_positions}", "SUCCESS")

        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

        return {"success": True, "message": f"Bot activated on {mode_label}", "status": self.get_status()}

    def stop(self):
        self.is_running = False
        self.add_log("Auto-Bot paused by operator", "WARNING")
        return {"success": True, "message": "Bot stopped", "status": self.get_status()}

    def evaluate_single_stock(self, symbol: str, held_positions_map: dict, stock_data: dict = None) -> dict:
        """Evaluates indicators, checks risk exits, and computes buy/sell signal for a stock"""
        if stock_data is None:
            stock_data = MarketDataService.get_stock_analysis_data(symbol)
            
        if "error" in stock_data:
            return {"symbol": symbol, "signal": "ERROR", "error": stock_data["error"]}

        current_price = stock_data["price"]
        change_pct = stock_data.get("percent_change", 0.0)
        closes = stock_data.get("closes", [])
        display_name = symbol.replace('.NS', '')

        # Check existing position
        pos = held_positions_map.get(symbol)
        
        # 1. Stop-Loss & Take-Profit check
        if pos and pos.quantity > 0:
            avg_price = pos.average_price
            pnl_pct = ((current_price - avg_price) / avg_price) * 100
            
            if pnl_pct <= -abs(self.stop_loss_pct):
                self.add_log(f"[STOP-LOSS] {display_name}: Hit -{abs(pnl_pct):.2f}% <= -{self.stop_loss_pct}%. Liquidating {pos.quantity} shares @ ₹{current_price:.2f}", "ERROR")
                exec_res = PaperTradingEngine.execute_order(symbol, "SELL", pos.quantity)
                return {
                    "symbol": symbol, "displayName": display_name, "price": current_price,
                    "change_pct": change_pct, "signal": "STOP_LOSS", "reason": f"Stop-Loss triggered at {pnl_pct:.2f}%",
                    "action_executed": "LIQUIDATED"
                }

            if pnl_pct >= abs(self.take_profit_pct):
                self.add_log(f"[TAKE-PROFIT] {display_name}: Hit +{pnl_pct:.2f}% >= +{self.take_profit_pct}%. Selling {pos.quantity} shares @ ₹{current_price:.2f} to lock gains!", "SUCCESS")
                exec_res = PaperTradingEngine.execute_order(symbol, "SELL", pos.quantity)
                return {
                    "symbol": symbol, "displayName": display_name, "price": current_price,
                    "change_pct": change_pct, "signal": "TAKE_PROFIT", "reason": f"Take-Profit target achieved at +{pnl_pct:.2f}%",
                    "action_executed": "PROFIT_LOCKED"
                }

        # 2. Strategy Indicator Evaluation
        if not closes or len(closes) < max(self.slow_period, self.rsi_period) + 2:
            return {
                "symbol": symbol, "displayName": display_name, "price": current_price,
                "change_pct": change_pct, "signal": "HOLD", "reason": "Insufficient candles",
                "fast_sma": None, "slow_sma": None, "rsi": None, "action_executed": None
            }

        s_closes = pd.Series(closes)

        signal = "HOLD"
        reason = ""
        fast_sma = None
        slow_sma = None
        rsi_val = None

        if self.strategy == "SMA_CROSSOVER":
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
            "action_executed": None
        }

    def evaluate_signals(self) -> dict:
        """Runs the scanner across all Nifty 50 constituents (or single stock) and auto-trades"""
        self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Get existing positions & cash balance
        held_positions = Position.query.filter(Position.quantity > 0).all()
        held_map = {p.symbol: p for p in held_positions}
        account = Account.query.first()
        available_cash = account.cash_balance if account else 0.0

        target_stocks = NIFTY_50_DEFAULT if self.scan_mode == "ALL_NIFTY_50" else [self.single_symbol]
        
        # Parallel high-speed data fetch for all Nifty 50 stocks
        stock_data_map = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
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

        scanner_results = []
        buys_found = 0
        sells_found = 0
        trades_executed = 0

        for symbol in target_stocks:
            stock_data = stock_data_map.get(symbol)
            if not stock_data:
                continue

            eval_res = self.evaluate_single_stock(symbol, held_map, stock_data)
            if eval_res.get("signal") == "ERROR":
                continue

            sig = eval_res["signal"]
            price = eval_res["price"]
            display_name = eval_res["displayName"]

            # AUTO-TRADE EXECUTION LOGIC:
            if sig == "BUY":
                buys_found += 1
                # If we don't already hold this stock and have available portfolio slots
                if symbol not in held_map and len(held_map) < self.max_positions:
                    # Calculate quantity based on capital allocation
                    trade_qty = max(1, int(self.allocation_per_trade / price))
                    total_cost = trade_qty * price

                    if available_cash >= total_cost:
                        exec_res = PaperTradingEngine.execute_order(symbol, "BUY", trade_qty)
                        if exec_res.get("success"):
                            trades_executed += 1
                            available_cash -= total_cost
                            eval_res["action_executed"] = f"BOUGHT {trade_qty} SHARES"
                            self.add_log(f"⚡ AUTO-BOUGHT {trade_qty} shares of {display_name} @ ₹{price:.2f} (Total: ₹{total_cost:,.2f}) | {eval_res['reason']}", "SUCCESS")
                            # Add to local held map to prevent double-buys
                            held_map[symbol] = Position(symbol=symbol, quantity=trade_qty, average_price=price)
                    else:
                        self.add_log(f"BUY signal on {display_name}, but insufficient cash (Needed ₹{total_cost:,.2f}, Avail ₹{available_cash:,.2f})", "WARNING")

            elif sig == "SELL":
                sells_found += 1
                # If we hold this stock, exit position
                if symbol in held_map:
                    pos = held_map[symbol]
                    exec_res = PaperTradingEngine.execute_order(symbol, "SELL", pos.quantity)
                    if exec_res.get("success"):
                        trades_executed += 1
                        eval_res["action_executed"] = f"SOLD {pos.quantity} SHARES"
                        self.add_log(f"⚡ AUTO-SOLD {pos.quantity} shares of {display_name} @ ₹{price:.2f} | {eval_res['reason']}", "WARNING")
                        del held_map[symbol]

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

        self.add_log(f"NIFTY 50 Scan Complete: Scanned {len(scanner_results)} stocks | 🟢 {buys_found} BUY signals | 🔴 {sells_found} SELL signals | {trades_executed} Trades executed", "INFO")

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
