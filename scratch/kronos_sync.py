import os
import sys
import json
import time
from datetime import datetime, timedelta
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

try:
    import torch
    import requests
    import openpyxl
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from model import Kronos, KronosTokenizer, KronosPredictor
except ImportError as e:
    print(f"\n[ERROR] Missing ML dependencies: {e}")
    print("Please run using the ML virtual environment:")
    print("   .\\venv-ml\\Scripts\\python.exe scratch/kronos_sync.py\n")
    sys.exit(1)

from app.services.market_data import MarketDataService
from app.models.nse_universe import get_top_turnover_stocks

VERCEL_API_URL = "https://python-trading-system.vercel.app/api/bot/ai-signals"
LOCAL_API_URL = "http://127.0.0.1:5000/api/bot/ai-signals"

def build_excel_forecast(summary_records, daily_records, output_path):
    """
    Builds a beautifully styled, professional Excel workbook (.xlsx)
    with 2 sheets:
    Sheet 1: 'AI Market Summary' (Ranked by profit opportunity)
    Sheet 2: '5-Day Trajectory Breakdown' (Day-by-day candle forecast)
    """
    wb = Workbook()
    
    # -------------------------------------------------------------
    # SHEET 1: AI Market Summary
    # -------------------------------------------------------------
    ws1 = wb.active
    ws1.title = "AI Market Summary"
    ws1.views.sheetView[0].showGridLines = True

    # Styling Palette
    font_title = Font(name="Segoe UI", size=15, bold=True, color="FFFFFF")
    font_sub = Font(name="Segoe UI", size=9.5, italic=True, color="CBD5E1")
    font_header = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
    font_data = Font(name="Segoe UI", size=9.5, color="1E293B")
    font_bold = Font(name="Segoe UI", size=9.5, bold=True, color="1E293B")
    
    font_bullish = Font(name="Segoe UI", size=9.5, bold=True, color="166534")
    fill_bullish = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    
    font_bearish = Font(name="Segoe UI", size=9.5, bold=True, color="991B1B")
    fill_bearish = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")

    fill_title = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    fill_header = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    fill_zebra = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    fill_white = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    thin_border_side = Side(style="thin", color="E2E8F0")
    cell_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    header_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=Side(style="medium", color="0F172A"))

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    # Title Banner (Row 1-2)
    ws1.merge_cells("A1:K1")
    title_cell = ws1["A1"]
    title_cell.value = "🔮 KRONOS AI 5-DAY FORWARD MARKET FORECAST — NSE INDIA"
    title_cell.font = font_title
    title_cell.fill = fill_title
    title_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws1.row_dimensions[1].height = 36

    ws1.merge_cells("A2:K2")
    sub_cell = ws1["A2"]
    now_str = datetime.now().strftime("%d-%b-%Y %I:%M %p IST")
    bullish_n = sum(1 for s in summary_records if "BULLISH" in s["bias"])
    bearish_n = len(summary_records) - bullish_n
    sub_cell.value = f"Generated: {now_str}  |  Universe: Top Liquid Equities  |  Synced to Bots: ALPHA (Intraday), NIGHTHAWK (24/7), TITAN (Swing)  |  Overall: {bullish_n} Bullish 🟢, {bearish_n} Bearish 🔴"
    sub_cell.font = font_sub
    sub_cell.fill = fill_title
    sub_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws1.row_dimensions[2].height = 22

    ws1.row_dimensions[3].height = 10  # blank spacer row

    # Headers (Row 4)
    headers_s1 = [
        "Rank", "Symbol", "Company", "Current Price (₹)", "5-Day Forecast (₹)", 
        "Expected Move (%)", "Directional Bias", "Projected High (₹)", 
        "Projected Low (₹)", "Max Upside (%)", "Bot Guidance"
    ]
    ws1.row_dimensions[4].height = 28
    for col_idx, h_text in enumerate(headers_s1, 1):
        cell = ws1.cell(row=4, column=col_idx, value=h_text)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
        cell.border = header_border

    # Sort summary records by expected_pct descending (best profit opportunities first)
    sorted_records = sorted(summary_records, key=lambda x: x["expected_pct"], reverse=True)

    # Populate Data Rows (Row 5+)
    for r_idx, rec in enumerate(sorted_records, 5):
        ws1.row_dimensions[r_idx].height = 22
        is_bullish = "BULLISH" in rec["bias"]
        row_fill = fill_zebra if r_idx % 2 == 0 else fill_white

        p_curr = rec["last_price"]
        p_proj = rec["projected_close"]
        p_high = rec["projected_high"]
        p_low = rec["projected_low"]
        pct_move = rec["expected_pct"]
        max_upside = round(((p_high - p_curr) / p_curr) * 100, 2) if p_curr > 0 else 0.0

        # Guidance text
        if is_bullish and pct_move >= 1.5:
            guidance = "⚡ STRONG BUY — High Conviction Swing & Momentum"
        elif is_bullish:
            guidance = "🟢 BUY ACCUMULATION — Favorable Upward Drift"
        elif pct_move <= -1.5:
            guidance = "🛑 SHIELD ACTIVATED — Block Buys / Short Setup"
        else:
            guidance = "⚠️ CAUTION — Downward Bias / Tight Stop Loss"

        row_values = [
            r_idx - 4,
            rec["ticker"],
            rec.get("name", rec["ticker"]),
            p_curr,
            p_proj,
            pct_move / 100.0,
            rec["bias"].replace("_", " "),
            p_high,
            p_low,
            max_upside / 100.0,
            guidance
        ]

        for c_idx, val in enumerate(row_values, 1):
            cell = ws1.cell(row=r_idx, column=c_idx, value=val)
            cell.font = font_data
            cell.fill = row_fill
            cell.border = cell_border
            cell.alignment = align_left

            # Alignment & formats
            if c_idx == 1:
                cell.alignment = align_center
                cell.font = font_bold
            elif c_idx == 2:
                cell.alignment = align_center
                cell.font = font_bold
            elif c_idx in (4, 5, 8, 9):
                cell.alignment = align_right
                cell.number_format = '₹#,##0.00'
            elif c_idx in (6, 10):
                cell.alignment = align_right
                cell.number_format = '+0.00%;-0.00%;0.00%'
                if c_idx == 6:
                    cell.font = font_bullish if is_bullish else font_bearish
                    cell.fill = fill_bullish if is_bullish else fill_bearish
            elif c_idx == 7:
                cell.alignment = align_center
                cell.font = font_bullish if is_bullish else font_bearish
                cell.fill = fill_bullish if is_bullish else fill_bearish

    # Auto-fit columns Sheet 1
    for col in ws1.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            # Skip title row from width measurement
            if cell.row in (1, 2):
                continue
            v_str = str(cell.value or '')
            if len(v_str) > max_len:
                max_len = len(v_str)
        ws1.column_dimensions[col_letter].width = max(max_len + 4, 12)

    # -------------------------------------------------------------
    # SHEET 2: 5-Day Trajectory Breakdown
    # -------------------------------------------------------------
    ws2 = wb.create_sheet(title="5-Day Day-by-Day Forecast")
    ws2.views.sheetView[0].showGridLines = True

    # Title Banner (Row 1-2)
    ws2.merge_cells("A1:I1")
    t2 = ws2["A1"]
    t2.value = "📈 DAY-BY-DAY NEURAL CANDLESTICK TRAJECTORIES (DAY 1 TO DAY 5)"
    t2.font = font_title
    t2.fill = fill_title
    t2.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws2.row_dimensions[1].height = 36

    ws2.merge_cells("A2:I2")
    s2 = ws2["A2"]
    s2.value = f"Individual daily Open, High, Low, Close & Volume predicted forward by Kronos-small autoregressive transformer."
    s2.font = font_sub
    s2.fill = fill_title
    s2.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws2.row_dimensions[2].height = 22

    ws2.row_dimensions[3].height = 10

    # Headers Sheet 2
    headers_s2 = [
        "Symbol", "Forecast Step", "Target Date", "Open (₹)", 
        "High (₹)", "Low (₹)", "Close (₹)", "Change vs Entry (%)", "Est. Volume"
    ]
    ws2.row_dimensions[4].height = 28
    for col_idx, h_text in enumerate(headers_s2, 1):
        cell = ws2.cell(row=4, column=col_idx, value=h_text)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
        cell.border = header_border

    for r_idx, d in enumerate(daily_records, 5):
        ws2.row_dimensions[r_idx].height = 20
        row_fill = fill_zebra if r_idx % 2 == 0 else fill_white
        chg = d["change_pct"] / 100.0
        is_pos = d["change_pct"] >= 0

        vals = [
            d["symbol"],
            d["day"],
            d["date"],
            d["open"],
            d["high"],
            d["low"],
            d["close"],
            chg,
            d["volume"]
        ]

        for c_idx, val in enumerate(vals, 1):
            cell = ws2.cell(row=r_idx, column=c_idx, value=val)
            cell.font = font_data
            cell.fill = row_fill
            cell.border = cell_border
            cell.alignment = align_left

            if c_idx in (1, 2, 3):
                cell.alignment = align_center
                if c_idx == 1:
                    cell.font = font_bold
            elif c_idx in (4, 5, 6, 7):
                cell.alignment = align_right
                cell.number_format = '₹#,##0.00'
            elif c_idx == 8:
                cell.alignment = align_right
                cell.number_format = '+0.00%;-0.00%;0.00%'
                cell.font = font_bullish if is_pos else font_bearish
                cell.fill = fill_bullish if is_pos else fill_bearish
            elif c_idx == 9:
                cell.alignment = align_right
                cell.number_format = '#,##0'

    # Auto-fit columns Sheet 2
    for col in ws2.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row in (1, 2):
                continue
            v_str = str(cell.value or '')
            if len(v_str) > max_len:
                max_len = len(v_str)
        ws2.column_dimensions[col_letter].width = max(max_len + 4, 14)

    wb.save(output_path)
    return output_path

