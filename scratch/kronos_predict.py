import os
import sys
from datetime import timedelta
import pandas as pd
import numpy as np

# Ensure project root and vendor/Kronos are in python search path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, '..'))
KRONOS_DIR = os.path.join(ROOT_DIR, 'vendor', 'Kronos')

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
if KRONOS_DIR not in sys.path:
    sys.path.insert(0, KRONOS_DIR)

# 1. Check ML dependencies gracefully
try:
    import torch
    import matplotlib.pyplot as plt
    from model import Kronos, KronosTokenizer, KronosPredictor
except ImportError as e:
    print(f"\n[ERROR] Missing ML dependencies: {e}")
    print("Please run using the ML environment:")
    print("   .\\venv-ml\\Scripts\\python.exe scratch/kronos_predict.py [SYMBOL]\n")
    sys.exit(1)

# Import existing application MarketDataService
from app.services.market_data import MarketDataService

def main():
    # Target symbol from command line argument or prompt
    if len(sys.argv) > 1:
        raw_symbol = sys.argv[1].strip().upper()
    else:
        raw_symbol = input("\nEnter NSE Stock Symbol (e.g. RELIANCE, TCS, INFY, SBIN, SAIL, NIFTY) [Default: RELIANCE]: ").strip().upper()

    if not raw_symbol:
        raw_symbol = "RELIANCE"

    # Normalize to Indian Market (NSE)
    if raw_symbol in ["NIFTY", "NIFTY50", "NIFTY 50", "^NSEI"]:
        symbol = "^NSEI"
        display_name = "NIFTY 50"
    elif raw_symbol in ["BANKNIFTY", "NIFTYBANK", "BANK NIFTY", "^NSEBANK"]:
        symbol = "^NSEBANK"
        display_name = "BANK NIFTY"
    else:
        clean = raw_symbol.replace(".NS", "").replace(".BO", "")
        symbol = f"{clean}.NS"
        display_name = clean

    print("\n" + "=" * 55)
    print(f"   KRONOS AI FORECAST: {display_name} (NSE INDIA)")
    print("=" * 55)

    # Auto-detect CUDA if available, fallback to CPU
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"[Device] Using compute engine: {device.upper()}")

    # 1. Fetch historical candles (~400 daily candles)
    print(f"\n[1/4] Fetching historical daily data for {display_name} ({symbol})...")
    candles = MarketDataService.get_historical_candles(symbol, period="2y", interval="1d")
    
    if not candles:
        # Try without .NS or with BSE (.BO) fallback if not found
        clean = symbol.replace('.NS', '')
        candles = MarketDataService.get_historical_candles(clean, period="2y", interval="1d")
        if not candles:
            candles = MarketDataService.get_historical_candles(f"{clean}.BO", period="2y", interval="1d")
            if candles:
                symbol = f"{clean}.BO"

    if not candles or len(candles) < 30:
        print(f"[ERROR] Insufficient data found for {display_name}. Please verify the NSE symbol.")
        sys.exit(1)

    df = pd.DataFrame(candles)
    lookback = min(400, len(df))
    df = df.iloc[-lookback:].copy().reset_index(drop=True)
    df['timestamps'] = pd.to_datetime(df['time'])
    df['amount'] = df['volume'] * df[['open', 'high', 'low', 'close']].mean(axis=1)

    last_close = df['close'].iloc[-1]
    print(f"      Loaded {len(df)} daily candles.")
    print(f"      Date Range: {df['timestamps'].iloc[0].strftime('%Y-%m-%d')} to {df['timestamps'].iloc[-1].strftime('%Y-%m-%d')}")
    print(f"      Latest Close Price: ₹{last_close:,.2f}")

    # 2. Load Model & Tokenizer
    print("\n[2/4] Loading Kronos foundation model...")
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=512)

    # 3. Generate 5-day forecast
    pred_len = 5
    print(f"\n[3/4] Generating next {pred_len}-day forward trajectory...")
    x_df = df[['open', 'high', 'low', 'close', 'volume', 'amount']]
    x_timestamp = df['timestamps']
    
    last_dt = x_timestamp.iloc[-1]
    future_dates = pd.bdate_range(start=last_dt + timedelta(days=1), periods=pred_len)
    y_timestamp = pd.Series(future_dates)

    pred_df = predictor.predict(
        df=x_df,
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        pred_len=pred_len,
        T=1.0,
        top_p=0.9,
        sample_count=1,
        verbose=False
    )

    # 4. Display Forecast Table (Strictly in Indian Rupees ₹)
    print("\n[4/4] Forecast Complete!")
    print("\n" + "=" * 70)
    print(f"       PROJECTED 5-DAY CANDLESTICKS FOR {display_name} (IN RUPEES ₹)")
    print("=" * 70)
    
    pred_summary = pred_df[['open', 'high', 'low', 'close', 'volume']].copy()
    pred_summary['pct_change'] = ((pred_summary['close'] - last_close) / last_close * 100).round(2)
    
    for idx, row in pred_summary.iterrows():
        date_str = idx.strftime('%Y-%m-%d')
        direction = "🟢 BULLISH" if row['pct_change'] >= 0 else "🔴 BEARISH"
        print(f" {date_str} | Open: ₹{row['open']:,.2f} | High: ₹{row['high']:,.2f} | Low: ₹{row['low']:,.2f} | Close: ₹{row['close']:,.2f} | {direction} ({row['pct_change']:+5.2f}%)")
    print("=" * 70)

    final_expected = pred_summary['pct_change'].iloc[-1]
    bias = "STRONG BULLISH 🚀" if final_expected > 1.5 else ("MODERATE BULLISH 📈" if final_expected > 0 else ("STRONG BEARISH 📉" if final_expected < -1.5 else "MODERATE BEARISH 🔻"))
    print(f"\n>> 5-Day Overall Directional Bias: {bias} (Expected move: {final_expected:+.2f}%)")

    # 5. Save chart
    clean_sym = symbol.replace("^", "").replace(".NS", "").replace(".BO", "")
    output_png = os.path.join(CURRENT_DIR, f'kronos_{clean_sym}.png')
    
    plot_lookback = min(60, len(df))
    hist_slice = df.iloc[-plot_lookback:]
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=False, gridspec_kw={'height_ratios': [2.5, 1]})
    
    ax1.plot(hist_slice['timestamps'], hist_slice['close'], label=f'Historical {display_name}', color='#2563eb', linewidth=2.0)
    forecast_dates = [hist_slice['timestamps'].iloc[-1]] + list(pred_df.index)
    forecast_prices = [hist_slice['close'].iloc[-1]] + list(pred_df['close'])
    ax1.plot(forecast_dates, forecast_prices, label=f'Kronos 5-Day Forecast', color='#dc2626', linewidth=2.2, linestyle='--', marker='o', markersize=4)
    
    ax1.set_title(f'Kronos Foundation Model - {display_name} (NSE India) 5-Day Forward Forecast', fontsize=14, fontweight='bold', pad=12)
    ax1.set_ylabel('Price (₹)', fontsize=12)
    ax1.grid(True, linestyle=':', alpha=0.6)
    ax1.legend(loc='upper left', frameon=True, fontsize=11)
    
    ax2.bar(hist_slice['timestamps'], hist_slice['volume'], label='Historical Traded Volume', color='#93c5fd', width=0.8)
    ax2.bar(pred_df.index, pred_df['volume'], label='Forecasted Volume', color='#fca5a5', width=0.8)
    ax2.set_ylabel('Traded Shares', fontsize=12)
    ax2.grid(True, linestyle=':', alpha=0.6)
    ax2.legend(loc='upper left', frameon=True, fontsize=10)
    
    plt.tight_layout()
    plt.savefig(output_png, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"\n[Chart Saved] -> {output_png}")
    print("Opening chart on your screen...")
    try:
        os.startfile(output_png)
    except Exception:
        pass
    print("Done!\n")

if __name__ == '__main__':
    main()
