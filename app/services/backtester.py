import os
import io
import time
import math
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from app.services.market_data import MarketDataService

class BacktestEngine:
    """
    High-Speed Vectorized & Bar-by-Bar Quantitative Backtesting Engine.
    Simulates institutional intraday strategies (SMC Liquidity Sweep, Auto-Pilot Ensemble,
    VWAP+EMA+RSI Confluence, 1-2-3 Reversal, Volume Shocker) across 1-year datasets in < 2 seconds.
    """

    @staticmethod
    def parse_uploaded_file(file_bytes: bytes, filename: str) -> pd.DataFrame:
        """Parses raw uploaded CSV or Excel bytes into normalized DataFrame"""
        fname_lower = filename.lower()
        if fname_lower.endswith('.xlsx') or fname_lower.endswith('.xls'):
            df = pd.read_excel(io.BytesIO(file_bytes))
        else:
            try:
                df = pd.read_csv(io.BytesIO(file_bytes))
            except Exception:
                try:
                    df = pd.read_csv(io.BytesIO(file_bytes), sep=';')
                except Exception:
                    df = pd.read_csv(io.BytesIO(file_bytes), sep=r'\s+')
        return BacktestEngine.normalize_dataframe(df)

    @staticmethod
    def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """Standardizes uploaded or fetched DataFrames into uniform lowercase OHLCV structure"""
        if df is None or df.empty:
            raise ValueError("Provided dataset is empty")

        # Normalize column names: strip whitespace, lowercase
        col_map = {}
        for col in df.columns:
            c_clean = str(col).strip().lower().replace(' ', '_').replace('.', '_')
            if c_clean in ['date', 'datetime', 'timestamp', 'time']:
                col_map[col] = 'datetime'
            elif c_clean in ['open', 'open_price', 'o']:
                col_map[col] = 'open'
            elif c_clean in ['high', 'high_price', 'h']:
                col_map[col] = 'high'
            elif c_clean in ['low', 'low_price', 'l']:
                col_map[col] = 'low'
            elif c_clean in ['close', 'close_price', 'c', 'adj_close', 'adjclose']:
                col_map[col] = 'close'
            elif c_clean in ['volume', 'vol', 'v', 'shares']:
                col_map[col] = 'volume'

        df = df.rename(columns=col_map)
        req_cols = ['open', 'high', 'low', 'close']
        for rc in req_cols:
            if rc not in df.columns:
                raise ValueError(f"Missing required column '{rc}' in dataset. Available: {list(df.columns)}")

        if 'volume' not in df.columns:
            df['volume'] = 1000.0

        # Handle datetime
        if 'datetime' in df.columns:
            try:
                df['datetime'] = pd.to_datetime(df['datetime'])
            except Exception:
                df['datetime'] = pd.date_range(end=datetime.now(), periods=len(df), freq='5min')
        else:
            df['datetime'] = pd.date_range(end=datetime.now(), periods=len(df), freq='5min')

        # Convert numeric columns
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.dropna(subset=['open', 'high', 'low', 'close']).copy()
        df = df.sort_values('datetime').reset_index(drop=True)
        return df

    @staticmethod
    def fetch_dataset(symbol: str = "RELIANCE.NS", period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        """Fetches genuine historical dataset directly from market data provider"""
        chart_res, resolved = MarketDataService._fetch_chart_data(symbol, interval=interval, range_val=period)
        if not chart_res:
            raise ValueError(f"Failed to fetch market data for symbol '{symbol}'")

        timestamps = chart_res.get('timestamp', [])
        quote_data = chart_res.get('indicators', {}).get('quote', [{}])[0]
        opens = quote_data.get('open', [])
        highs = quote_data.get('high', [])
        lows = quote_data.get('low', [])
        closes = quote_data.get('close', [])
        volumes = quote_data.get('volume', [])

        records = []
        for i in range(len(timestamps)):
            c = closes[i] if i < len(closes) else None
            o = opens[i] if i < len(opens) else None
            h = highs[i] if i < len(highs) else None
            l = lows[i] if i < len(lows) else None
            v = volumes[i] if i < len(volumes) else 0.0
            if None not in (o, h, l, c):
                records.append({
                    "datetime": datetime.fromtimestamp(timestamps[i]),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v or 0.0)
                })

        if not records:
            raise ValueError(f"No valid candles retrieved for symbol '{symbol}'")

        df = pd.DataFrame(records)
        return BacktestEngine.normalize_dataframe(df)

    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """Vectorized computation of all indicators across the entire timeline"""
        df = df.copy()

        # EMAs
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['ema_200'] = df['close'].ewm(span=min(200, max(20, len(df))), adjust=False).mean()

        # RSI-14
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0.0)).rolling(14, min_periods=1).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14, min_periods=1).mean()
        rs = gain / (loss + 1e-9)
        df['rsi_14'] = 100.0 - (100.0 / (1.0 + rs))
        df['rsi_14'] = df['rsi_14'].fillna(50.0)

        # ATR-14
        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift(1)).abs()
        tr3 = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr_14'] = tr.rolling(14, min_periods=1).mean().fillna(df['close'] * 0.015)

        # RVOL (Volume Surge vs 20-period moving average)
        df['vol_avg20'] = df['volume'].rolling(20, min_periods=1).mean()
        df['vol_ratio'] = (df['volume'] / (df['vol_avg20'] + 1e-9)).fillna(1.0)

        # VWAP: session or rolling 20-period typical price
        typical_price = (df['high'] + df['low'] + df['close']) / 3.0
        rolling_window = min(20, len(df))
        roll_tp_vol = (typical_price * df['volume']).rolling(rolling_window, min_periods=1).sum()
        roll_vol = df['volume'].rolling(rolling_window, min_periods=1).sum()
        df['vwap'] = (roll_tp_vol / (roll_vol + 1e-9)).fillna(typical_price)

        # Previous Day Low & Previous Day High
        df['date_only'] = df['datetime'].dt.date
        # Check if dataset has multiple candles per day (intraday)
        is_intraday = df['date_only'].nunique() < len(df) * 0.8
        if is_intraday:
            # Group by calendar date to find each day's actual High & Low
            daily_highs = df.groupby('date_only')['high'].max().shift(1)
            daily_lows = df.groupby('date_only')['low'].min().shift(1)
            df['prev_day_high'] = df['date_only'].map(daily_highs).fillna(df['high'].shift(1)).fillna(df['high'])
            df['prev_day_low'] = df['date_only'].map(daily_lows).fillna(df['low'].shift(1)).fillna(df['low'])
        else:
            df['prev_day_high'] = df['high'].shift(1).fillna(df['high'])
            df['prev_day_low'] = df['low'].shift(1).fillna(df['low'])

        return df

    @staticmethod
    def calculate_charges(order_val: float, side: str = "BUY") -> dict:
        """Real Indian NSE intraday equity charges structure"""
        brokerage = min(20.0, round(order_val * 0.0003, 2))  # Flat ₹20 or 0.03%
        stt = round(order_val * 0.00025, 2) if side == "SELL" else 0.0  # 0.025% on intraday sell
        exchange_turnover = round(order_val * 0.0000345, 2)  # NSE 0.00345%
        sebi_turnover = round(order_val * 0.000001, 2)
        stamp_duty = round(order_val * 0.00003, 2) if side == "BUY" else 0.0  # 0.003% on buy
        gst = round((brokerage + exchange_turnover + sebi_turnover) * 0.18, 2)  # 18% GST
        total = round(brokerage + stt + exchange_turnover + sebi_turnover + stamp_duty + gst, 2)
        return {
            "brokerage": brokerage,
            "stt": stt,
            "exchange": exchange_turnover,
            "sebi": sebi_turnover,
            "stamp": stamp_duty,
            "gst": gst,
            "total": total
        }

    @staticmethod
    def run_backtest(df: pd.DataFrame, params: dict = None) -> dict:
        """
        Executes complete vectorized bar-by-bar backtest and returns high-resolution quant metrics.
        """
        start_exec_time = time.time()
        p = params or {}
        strategy = p.get("strategy", "AUTO_ENSEMBLE")
        initial_capital = float(p.get("capital", 50000.0))
        allocation = float(p.get("allocation", 5000.0))
        leverage = float(p.get("leverage", 5.0))
        stop_loss_pct = float(p.get("stop_loss_pct", 1.0))
        take_profit_pct = float(p.get("take_profit_pct", 2.8))
        use_trailing = bool(p.get("use_trailing", True))
        use_eod_cutoff = bool(p.get("use_eod_cutoff", True))
        include_charges = bool(p.get("include_charges", True))
        slippage_pct = float(p.get("slippage_pct", 0.03)) / 100.0

        # Calculate indicators
        df = BacktestEngine.calculate_indicators(df)

        capital = initial_capital
        cash = initial_capital
        position = None
        trades = []
        equity_curve = []
        peak_capital = initial_capital
        max_drawdown_amount = 0.0
        max_drawdown_pct = 0.0
        eod_closed_count = 0

        # Bar loop
        n_bars = len(df)
        for i in range(1, n_bars):
            row = df.iloc[i]
            prev_row = df.iloc[i - 1]
            t = row['datetime']
            price = float(row['close'])
            high = float(row['high'])
            low = float(row['low'])
            curr_hour = t.hour
            curr_min = t.minute
            time_mins = curr_hour * 60 + curr_min

            # 1. POSITION MANAGEMENT & EXIT CHECKS
            if position:
                pos_side = position['side']
                entry_price = position['entry_price']
                qty = position['quantity']
                stop_loss = position['stop_loss']
                take_profit = position['take_profit']
                extreme = position['extreme']

                # Update extreme
                if pos_side == 'BUY':
                    extreme = max(extreme, high)
                else:
                    extreme = min(extreme, low)
                position['extreme'] = extreme

                exit_price = None
                exit_reason = None
                is_win = False

                # A. 3:00 PM (15:00 IST) Auto-Square-Off Check (Only for intraday data)
                if use_eod_cutoff and time_mins >= (15 * 60):
                    exit_price = price
                    exit_reason = "3:00 PM Auto Square-Off"
                    eod_closed_count += 1

                # B. Take-Profit Check
                elif pos_side == 'BUY' and high >= take_profit:
                    exit_price = take_profit
                    exit_reason = f"Target Achieved (₹{take_profit:.2f})"
                    is_win = True
                elif pos_side == 'SELL' and low <= take_profit:
                    exit_price = take_profit
                    exit_reason = f"Target Achieved (₹{take_profit:.2f})"
                    is_win = True

                # C. Trailing Stop / Breakeven Shield
                elif use_trailing:
                    unrealized_roi = ((extreme - entry_price) / entry_price * 100.0) if pos_side == 'BUY' else ((entry_price - extreme) / entry_price * 100.0)
                    if unrealized_roi >= 0.65 and not position['trailing_active']:
                        position['trailing_active'] = True
                        be_price = round(entry_price * (1.0015 if pos_side == 'BUY' else 0.9985), 2)
                        if pos_side == 'BUY':
                            position['stop_loss'] = max(position['stop_loss'], be_price)
                        else:
                            position['stop_loss'] = min(position['stop_loss'], be_price)

                    # Dynamic Stop check
                    if pos_side == 'BUY' and low <= position['stop_loss']:
                        exit_price = position['stop_loss']
                        exit_reason = f"Trailing / Stop Hit (₹{exit_price:.2f})"
                    elif pos_side == 'SELL' and high >= position['stop_loss']:
                        exit_price = position['stop_loss']
                        exit_reason = f"Trailing / Stop Hit (₹{exit_price:.2f})"

                # D. Static / Ceiling Stop-Loss Check
                if exit_price is None:
                    if pos_side == 'BUY' and low <= stop_loss:
                        exit_price = stop_loss
                        exit_reason = f"Stop-Loss Triggered (₹{stop_loss:.2f})"
                    elif pos_side == 'SELL' and high >= stop_loss:
                        exit_price = stop_loss
                        exit_reason = f"Stop-Loss Triggered (₹{stop_loss:.2f})"

                # Execute Exit if triggered
                if exit_price is not None:
                    # Apply slippage on exit
                    actual_exit = exit_price * (1.0 - slippage_pct) if pos_side == 'BUY' else exit_price * (1.0 + slippage_pct)
                    gross_pnl = ((actual_exit - entry_price) * qty) if pos_side == 'BUY' else ((entry_price - actual_exit) * qty)
                    exit_order_val = qty * actual_exit
                    exit_chg = BacktestEngine.calculate_charges(exit_order_val, "SELL" if pos_side == 'BUY' else "BUY")["total"] if include_charges else 0.0
                    total_trade_chg = round(position['entry_charges'] + exit_chg, 2)
                    net_pnl = round(gross_pnl - (total_trade_chg if include_charges else 0.0), 2)
                    cost_basis = round((qty * entry_price) / leverage, 2)
                    roi_pct = round((net_pnl / cost_basis) * 100.0, 2) if cost_basis > 0 else 0.0

                    capital += net_pnl
                    trades.append({
                        "id": len(trades) + 1,
                        "side": pos_side,
                        "entry_time": position['entry_time'].strftime("%Y-%m-%d %H:%M"),
                        "exit_time": t.strftime("%Y-%m-%d %H:%M"),
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(actual_exit, 2),
                        "quantity": qty,
                        "gross_pnl": round(gross_pnl, 2),
                        "charges": total_trade_chg,
                        "net_pnl": net_pnl,
                        "roi_pct": roi_pct,
                        "is_win": net_pnl > 0,
                        "exit_reason": exit_reason,
                        "strategy_tag": position['strategy_tag']
                    })
                    position = None

            # 2. ENTRY EVALUATION (If flat)
            if position is None:
                # Disallow new entries after 14:30 IST if EOD cutoff is active
                if use_eod_cutoff and time_mins >= (14 * 60 + 30):
                    continue

                sig = "HOLD"
                sig_reason = ""
                strat_tag = "System"
                entry_sl = None
                entry_tp = None

                pdl = float(row['prev_day_low'])
                pdh = float(row['prev_day_high'])
                vwap_val = float(row['vwap'])
                ema9 = float(row['ema_9'])
                ema21 = float(row['ema_21'])
                ema200 = float(row['ema_200'])
                rsi = float(row['rsi_14'])
                atr = float(row['atr_14'])
                vol_r = float(row['vol_ratio'])

                # Strategy 1: SMC Liquidity Sweep (PDL / PDH Traps)
                if strategy in ["SMC_LIQUIDITY_SWEEP", "AUTO_ENSEMBLE"]:
                    if (low < pdl) and (price >= pdl) and (price >= vwap_val * 0.997):
                        sig = "BUY"
                        strat_tag = "SMC Liquidity Sweep"
                        raw_sl = round(low - 0.1 * atr, 2)
                        strict_sl = round(price * (1.0 - stop_loss_pct / 100.0), 2)
                        entry_sl = max(raw_sl, strict_sl)
                        risk_d = max(price * 0.004, price - entry_sl)
                        entry_tp = round(price + (risk_d * take_profit_pct), 2)
                    elif (high > pdh) and (price <= pdh) and (price <= vwap_val * 1.003):
                        sig = "SELL"
                        strat_tag = "SMC Liquidity Sweep"
                        raw_sl = round(high + 0.1 * atr, 2)
                        strict_sl = round(price * (1.0 + stop_loss_pct / 100.0), 2)
                        entry_sl = min(raw_sl, strict_sl)
                        risk_d = max(price * 0.004, entry_sl - price)
                        entry_tp = round(price - (risk_d * take_profit_pct), 2)

                # Strategy 2: VWAP + EMA + RSI Confluence
                if sig == "HOLD" and strategy in ["VWAP_EMA_RSI", "AUTO_ENSEMBLE"]:
                    if (price >= vwap_val) and (ema9 >= ema21) and (48.0 <= rsi <= 68.0):
                        sig = "BUY"
                        strat_tag = "VWAP+EMA+RSI Confluence"
                        entry_sl = max(round(vwap_val, 2), round(price * (1.0 - stop_loss_pct / 100.0), 2))
                        risk_d = max(price * 0.005, price - entry_sl)
                        entry_tp = round(price + (risk_d * 2.6), 2)
                    elif (price < vwap_val) and (ema9 < ema21) and (32.0 <= rsi <= 52.0):
                        sig = "SELL"
                        strat_tag = "VWAP+EMA+RSI Confluence"
                        entry_sl = min(round(vwap_val, 2), round(price * (1.0 + stop_loss_pct / 100.0), 2))
                        risk_d = max(price * 0.005, entry_sl - price)
                        entry_tp = round(price - (risk_d * 2.6), 2)

                # Strategy 3: Volume Shocker
                if sig == "HOLD" and strategy in ["VOLUME_SHOCKER", "AUTO_ENSEMBLE"]:
                    pct_change = ((price - prev_row['close']) / prev_row['close']) * 100.0
                    if (vol_r >= 1.75) and (0.35 <= pct_change <= 8.0) and (price >= ema200 * 0.98):
                        sig = "BUY"
                        strat_tag = "Volume Shocker"
                        entry_sl = round(price * (1.0 - stop_loss_pct / 100.0), 2)
                        entry_tp = round(price * (1.0 + (stop_loss_pct * 2.8) / 100.0), 2)

                # Strategy 4: 1-2-3 Reversal (SMA & Trend Reversal)
                if sig == "HOLD" and strategy in ["123_REVERSAL", "AUTO_ENSEMBLE"]:
                    if (prev_row['close'] <= ema21) and (price > ema21) and (ema9 > ema21) and (rsi > 50):
                        sig = "BUY"
                        strat_tag = "1-2-3 Reversal"
                        entry_sl = round(min(low, prev_row['low']) * 0.998, 2)
                        entry_sl = max(entry_sl, round(price * (1.0 - stop_loss_pct / 100.0), 2))
                        risk_d = max(price * 0.005, price - entry_sl)
                        entry_tp = round(price + (risk_d * 2.5), 2)

                # Strategy 5: RSI Extreme 20/80 Mean Reversion
                if sig == "HOLD" and strategy in ["RSI_20_80", "AUTO_ENSEMBLE"]:
                    if rsi <= 20.0:
                        sig = "BUY"
                        strat_tag = "RSI Extreme (20/80)"
                        entry_sl = round(price * (1.0 - stop_loss_pct / 100.0), 2)
                        entry_tp = round(price * 1.03, 2)
                    elif rsi >= 80.0:
                        sig = "SELL"
                        strat_tag = "RSI Extreme (20/80)"
                        entry_sl = round(price * (1.0 + stop_loss_pct / 100.0), 2)
                        entry_tp = round(price * 0.97, 2)

                # Strategy 6: 200 DMA Regime Trend
                if sig == "HOLD" and strategy == "DMA_200":
                    if prev_row['close'] < ema200 and price >= ema200:
                        sig = "BUY"
                        strat_tag = "200 DMA Trend"
                        entry_sl = round(ema200, 2)
                        risk_d = max(price * 0.005, price - entry_sl)
                        entry_tp = round(price + (risk_d * 2.8), 2)

                # If signal produced, open position
                if sig in ["BUY", "SELL"] and entry_sl and entry_tp:
                    actual_entry = price * (1.0 + slippage_pct) if sig == "BUY" else price * (1.0 - slippage_pct)
                    trade_qty = max(1, int((allocation * leverage) / actual_entry))
                    entry_order_val = trade_qty * actual_entry
                    entry_chg = BacktestEngine.calculate_charges(entry_order_val, sig)["total"] if include_charges else 0.0

                    position = {
                        "side": sig,
                        "entry_time": t,
                        "entry_price": actual_entry,
                        "quantity": trade_qty,
                        "stop_loss": entry_sl,
                        "take_profit": entry_tp,
                        "extreme": actual_entry,
                        "trailing_active": False,
                        "entry_charges": entry_chg,
                        "strategy_tag": strat_tag
                    }

            # Update Equity curve & Drawdown metrics
            peak_capital = max(peak_capital, capital)
            dd_amt = round(peak_capital - capital, 2)
            dd_pct = round((dd_amt / peak_capital) * 100.0, 2) if peak_capital > 0 else 0.0
            max_drawdown_amount = max(max_drawdown_amount, dd_amt)
            max_drawdown_pct = max(max_drawdown_pct, dd_pct)

            # Sample equity curve point (daily or every 15 bars to keep payload compact)
            if i % max(1, n_bars // 120) == 0 or i == n_bars - 1:
                equity_curve.append({
                    "time": t.strftime("%Y-%m-%d %H:%M"),
                    "equity": round(capital, 2),
                    "drawdown": dd_pct
                })

        # Calculate final quantitative summary statistics
        total_trades = len(trades)
        wins = [tr for tr in trades if tr['is_win']]
        losses = [tr for tr in trades if not tr['is_win']]
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = round((win_count / total_trades) * 100.0, 2) if total_trades > 0 else 0.0

        gross_wins = sum(tr['gross_pnl'] for tr in wins)
        gross_losses = abs(sum(tr['gross_pnl'] for tr in losses))
        profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (99.0 if gross_wins > 0 else 0.0)

        total_charges = round(sum(tr['charges'] for tr in trades), 2)
        net_pnl = round(capital - initial_capital, 2)
        roi_pct = round((net_pnl / initial_capital) * 100.0, 2)

        avg_win = round(gross_wins / win_count, 2) if win_count > 0 else 0.0
        avg_loss = round(gross_losses / loss_count, 2) if loss_count > 0 else 0.0
        risk_reward_achieved = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

        execution_duration = round(time.time() - start_exec_time, 2)

        return {
            "success": True,
            "metrics": {
                "initial_capital": initial_capital,
                "final_capital": round(capital, 2),
                "net_pnl": net_pnl,
                "roi_pct": roi_pct,
                "total_trades": total_trades,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "max_drawdown_pct": max_drawdown_pct,
                "max_drawdown_amount": max_drawdown_amount,
                "total_charges": total_charges,
                "gross_pnl": round(gross_wins - gross_losses, 2),
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "risk_reward_achieved": risk_reward_achieved,
                "eod_closed_count": eod_closed_count,
                "execution_seconds": execution_duration
            },
            "data_summary": {
                "total_candles": n_bars,
                "start_time": df.iloc[0]['datetime'].strftime("%Y-%m-%d"),
                "end_time": df.iloc[-1]['datetime'].strftime("%Y-%m-%d"),
                "strategy": strategy
            },
            "equity_curve": equity_curve,
            "trades": trades
        }

    @staticmethod
    def generate_sample_csv_content(symbol: str = "RELIANCE.NS") -> str:
        """Generates a genuine 1-year sample CSV formatted for instant backtest upload"""
        df = BacktestEngine.fetch_dataset(symbol, period="1y", interval="1d")
        buffer = io.StringIO()
        df[['datetime', 'open', 'high', 'low', 'close', 'volume']].to_csv(buffer, index=False)
        return buffer.getvalue()
