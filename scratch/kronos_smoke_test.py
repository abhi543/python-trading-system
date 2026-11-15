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
    print("Please install requirements-ml.txt in your dedicated ML virtual environment:")
    print("   pip install -r requirements-ml.txt\n")
    sys.exit(1)

# Import existing application MarketDataService
from app.services.market_data import MarketDataService

def run_smoke_test():
    print("====================================================")
    print("       KRONOS FOUNDATION MODEL SMOKE TEST           ")
    print("====================================================")

    # Auto-detect CUDA if available, fallback to CPU
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"[Device] Using compute device: {device.upper()}")
    if device.startswith("cuda"):
        print(f"[Device] GPU: {torch.cuda.get_device_name(0)}")

    # 1. Fetch historical candles for AAPL (~400 daily candles)
    symbol = "AAPL"
    print(f"\n[1/4] Fetching historical daily candles for {symbol} via MarketDataService...")
    candles = MarketDataService.get_historical_candles(symbol, period="2y", interval="1d")
    if not candles:
        print("[ERROR] Failed to fetch market data.")
        sys.exit(1)

    df = pd.DataFrame(candles)
    lookback = min(400, len(df))
    df = df.iloc[-lookback:].copy().reset_index(drop=True)
    df['timestamps'] = pd.to_datetime(df['time'])
    
    # Calculate amount if not present
    df['amount'] = df['volume'] * df[['open', 'high', 'low', 'close']].mean(axis=1)

    print(f"      Loaded {len(df)} daily candles.")
    print(f"      Date Range: {df['timestamps'].iloc[0].strftime('%Y-%m-%d')} to {df['timestamps'].iloc[-1].strftime('%Y-%m-%d')}")
    print(f"      Last Close Price: ${df['close'].iloc[-1]:.2f}")

    # 2. Load Tokenizer and Model from Hugging Face
    print("\n[2/4] Loading NeoQuasar/Kronos-Tokenizer-base and NeoQuasar/Kronos-small...")
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")

    # 3. Instantiate Predictor
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=512)

    # 4. Prepare Inputs & Forecast Next 5 Trading Days
    pred_len = 5
    print(f"\n[3/4] Forecasting next {pred_len} trading days...")
    
    x_df = df[['open', 'high', 'low', 'close', 'volume', 'amount']]
    x_timestamp = df['timestamps']
    
    # Generate next 5 business days for forecast target timestamps
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
        verbose=True
    )

    print("\n[4/4] Forecast Completed Successfully!")
    print("\n================ FORECAST DATAFRAME ================")
    print(pred_df[['open', 'high', 'low', 'close', 'volume']].to_string())
    print("====================================================")

    # 5. Plot and save to scratch/kronos_smoke.png
    output_png = os.path.join(CURRENT_DIR, 'kronos_smoke.png')
    print(f"\nGenerating visualization -> {output_png}...")
    
    # Show last 60 days of history + 5 days forecast
    plot_lookback = min(60, len(df))
    hist_slice = df.iloc[-plot_lookback:]
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=False, gridspec_kw={'height_ratios': [2.5, 1]})
    
    # Price Plot
    ax1.plot(hist_slice['timestamps'], hist_slice['close'], label='Historical Close', color='#2563eb', linewidth=2.0)
    
    # Connect forecast to last known close price
    forecast_dates = [hist_slice['timestamps'].iloc[-1]] + list(pred_df.index)
    forecast_prices = [hist_slice['close'].iloc[-1]] + list(pred_df['close'])
    ax1.plot(forecast_dates, forecast_prices, label=f'Kronos {pred_len}-Day Forecast', color='#dc2626', linewidth=2.2, linestyle='--', marker='o', markersize=4)
    
    ax1.set_title(f'Kronos Foundation Model - {symbol} 5-Day Forward Forecast', fontsize=14, fontweight='bold', pad=12)
    ax1.set_ylabel('Price ($)', fontsize=12)
    ax1.grid(True, linestyle=':', alpha=0.6)
    ax1.legend(loc='upper left', frameon=True, fontsize=11)
    
    # Volume Plot
    ax2.bar(hist_slice['timestamps'], hist_slice['volume'], label='Historical Volume', color='#93c5fd', width=0.8)
    ax2.bar(pred_df.index, pred_df['volume'], label='Forecasted Volume', color='#fca5a5', width=0.8)
    ax2.set_ylabel('Volume', fontsize=12)
    ax2.grid(True, linestyle=':', alpha=0.6)
    ax2.legend(loc='upper left', frameon=True, fontsize=10)
    
    plt.tight_layout()
    plt.savefig(output_png, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"Plot saved successfully to: {output_png}")
    print("\nSmoke test finished successfully!")

if __name__ == '__main__':
    run_smoke_test()
