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
