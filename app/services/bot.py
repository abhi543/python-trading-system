import threading
import time
from datetime import datetime, timezone
import pandas as pd
import numpy as np

from app.services.market_data import MarketDataService
from app.services.paper_trader import PaperTradingEngine
from app.models.trading import Position

class AutoTradingBot:
    def __init__(self):
        self.is_running = False
        self.symbol = "RELIANCE.NS"
        self.strategy = "SMA_CROSSOVER"  # SMA_CROSSOVER, RSI_MOMENTUM
        self.quantity = 10
        self.interval_seconds = 15
        self.fast_period = 9
        self.slow_period = 21
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.stop_loss_pct = 2.0   # 2% stop loss
        self.take_profit_pct = 4.0 # 4% take profit
        
        self.logs = []
        self.last_signal = "HOLD"
        self.last_run_time = None
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
            if len(self.logs) > 60:
                self.logs.pop()

    def get_status(self) -> dict:
        quote = MarketDataService.get_quote(self.symbol)
        current_price = quote.get("price", 0.0) if "error" not in quote else 0.0
        
        return {
            "is_running": self.is_running,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "quantity": self.quantity,
            "interval_seconds": self.interval_seconds,
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "rsi_period": self.rsi_period,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "last_signal": self.last_signal,
            "current_price": current_price,
            "last_run_time": self.last_run_time,
            "logs": self.logs[:25]
        }

    def start(self, config: dict = None):
        if config:
            self.symbol = config.get("symbol", self.symbol).strip().upper()
            self.strategy = config.get("strategy", self.strategy)
            self.quantity = float(config.get("quantity", self.quantity))
            self.fast_period = int(config.get("fast_period", self.fast_period))
            self.slow_period = int(config.get("slow_period", self.slow_period))
            self.rsi_period = int(config.get("rsi_period", self.rsi_period))
            self.stop_loss_pct = float(config.get("stop_loss_pct", self.stop_loss_pct))
            self.take_profit_pct = float(config.get("take_profit_pct", self.take_profit_pct))
            self.interval_seconds = int(config.get("interval_seconds", self.interval_seconds))

        if self.is_running:
            return {"success": True, "message": "Bot is already running", "status": self.get_status()}

        self.is_running = True
        self.add_log(f"Bot started on {self.symbol} using {self.strategy} (Qty: {self.quantity})", "SUCCESS")
        
        # Start background worker thread if not serverless
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

        return {"success": True, "message": f"Bot activated for {self.symbol}", "status": self.get_status()}

    def stop(self):
        self.is_running = False
        self.add_log(f"Bot paused for {self.symbol}", "WARNING")
        return {"success": True, "message": "Bot stopped", "status": self.get_status()}

    def evaluate_signals(self, app_context=None):
        """Single tick evaluation cycle (computes indicators, checks SL/TP, executes orders)"""
        self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        quote = MarketDataService.get_quote(self.symbol)
        if "error" in quote:
            self.add_log(f"Failed to fetch market quote for {self.symbol}: {quote['error']}", "ERROR")
            return {"signal": "ERROR", "reason": quote["error"]}

        current_price = quote["price"]
        candles = MarketDataService.get_historical_candles(self.symbol, period="1mo", interval="1d")
        if not candles or len(candles) < max(self.slow_period, self.rsi_period) + 2:
            self.add_log(f"Insufficient candle history to calculate indicators for {self.symbol}", "WARNING")
            return {"signal": "HOLD", "reason": "Insufficient candles"}

        df = pd.DataFrame(candles)
        closes = df['close']

        # 1. Check Stop-Loss / Take-Profit on existing positions
        from app import create_app
        # Query existing position within app context
        pos = Position.query.filter_by(symbol=self.symbol).first() if Position else None
        
        if pos and pos.quantity > 0:
            avg_price = pos.average_price
            pnl_pct = ((current_price - avg_price) / avg_price) * 100
            
            # Stop loss trigger
            if pnl_pct <= -abs(self.stop_loss_pct):
                self.add_log(f"STOP LOSS HIT on {self.symbol}! Loss: {pnl_pct:.2f}% <= -{self.stop_loss_pct}%. Liquidating {pos.quantity} shares.", "ERROR")
                res = PaperTradingEngine.execute_order(self.symbol, "SELL", pos.quantity)
                self.last_signal = "STOP_LOSS_SELL"
                return {"signal": "SELL", "reason": f"Stop loss at {pnl_pct:.2f}%", "execution": res}

            # Take profit trigger
            if pnl_pct >= abs(self.take_profit_pct):
                self.add_log(f"TAKE PROFIT HIT on {self.symbol}! Gain: +{pnl_pct:.2f}% >= +{self.take_profit_pct}%. Selling {pos.quantity} shares to lock profit.", "SUCCESS")
                res = PaperTradingEngine.execute_order(self.symbol, "SELL", pos.quantity)
                self.last_signal = "TAKE_PROFIT_SELL"
                return {"signal": "SELL", "reason": f"Take profit at +{pnl_pct:.2f}%", "execution": res}

        # 2. Strategy Signal Generation
        signal = "HOLD"
        reason = ""

        if self.strategy == "SMA_CROSSOVER":
            df['SMA_Fast'] = closes.rolling(window=self.fast_period).mean()
            df['SMA_Slow'] = closes.rolling(window=self.slow_period).mean()
            
            fast_curr = df['SMA_Fast'].iloc[-1]
            fast_prev = df['SMA_Fast'].iloc[-2]
            slow_curr = df['SMA_Slow'].iloc[-1]
            slow_prev = df['SMA_Slow'].iloc[-2]

            # Bullish crossover: Fast crosses above Slow
            if fast_curr > slow_curr and fast_prev <= slow_prev:
                signal = "BUY"
                reason = f"Golden Cross! Fast SMA ({fast_curr:.1f}) crossed above Slow SMA ({slow_curr:.1f})"
            # Bearish crossover: Fast crosses below Slow
            elif fast_curr < slow_curr and fast_prev >= slow_prev:
                signal = "SELL"
                reason = f"Death Cross! Fast SMA ({fast_curr:.1f}) crossed below Slow SMA ({slow_curr:.1f})"
            else:
                trend = "Bullish" if fast_curr > slow_curr else "Bearish"
                reason = f"In {trend} trend. Fast SMA: {fast_curr:.1f}, Slow SMA: {slow_curr:.1f}. No new cross."

        elif self.strategy == "RSI_MOMENTUM":
            # Calculate standard 14-period RSI
            delta = closes.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
            rs = gain / (loss + 1e-9)
            rsi = 100 - (100 / (1 + rs))
            current_rsi = rsi.iloc[-1]

            if current_rsi < self.rsi_oversold:
                signal = "BUY"
                reason = f"RSI Oversold ({current_rsi:.1f} < {self.rsi_oversold}) -> Reversal Buy Signal"
            elif current_rsi > self.rsi_overbought:
                signal = "SELL"
                reason = f"RSI Overbought ({current_rsi:.1f} > {self.rsi_overbought}) -> Overextended Sell Signal"
            else:
                reason = f"RSI Neutral at {current_rsi:.1f} (Thresholds: {self.rsi_oversold} - {self.rsi_overbought})"

        self.last_signal = signal

        # 3. Automatic Order Placement on Signals
        if signal == "BUY":
            # Only buy if we don't already have an active position
            if not pos or pos.quantity == 0:
                self.add_log(f"SIGNAL BUY on {self.symbol} @ ₹{current_price:.2f}. Reason: {reason}", "SUCCESS")
                exec_res = PaperTradingEngine.execute_order(self.symbol, "BUY", self.quantity)
                self.add_log(f"Execution: {exec_res.get('message')}", "SUCCESS" if exec_res.get("success") else "ERROR")
                return {"signal": "BUY", "reason": reason, "execution": exec_res}
            else:
                self.add_log(f"BUY Signal generated, but position already open ({pos.quantity} shares @ ₹{pos.average_price:.2f}). Holding.", "INFO")
                return {"signal": "BUY", "reason": "Already in position"}

        elif signal == "SELL":
            if pos and pos.quantity > 0:
                self.add_log(f"SIGNAL SELL on {self.symbol} @ ₹{current_price:.2f}. Reason: {reason}", "WARNING")
                exec_res = PaperTradingEngine.execute_order(self.symbol, "SELL", pos.quantity)
                self.add_log(f"Execution: {exec_res.get('message')}", "SUCCESS" if exec_res.get("success") else "ERROR")
                return {"signal": "SELL", "reason": reason, "execution": exec_res}
            else:
                self.add_log(f"SELL Signal generated, but no active position to exit.", "INFO")
                return {"signal": "SELL", "reason": "No position to sell"}

        self.add_log(f"Evaluated {self.symbol} @ ₹{current_price:.2f} | Status: HOLD | {reason}", "INFO")
        return {"signal": "HOLD", "reason": reason}

    def _run_loop(self):
        """Continuous background execution loop"""
        from run import app
        while self.is_running:
            try:
                with app.app_context():
                    self.evaluate_signals()
            except Exception as e:
                self.add_log(f"Loop evaluation exception: {e}", "ERROR")

            # Sleep in small increments so stop() responds immediately
            for _ in range(self.interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

# Global singleton bot instance
bot_instance = AutoTradingBot()
