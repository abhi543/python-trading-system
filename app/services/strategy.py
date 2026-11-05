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
            "chart": {
                "dates": dates,
                "prices": [round(float(p), 2) for p in df['Close']],
                "sma_fast": [round(float(p), 2) if not pd.isna(p) else None for p in df['SMA_Fast']],
                "sma_slow": [round(float(p), 2) if not pd.isna(p) else None for p in df['SMA_Slow']],
                "strategy_equity": [round(float(initial_capital * val), 2) for val in df['Cum_Strategy_Return']],
                "benchmark_equity": [round(float(initial_capital * val), 2) for val in df['Cum_Market_Return']]
            }
        }