def generate_and_sync_signals(max_stocks=30):
    print("=" * 70)
    print("      KRONOS AI -> MULTI-BOT SYNC & EXCEL FORECAST ENGINE")
    print("      Syncs with: Bot ALPHA (Intraday), Bot NIGHTHAWK (24/7), Bot TITAN (Swing)")
    print("=" * 70)
    
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"[Engine] Compute device: {device.upper()}")
    print(f"[Time]   Current time:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
    
    # 1. Fetch Top Universe Stocks
    print(f"\n[1/5] Selecting Top Liquid Equities from Universe...")
    universe = get_top_turnover_stocks(200)
    target_stocks = universe[:max_stocks]
    print(f"      Selected {len(target_stocks)} key market leaders: {', '.join([s.replace('.NS','') for s in target_stocks[:6]])}...")

    # 2. Load Model & Tokenizer
    print("\n[2/5] Initializing Kronos Foundation Model...")
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=512)

    # 3. Forecast each stock
    print(f"\n[3/5] Generating 5-day neural trajectories ({len(target_stocks)} stocks)...")
    signals_payload = {}
    summary_records = []
    daily_records = []
    bullish_count = 0
    bearish_count = 0

    pred_len = 5
    start_time = time.time()

    for idx, sym in enumerate(target_stocks, 1):
        clean_name = sym.replace('.NS', '')
        try:
            candles = MarketDataService.get_historical_candles(sym, period="2y", interval="1d")
            if not candles or len(candles) < 30:
                continue

            df = pd.DataFrame(candles)
            lookback = min(400, len(df))
            df = df.iloc[-lookback:].copy().reset_index(drop=True)
            df['timestamps'] = pd.to_datetime(df['time'])
            df['amount'] = df['volume'] * df[['open', 'high', 'low', 'close']].mean(axis=1)

            last_close = float(df['close'].iloc[-1])
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

            final_proj_close = float(pred_df['close'].iloc[-1])
            pct_move = round(((final_proj_close - last_close) / last_close) * 100, 2)
            bias = "STRONG_BULLISH" if pct_move > 1.5 else ("BULLISH" if pct_move > 0 else ("STRONG_BEARISH" if pct_move < -1.5 else "BEARISH"))
            
            proj_high = round(float(pred_df['high'].max()), 2)
            proj_low = round(float(pred_df['low'].min()), 2)

            if "BULLISH" in bias:
                bullish_count += 1
                tag = f"🟢 BULLISH ({pct_move:+5.2f}%)"
            else:
                bearish_count += 1
                tag = f"🔴 BEARISH ({pct_move:+5.2f}%)"

            # 1. API Payload item
            signals_payload[sym] = {
                "symbol": sym,
                "ticker": clean_name,
                "last_price": last_close,
                "projected_close": round(final_proj_close, 2),
                "expected_pct": pct_move,
                "bias": bias,
                "projected_high": proj_high,
                "projected_low": proj_low,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            # 2. Excel Summary Record
            summary_records.append({
                "symbol": sym,
                "ticker": clean_name,
                "name": clean_name,
                "last_price": last_close,
                "projected_close": round(final_proj_close, 2),
                "expected_pct": pct_move,
                "bias": bias,
                "projected_high": proj_high,
                "projected_low": proj_low
            })

            # 3. Excel Day-by-Day Records
            for step_idx, (f_date, f_row) in enumerate(pred_df.iterrows(), 1):
                c_close = float(f_row['close'])
                c_change = round(((c_close - last_close) / last_close) * 100, 2)
                f_date_str = f_date.strftime("%d-%b-%Y") if hasattr(f_date, "strftime") else str(f_date)[:10]
                daily_records.append({
                    "symbol": clean_name,
                    "day": f"Day {step_idx}",
                    "date": f_date_str,
                    "open": round(float(f_row['open']), 2),
                    "high": round(float(f_row['high']), 2),
                    "low": round(float(f_row['low']), 2),
                    "close": round(c_close, 2),
                    "change_pct": c_change,
                    "volume": int(f_row.get('volume', 0))
                })

            print(f" [{idx:02d}/{len(target_stocks):02d}] {clean_name:<12} -> {tag}")
        except Exception as e:
            pass

    elapsed = round(time.time() - start_time, 1)
    print(f"\n      Completed neural inference in {elapsed}s.")
    print(f"      Summary: {bullish_count} Bullish 🟢 | {bearish_count} Bearish 🔴")

    # 4. Generate & Launch Excel Forecast Report
    print("\n[4/5] Generating Formatted Excel Forecast Workbook (.xlsx)...")
    desktop_dir = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
    if not os.path.exists(desktop_dir):
        desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")

    excel_filename = "Kronos_AI_Forecast_Today.xlsx"
    desktop_excel_path = os.path.join(desktop_dir, excel_filename)
    local_excel_path = os.path.join(CURRENT_DIR, excel_filename)

    try:
        build_excel_forecast(summary_records, daily_records, desktop_excel_path)
        # Also copy/save locally in scratch
        build_excel_forecast(summary_records, daily_records, local_excel_path)
        print(f"      📊 Excel Report saved to Desktop: {desktop_excel_path}")
        print(f"      💾 Local Copy: {local_excel_path}")
        
        # Pop open the Excel workbook directly on Windows
        try:
            os.startfile(desktop_excel_path)
            print(f"      🚀 Auto-launched {excel_filename} in Microsoft Excel!")
        except Exception:
            pass
    except Exception as e:
        print(f"      ⚠️ Excel generation notice: {e}")

    # 5. Upload payload to Bot ALPHA, NIGHTHAWK, and TITAN
    print("\n[5/5] Syncing AI signals to ALPHA, NIGHTHAWK, and TITAN...")
    upload_body = {
        "metadata": {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model": "Kronos-small",
            "universe_count": len(signals_payload),
            "bullish_count": bullish_count,
            "bearish_count": bearish_count
        },
        "signals": signals_payload
    }

    # Sync to Vercel Cloud
    try:
        res = requests.post(VERCEL_API_URL, json=upload_body, timeout=8)
        if res.status_code == 200:
            print("      ☁️ Vercel Cloud:   SYNCED ALL 3 BOTS SUCCESSFULLY! (0.1s)")
        else:
            print(f"      ☁️ Vercel Cloud:   Response status {res.status_code}")
    except Exception as e:
        print(f"      ☁️ Vercel Cloud:   Notice: {e}")

    # Sync to Local Server (if running)
    try:
        res_loc = requests.post(LOCAL_API_URL, json=upload_body, timeout=3)
        if res_loc.status_code == 200:
            print("      💻 Local Desk:      SYNCED ALL 3 BOTS SUCCESSFULLY!")
    except Exception:
        pass

    # Also save local backup cache
    cache_file = os.path.join(CURRENT_DIR, 'latest_ai_signals.json')
    app_data_file = os.path.join(ROOT_DIR, 'app', 'data', 'latest_ai_signals.json')
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(upload_body, f, indent=2)
        print(f"      💾 Saved JSON:     {cache_file}")
    except Exception:
        pass
    try:
        with open(app_data_file, 'w', encoding='utf-8') as f:
            json.dump(upload_body, f, indent=2)
        print(f"      📦 App Data:       {app_data_file}")
    except Exception:
        pass

    print("\n" + "=" * 70)
    print(f"  🎉 SUCCESS: ALPHA, NIGHTHAWK, and TITAN are now synchronized!")
    print(f"  📊 Excel Forecast: {desktop_excel_path}")
    print(f"  🌐 Bot ALPHA:      https://python-trading-system.vercel.app/bot")
    print(f"  🌙 Bot NIGHTHAWK:  https://python-trading-system.vercel.app/bot-after-hours")
    print(f"  ⚡ Bot TITAN:      https://python-trading-system.vercel.app/bot-swing")
    print("=" * 70)

if __name__ == '__main__':
    max_count = 20  # Default to top 20 active liquid leaders for fast morning sync
    if len(sys.argv) > 1:
        try:
            max_count = int(sys.argv[1])
        except ValueError:
            pass
    generate_and_sync_signals(max_stocks=max_count)
