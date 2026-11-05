# AlgoTrade - Python + Flask Personal Trading System

A modular, real-time trading workstation and algorithmic strategy sandbox built with **Python 3**, **Flask**, and **Lightweight Charts**.

---

## 🌟 Features Included

1. **Interactive Real-Time Charting**
   - Candlestick & volume charts powered by TradingView's Lightweight Charts.
   - Dynamic timeframe selector (`1D`, `5D`, `1M`, `6M`, `1Y`).
   - Live price updates and quote polling.

2. **Paper Trading & Order Execution Engine**
   - Simulated account starting with **$100,000** virtual cash.
   - Buy & sell market order executions.
   - Real-time P&L tracking (Unrealized & Realized).
   - One-click position closing and account reset.

3. **Multi-Asset Watchlist**
   - Track equities, ETFs, and major indices.
   - Add/remove custom symbols seamlessly.

4. **Algorithmic Strategy Backtester**
   - Backtest Dual Moving Average (SMA Fast/Slow) crossover strategies on historical data.
   - Compare strategy equity curve directly against standard Buy & Hold benchmarks.

5. **Extensible Architecture**
   - Ready to plug into live broker APIs (e.g., Alpaca, Interactive Brokers, Zerodha, Binance/CCXT).

---

## 📁 Project Structure

```text
Trading/
├── app/
│   ├── models/
│   │   └── trading.py          # SQLite models (Account, Position, TradeHistory, Watchlist)
│   ├── routes/
│   │   ├── api.py              # REST API endpoints (quotes, orders, backtest, portfolio)
│   │   └── views.py            # Dashboard HTML views
│   ├── services/
│   │   ├── market_data.py      # Real-time and historical OHLCV data fetcher
│   │   ├── paper_trader.py     # Execution engine and P&L calculations
│   │   └── strategy.py         # Algorithmic strategy backtester
│   ├── static/
│   │   ├── css/style.css       # Bloomberg-style dark financial UI
│   │   └── js/app.js           # Client-side reactivity & TradingView chart integration
│   └── templates/
│       ├── base.html           # Base layout with metrics bar
│       └── dashboard.html      # Trading terminal dashboard
├── venv/                       # Isolated Python virtual environment
├── config.py                   # Configuration and database settings
├── requirements.txt            # Dependency declarations
├── run.py                      # Application entrypoint
├── .env                        # Local environment variables
└── README.md                   # Documentation
```

---

## 🚀 How to Run

1. **Activate the Virtual Environment**:
   - In PowerShell:
     ```powershell
     .\venv\Scripts\Activate.ps1
     ```
   - In Command Prompt:
     ```cmd
     venv\Scripts\activate.bat
     ```

2. **Start the Flask Server**:
   ```bash
   python run.py
   ```

3. **Open the Terminal**:
   Visit [http://127.0.0.1:5000](http://127.0.0.1:5000) in your web browser.

---

## 🔌 Connecting to Live Brokers

To migrate from paper trading to live execution:
- **US Equities**: Install `alpaca-trade-api` or `alpaca-py` and configure keys in `.env`.
- **Crypto**: Install `ccxt` (`pip install ccxt`) to connect Binance, Bybit, Coinbase, or Kraken.
- **Indian Markets**: Install `kiteconnect` for Zerodha or `smartapi-python` for Angel One.

