import pandas as pd
import numpy as np
from app.services.market_data import MarketDataService

class StrategyBacktester:
    @staticmethod
    def sma_crossover(symbol: str, short_window: int = 20, long_window: int = 50, period: str = "1y", initial_capital: float = 10000.0):
        symbol = symbol.strip().upper()
        candles = MarketDataService.get_historical_candles(symbol, period=period, interval="1d")
        
        if not candles or len(candles) < long_window:
            return {"error": "Not enough historical data for the chosen parameters"}
            
        df = pd.DataFrame(candles)
        df['Close'] = df['close']
        df['SMA_Fast'] = df['Close'].rolling(window=short_window).mean()
        df['SMA_Slow'] = df['Close'].rolling(window=long_window).mean()
        
        # Signal: 1 when Fast > Slow, 0 otherwise
        df['Signal'] = 0.0
        df.loc[df.index[short_window:], 'Signal'] = np.where(df['SMA_Fast'][short_window:] > df['SMA_Slow'][short_window:], 1.0, 0.0)
        df['Position'] = df['Signal'].diff()
        
        # Calculate returns
        df['Market_Return'] = df['Close'].pct_change()
        df['Strategy_Return'] = df['Market_Return'] * df['Signal'].shift(1)
        
        # Cumulative returns
        df['Cum_Market_Return'] = (1 + df['Market_Return'].fillna(0)).cumprod()
        df['Cum_Strategy_Return'] = (1 + df['Strategy_Return'].fillna(0)).cumprod()
        
        final_strategy_equity = initial_capital * df['Cum_Strategy_Return'].iloc[-1]
        final_market_equity = initial_capital * df['Cum_Market_Return'].iloc[-1]
        
        total_trades = int((df['Position'] != 0).sum())
        
        # Performance Analytics: Max Drawdown, Win Rate & Sharpe Ratio
        cum_strat = df['Cum_Strategy_Return']
        rolling_max = cum_strat.cummax()
        drawdown_series = (cum_strat - rolling_max) / rolling_max
        max_drawdown_pct = round(float(drawdown_series.min() * 100), 2) if not drawdown_series.empty else 0.0

        # Win Rate on trade days where strategy return is realized
        active_returns = df.loc[df['Strategy_Return'] != 0, 'Strategy_Return']
        win_rate_pct = round(float((active_returns > 0).mean() * 100), 2) if len(active_returns) > 0 else 0.0

        # Annualized Sharpe Ratio (assuming 252 trading days)
        strat_ret = df['Strategy_Return'].fillna(0)
        ret_std = strat_ret.std()
        if ret_std > 1e-8:
            sharpe_ratio = round(float((strat_ret.mean() / ret_std) * np.sqrt(252)), 2)
        else:
            sharpe_ratio = 0.0
        
        dates = [str(d) for d in df['time']]
        
        return {
            "symbol": symbol,
            "period": period,
            "short_window": short_window,
            "long_window": long_window,
            "initial_capital": initial_capital,
            "final_strategy_equity": round(float(final_strategy_equity), 2),
            "strategy_return_pct": round(float((df['Cum_Strategy_Return'].iloc[-1] - 1) * 100), 2),
            "benchmark_return_pct": round(float((df['Cum_Market_Return'].iloc[-1] - 1) * 100), 2),
            "total_trades": total_trades,
            "max_drawdown_pct": max_drawdown_pct,
            "win_rate_pct": win_rate_pct,
            "sharpe_ratio": sharpe_ratio,
            "chart": {
                "dates": dates,
                "prices": [round(float(p), 2) for p in df['Close']],
                "sma_fast": [round(float(p), 2) if not pd.isna(p) else None for p in df['SMA_Fast']],
                "sma_slow": [round(float(p), 2) if not pd.isna(p) else None for p in df['SMA_Slow']],
                "strategy_equity": [round(float(initial_capital * val), 2) for val in df['Cum_Strategy_Return']],
                "benchmark_equity": [round(float(initial_capital * val), 2) for val in df['Cum_Market_Return']]
            }
        }

    @staticmethod
    def dma_200_strategy(symbol: str, period: str = "2y", initial_capital: float = 100000.0, ma_type: str = "SMA", allow_short: bool = False):
        """
        200-Day Moving Average Breakout & Regime Strategy
        - BUY when price trades above 200 DMA (Stop Loss = Exact 200 DMA)
        - EXIT / SELL when price trades below 200 DMA (Stop Loss = Exact 200 DMA)
        - Stop Loss trails dynamically each day with the exact 200 DMA line.
        """
        symbol = symbol.strip().upper()
        candles = MarketDataService.get_historical_candles(symbol, period=period, interval="1d")
        
        if not candles or len(candles) < 210:
            # Fallback to 5y if 2y had insufficient bars
            candles = MarketDataService.get_historical_candles(symbol, period="5y", interval="1d")
            if not candles or len(candles) < 210:
                return {"error": f"Insufficient historical daily bars ({len(candles) if candles else 0}) to calculate a 200 DMA."}

        df = pd.DataFrame(candles)
        df['Close'] = df['close'].astype(float)
        df['High'] = df['high'].astype(float)
        df['Low'] = df['low'].astype(float)

        if str(ma_type).upper() == "EMA":
            df['DMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        else:
            df['DMA_200'] = df['Close'].rolling(window=200).mean()

        # Generate Signals starting after 200th bar
        df['Signal'] = 0.0
        # Long condition: Close > 200 DMA
        # Short condition: Close < 200 DMA (if allow_short is True, else 0.0 for cash preservation)
        if allow_short:
            df.loc[df.index[200:], 'Signal'] = np.where(
                df['Close'][200:] > df['DMA_200'][200:], 1.0, -1.0
            )
        else:
            df.loc[df.index[200:], 'Signal'] = np.where(
                df['Close'][200:] > df['DMA_200'][200:], 1.0, 0.0
            )

        df['Position'] = df['Signal'].diff()

        # Returns: shift signal by 1 day to prevent lookahead bias (execute at next day open/close)
        df['Market_Return'] = df['Close'].pct_change()
        df['Strategy_Return'] = df['Market_Return'] * df['Signal'].shift(1)

        # Truncate warmup period (first 200 bars)
        df_active = df.iloc[200:].copy().reset_index(drop=True)
        df_active['Cum_Market_Return'] = (1 + df_active['Market_Return'].fillna(0)).cumprod()
        df_active['Cum_Strategy_Return'] = (1 + df_active['Strategy_Return'].fillna(0)).cumprod()

        final_strategy_equity = initial_capital * df_active['Cum_Strategy_Return'].iloc[-1]
        final_market_equity = initial_capital * df_active['Cum_Market_Return'].iloc[-1]

        total_trades = int((df_active['Position'] != 0).sum())

        # Performance Analytics
        cum_strat = df_active['Cum_Strategy_Return']
        rolling_max = cum_strat.cummax()
        drawdown_series = (cum_strat - rolling_max) / rolling_max
        max_drawdown_pct = round(float(drawdown_series.min() * 100), 2) if not drawdown_series.empty else 0.0

        # Benchmark Max Drawdown for comparison
        cum_bench = df_active['Cum_Market_Return']
        bench_rolling_max = cum_bench.cummax()
        bench_drawdown_series = (cum_bench - bench_rolling_max) / bench_rolling_max
        bench_max_drawdown_pct = round(float(bench_drawdown_series.min() * 100), 2) if not bench_drawdown_series.empty else 0.0

        active_returns = df_active.loc[df_active['Strategy_Return'] != 0, 'Strategy_Return']
        win_rate_pct = round(float((active_returns > 0).mean() * 100), 2) if len(active_returns) > 0 else 0.0

        strat_ret = df_active['Strategy_Return'].fillna(0)
        ret_std = strat_ret.std()
        sharpe_ratio = round(float((strat_ret.mean() / ret_std) * np.sqrt(252)), 2) if ret_std > 1e-8 else 0.0

        dates = [str(d) for d in df_active['time']]
        curr_price = float(df_active['Close'].iloc[-1])
        curr_dma = float(df_active['DMA_200'].iloc[-1])

        return {
            "symbol": symbol,
            "strategy_name": f"200-Day {ma_type.upper()} Regime & Trailing Stop",
            "period": period,
            "ma_type": ma_type.upper(),
            "allow_short": allow_short,
            "initial_capital": initial_capital,
            "final_strategy_equity": round(float(final_strategy_equity), 2),
            "final_market_equity": round(float(final_market_equity), 2),
            "strategy_return_pct": round(float((df_active['Cum_Strategy_Return'].iloc[-1] - 1) * 100), 2),
            "benchmark_return_pct": round(float((df_active['Cum_Market_Return'].iloc[-1] - 1) * 100), 2),
            "total_trades": total_trades,
            "max_drawdown_pct": max_drawdown_pct,
            "benchmark_max_drawdown_pct": bench_max_drawdown_pct,
            "drawdown_reduction_pct": round(abs(bench_max_drawdown_pct) - abs(max_drawdown_pct), 2),
            "win_rate_pct": win_rate_pct,
            "sharpe_ratio": sharpe_ratio,
            "current_status": {
                "price": round(curr_price, 2),
                "dma_200": round(curr_dma, 2),
                "regime": "BULLISH (LONG)" if curr_price >= curr_dma else "BEARISH (CASH/SHORT)",
                "distance_pct": round(((curr_price - curr_dma) / curr_dma) * 100, 2),
                "exact_stop_loss": round(curr_dma, 2)
            },
            "chart": {
                "dates": dates,
                "prices": [round(float(p), 2) for p in df_active['Close']],
                "dma_200": [round(float(p), 2) if not pd.isna(p) else None for p in df_active['DMA_200']],
                "strategy_equity": [round(float(initial_capital * val), 2) for val in df_active['Cum_Strategy_Return']],
                "benchmark_equity": [round(float(initial_capital * val), 2) for val in df_active['Cum_Market_Return']]
            }
        }

    @staticmethod
    def vwap_ema_rsi_strategy(symbol: str, period: str = "2y", initial_capital: float = 100000.0, fast_ema: int = 9, slow_ema: int = 21, rsi_period: int = 14, allow_short: bool = False):
        """
        Institutional Triple Confluence Strategy: VWAP + EMA + RSI
        - Anchor: Price vs VWAP (Institutional Volume Benchmark)
        - Trend: Fast EMA vs Slow EMA (9/21 Trend Alignment)
        - Timing: RSI-14 Pullback & Thrust Confirmation (Cardwell Bull/Bear Zones)
        - Exact Dynamic Stop-Loss = VWAP Line
        """
        symbol = symbol.strip().upper()
        candles = MarketDataService.get_historical_candles(symbol, period=period, interval="1d")
        
        if not candles or len(candles) < 35:
            candles = MarketDataService.get_historical_candles(symbol, period="5y", interval="1d")
            if not candles or len(candles) < 35:
                return {"error": f"Insufficient historical bars ({len(candles) if candles else 0}) for VWAP+EMA+RSI analysis."}

        df = pd.DataFrame(candles)
        df['Close'] = df['close'].astype(float)
        df['High'] = df['high'].astype(float)
        df['Low'] = df['low'].astype(float)
        df['Volume'] = df['volume'].astype(float)

        # 1. 20-Day Rolling VWAP (Institutional Benchmark)
        typical_price = (df['High'] + df['Low'] + df['Close']) / 3.0
        rolling_vol = df['Volume'].rolling(20).sum().replace(0, np.nan)
        rolling_tp_vol = (typical_price * df['Volume']).rolling(20).sum()
        df['VWAP'] = (rolling_tp_vol / rolling_vol).fillna(df['Close'].rolling(20).mean())

        # 2. Fast & Slow Exponential Moving Averages
        df['EMA_Fast'] = df['Close'].ewm(span=fast_ema, adjust=False).mean()
        df['EMA_Slow'] = df['Close'].ewm(span=slow_ema, adjust=False).mean()

        # 3. 14-period RSI
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
        rs = gain / (loss + 1e-9)
        df['RSI'] = 100 - (100 / (1 + rs))

        # 4. State Machine Simulation (Prevents multi-day whipsaws)
        warmup = max(slow_ema, 25)
        signals = [0.0] * len(df)
        curr_pos = 0.0

        for i in range(warmup, len(df)):
            c = df['Close'].iloc[i]
            vwap_val = df['VWAP'].iloc[i]
            ef = df['EMA_Fast'].iloc[i]
            es = df['EMA_Slow'].iloc[i]
            rsi_val = df['RSI'].iloc[i]

            if curr_pos == 0.0:
                # Long entry: Above VWAP, Bullish EMA, Healthy Momentum (48 <= RSI <= 68)
                if c > vwap_val and ef > es and 48.0 <= rsi_val <= 68.0:
                    curr_pos = 1.0
                # Short entry (if allowed): Below VWAP, Bearish EMA, Weak Momentum (32 <= RSI <= 52)
                elif allow_short and c < vwap_val and ef < es and 32.0 <= rsi_val <= 52.0:
                    curr_pos = -1.0
            elif curr_pos == 1.0:
                # Long Exit: Close < VWAP (Exact SL hit), or EMA breakdown, or RSI exhaustion > 75
                if c < vwap_val or c < (es * 0.99) or rsi_val > 75.0:
                    curr_pos = 0.0
            elif curr_pos == -1.0:
                # Short Exit: Close > VWAP (Exact SL hit), or EMA bounce, or RSI oversold < 25
                if c > vwap_val or c > (es * 1.01) or rsi_val < 25.0:
                    curr_pos = 0.0

            signals[i] = curr_pos

        df['Signal'] = signals
        df['Position'] = df['Signal'].diff()

        # Shift signals 1 bar to eliminate lookahead bias
        df['Market_Return'] = df['Close'].pct_change()
        df['Strategy_Return'] = df['Market_Return'] * df['Signal'].shift(1)

        df_active = df.iloc[warmup:].copy().reset_index(drop=True)
        df_active['Cum_Market_Return'] = (1 + df_active['Market_Return'].fillna(0)).cumprod()
        df_active['Cum_Strategy_Return'] = (1 + df_active['Strategy_Return'].fillna(0)).cumprod()

        final_strategy_equity = initial_capital * df_active['Cum_Strategy_Return'].iloc[-1]
        final_market_equity = initial_capital * df_active['Cum_Market_Return'].iloc[-1]
        total_trades = int((df_active['Position'] != 0).sum())

        cum_strat = df_active['Cum_Strategy_Return']
        rolling_max = cum_strat.cummax()
        drawdown_series = (cum_strat - rolling_max) / rolling_max
        max_drawdown_pct = round(float(drawdown_series.min() * 100), 2) if not drawdown_series.empty else 0.0

        cum_bench = df_active['Cum_Market_Return']
        bench_rolling_max = cum_bench.cummax()
        bench_drawdown_series = (cum_bench - bench_rolling_max) / bench_rolling_max
        bench_max_drawdown_pct = round(float(bench_drawdown_series.min() * 100), 2) if not bench_drawdown_series.empty else 0.0

        active_returns = df_active.loc[df_active['Strategy_Return'] != 0, 'Strategy_Return']
        win_rate_pct = round(float((active_returns > 0).mean() * 100), 2) if len(active_returns) > 0 else 0.0

        strat_ret = df_active['Strategy_Return'].fillna(0)
        ret_std = strat_ret.std()
        sharpe_ratio = round(float((strat_ret.mean() / ret_std) * np.sqrt(252)), 2) if ret_std > 1e-8 else 0.0

        dates = [str(d) for d in df_active['time']]
        curr_price = float(df_active['Close'].iloc[-1])
        curr_vwap = float(df_active['VWAP'].iloc[-1])
        curr_ef = float(df_active['EMA_Fast'].iloc[-1])
        curr_es = float(df_active['EMA_Slow'].iloc[-1])
        curr_rsi = float(df_active['RSI'].iloc[-1])

        is_bullish = curr_price >= curr_vwap and curr_ef >= curr_es
        regime = "CONFLUENCE BULLISH" if is_bullish else ("CONFLUENCE BEARISH" if curr_price < curr_vwap and curr_ef < curr_es else "NEUTRAL CHOP")

        return {
            "symbol": symbol,
            "strategy_name": f"Triple Confluence: VWAP + {fast_ema}/{slow_ema} EMA + RSI({rsi_period})",
            "period": period,
            "fast_ema": fast_ema,
            "slow_ema": slow_ema,
            "rsi_period": rsi_period,
            "allow_short": allow_short,
            "initial_capital": initial_capital,
            "final_strategy_equity": round(float(final_strategy_equity), 2),
            "final_market_equity": round(float(final_market_equity), 2),
            "strategy_return_pct": round(float((df_active['Cum_Strategy_Return'].iloc[-1] - 1) * 100), 2),
            "benchmark_return_pct": round(float((df_active['Cum_Market_Return'].iloc[-1] - 1) * 100), 2),
            "total_trades": total_trades,
            "max_drawdown_pct": max_drawdown_pct,
            "benchmark_max_drawdown_pct": bench_max_drawdown_pct,
            "drawdown_reduction_pct": round(abs(bench_max_drawdown_pct) - abs(max_drawdown_pct), 2),
            "win_rate_pct": win_rate_pct,
            "sharpe_ratio": sharpe_ratio,
            "current_status": {
                "price": round(curr_price, 2),
                "vwap": round(curr_vwap, 2),
                "ema_fast": round(curr_ef, 2),
                "ema_slow": round(curr_es, 2),
                "rsi": round(curr_rsi, 1),
                "regime": regime,
                "exact_stop_loss": round(curr_vwap, 2)
            },
            "chart": {
                "dates": dates,
                "prices": [round(float(p), 2) for p in df_active['Close']],
                "vwap": [round(float(p), 2) if not pd.isna(p) else None for p in df_active['VWAP']],
                "ema_fast": [round(float(p), 2) if not pd.isna(p) else None for p in df_active['EMA_Fast']],
                "ema_slow": [round(float(p), 2) if not pd.isna(p) else None for p in df_active['EMA_Slow']],
                "strategy_equity": [round(float(initial_capital * val), 2) for val in df_active['Cum_Strategy_Return']],
                "benchmark_equity": [round(float(initial_capital * val), 2) for val in df_active['Cum_Market_Return']]
            }
        }
