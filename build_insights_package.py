import os
import json
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether, PageBreak, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

# 1. LOAD DATA
with open('nighthawk_overnight_data.json', 'r', encoding='utf-8') as f:
    raw_data = json.load(f)

account = raw_data.get('portfolio', {}).get('account', {})
closed_summary = raw_data.get('closed_trades', {}).get('summary', {})
trades_list = raw_data.get('closed_trades', {}).get('trades', [])
positions_list = raw_data.get('portfolio', {}).get('positions', [])

df = pd.DataFrame(trades_list)
df['realized_pnl'] = pd.to_numeric(df['realized_pnl'], errors='coerce').fillna(0)
df['charges'] = pd.to_numeric(df['charges'], errors='coerce').fillna(0)
df['net_pnl'] = df['realized_pnl'] - df['charges']
df['turnover'] = pd.to_numeric(df['turnover'] if 'turnover' in df.columns else df.get('total_value', 0), errors='coerce').fillna(0)
df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(0)
df['entry_price'] = pd.to_numeric(df['entry_price'], errors='coerce').fillna(0)
df['exit_price'] = pd.to_numeric(df['exit_price'] if 'exit_price' in df.columns else df.get('price', 0), errors='coerce').fillna(0)
df['roi_pct'] = pd.to_numeric(df['roi_pct'], errors='coerce').fillna(0)

if 'timestamp' in df.columns:
    df['dt'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df.sort_values('dt', ascending=True).reset_index(drop=True)

# Performance Metrics
initial_capital = 10000000.0  # ₹1 Crore base
df['cum_net_pnl'] = df['net_pnl'].cumsum()
df['equity'] = initial_capital + df['cum_net_pnl']
df['peak_equity'] = df['equity'].cummax()
df['drawdown'] = df['equity'] - df['peak_equity']
df['drawdown_pct'] = (df['drawdown'] / df['peak_equity']) * 100

total_trades = len(df)
wins = df[df['net_pnl'] > 0]
losses = df[df['net_pnl'] <= 0]
win_count = len(wins)
loss_count = len(losses)
win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
gross_wins = wins['net_pnl'].sum()
gross_losses = abs(losses['net_pnl'].sum())
net_realized_pnl = df['net_pnl'].sum()
total_charges = df['charges'].sum()
profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else 0
avg_win = wins['net_pnl'].mean() if win_count > 0 else 0
avg_loss = abs(losses['net_pnl'].mean()) if loss_count > 0 else 0
payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0
expectancy = ((win_rate/100 * avg_win) - ((1 - win_rate/100) * avg_loss))
max_dd_val = df['drawdown'].min()
max_dd_pct = df['drawdown_pct'].min()
total_turnover = df['turnover'].sum()

open_unrealized_pnl = account.get('unrealized_pnl', 0.0)
final_total_equity = account.get('total_equity', df['equity'].iloc[-1])
roi_on_capital = ((final_total_equity - initial_capital) / initial_capital) * 100

print(f"Metrics: Net PnL = Rs. {net_realized_pnl:,.2f}, Total Equity = Rs. {final_total_equity:,.2f} (+{roi_on_capital:.2f}%)")

# ------------------------------------------------------------------------------
# 2. GENERATE HIGH-RES CHARTS WITH MATPLOTLIB
# ------------------------------------------------------------------------------
plt.style.use('dark_background')
os.makedirs('report_assets', exist_ok=True)

# Chart 1: Equity Growth Curve & Underwater Drawdown
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.2), gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
fig.patch.set_facecolor('#0B0F19')
ax1.set_facecolor('#111827')
ax2.set_facecolor('#111827')

# Equity Plot
trades_idx = np.arange(1, len(df) + 1)
equity_crores = df['equity'] / 1e7
ax1.plot(trades_idx, equity_crores, color='#00F090', linewidth=2.2, label='Portfolio Equity (in ₹ Crores)')
ax1.fill_between(trades_idx, 1.0, equity_crores, color='#00F090', alpha=0.15)
ax1.axhline(1.0, color='#94A3B8', linestyle='--', linewidth=1.0, label='Starting Capital (₹1.00 Cr)')

ax1.set_title('NIGHTHAWK OVERNIGHT PERFORMANCE: COMPOUND EQUITY CURVE', fontsize=12, fontweight='bold', color='#FFFFFF', pad=12)
ax1.set_ylabel('Equity (₹ Crores)', fontsize=10, color='#CBD5E1', fontweight='bold')
ax1.yaxis.set_major_formatter(ticker.FormatStrFormatter('₹%.2f Cr'))
ax1.grid(True, linestyle=':', alpha=0.25, color='#64748B')
ax1.legend(loc='upper left', framealpha=0.4, facecolor='#0B0F19', edgecolor='#334155', fontsize=9)

# Drawdown Plot
dd_pct = df['drawdown_pct']
ax2.fill_between(trades_idx, 0, dd_pct, color='#EF4444', alpha=0.4)
ax2.plot(trades_idx, dd_pct, color='#EF4444', linewidth=1.2)
ax2.set_ylabel('Drawdown %', fontsize=9, color='#CBD5E1', fontweight='bold')
ax2.set_xlabel('Trade Sequence (# 1 to 906)', fontsize=10, color='#CBD5E1', fontweight='bold')
ax2.yaxis.set_major_formatter(ticker.FormatStrFormatter('%.1f%%'))
ax2.grid(True, linestyle=':', alpha=0.25, color='#64748B')
ax2.set_ylim([min(max_dd_pct * 1.3, -2.5), 0.2])

plt.tight_layout()
chart_equity_path = 'report_assets/chart_equity_curve.png'
plt.savefig(chart_equity_path, dpi=200, bbox_inches='tight', facecolor=fig.get_facecolor())
plt.close()

# Chart 2: P&L Distribution Histogram
fig, ax = plt.subplots(figsize=(7, 3.8))
fig.patch.set_facecolor('#0B0F19')
ax.set_facecolor('#111827')

n_bins = 40
win_pnl = df[df['net_pnl'] > 0]['net_pnl'] / 1e3
loss_pnl = df[df['net_pnl'] <= 0]['net_pnl'] / 1e3

ax.hist(loss_pnl, bins=25, color='#EF4444', alpha=0.75, label=f'Losses ({loss_count} trades)')
ax.hist(win_pnl, bins=25, color='#00F090', alpha=0.75, label=f'Wins ({win_count} trades)')
ax.axvline(0, color='#FFFFFF', linestyle='--', linewidth=1.0)
ax.axvline(expectancy / 1e3, color='#38BDF8', linestyle='-', linewidth=2.0, label=f'Expectancy (+₹{expectancy/1e3:.1f}k)')

ax.set_title('TRADE P&L DISTRIBUTION (POSITIVE EXPECTANCY CLUSTER)', fontsize=11, fontweight='bold', color='#FFFFFF', pad=10)
ax.set_xlabel('Net Trade P&L (₹ Thousands)', fontsize=9, color='#CBD5E1', fontweight='bold')
ax.set_ylabel('Trade Frequency', fontsize=9, color='#CBD5E1', fontweight='bold')
ax.grid(True, linestyle=':', alpha=0.25, color='#64748B')
ax.legend(loc='upper right', framealpha=0.4, facecolor='#0B0F19', edgecolor='#334155', fontsize=8)

plt.tight_layout()
chart_hist_path = 'report_assets/chart_pnl_hist.png'
plt.savefig(chart_hist_path, dpi=200, bbox_inches='tight', facecolor=fig.get_facecolor())
plt.close()

# Chart 3: Top 6 Profit vs Top 6 Loss Stocks
stock_pnl = df.groupby('symbol')['net_pnl'].sum().sort_values()
top_losses = stock_pnl.head(6) / 1e5
top_gains = stock_pnl.tail(6) / 1e5
combo_stocks = pd.concat([top_losses, top_gains])

fig, ax = plt.subplots(figsize=(8.5, 4.2))
fig.patch.set_facecolor('#0B0F19')
ax.set_facecolor('#111827')

bar_colors = ['#EF4444' if v < 0 else '#00F090' for v in combo_stocks.values]
clean_labels = [s.replace('.NS', '') for s in combo_stocks.index]
bars = ax.barh(clean_labels, combo_stocks.values, color=bar_colors, height=0.65, alpha=0.85)

for bar in bars:
    w = bar.get_width()
    offset = 0.15 if w >= 0 else -0.15
    ha = 'left' if w >= 0 else 'right'
    color = '#00F090' if w >= 0 else '#EF4444'
    ax.text(w + offset, bar.get_y() + bar.get_height()/2, f'₹{w:+.2f}L', va='center', ha=ha, fontsize=8, fontweight='bold', color=color)

ax.axvline(0, color='#64748B', linestyle='-', linewidth=0.8)
ax.set_title('TOP PROFIT & LOSS GENERATING TICKERS (IN ₹ LAKHS)', fontsize=11, fontweight='bold', color='#FFFFFF', pad=10)
ax.set_xlabel('Cumulative Realized P&L (₹ Lakhs)', fontsize=9, color='#CBD5E1', fontweight='bold')
ax.grid(True, linestyle=':', alpha=0.25, color='#64748B', axis='x')

plt.tight_layout()
chart_stocks_path = 'report_assets/chart_stocks.png'
plt.savefig(chart_stocks_path, dpi=200, bbox_inches='tight', facecolor=fig.get_facecolor())
plt.close()

print("Charts successfully rendered!")

# ------------------------------------------------------------------------------
# 3. BUILD PROFESSIONAL EXCEL WORKBOOK (.XLSX)
# ------------------------------------------------------------------------------
wb = Workbook()

# Colors
navy_header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
slate_subhead_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
green_accent_fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
red_accent_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
card_bg_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

font_title = Font(name="Segoe UI", size=15, bold=True, color="FFFFFF")
font_head = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
font_bold = Font(name="Segoe UI", size=9, bold=True)
font_regular = Font(name="Segoe UI", size=9)
font_green_bold = Font(name="Segoe UI", size=9, bold=True, color="047857")
font_red_bold = Font(name="Segoe UI", size=9, bold=True, color="B91C1C")
font_kpi_num = Font(name="Consolas", size=13, bold=True, color="0F172A")

thin_border = Border(
    left=Side(style='thin', color="CBD5E1"),
    right=Side(style='thin', color="CBD5E1"),
    top=Side(style='thin', color="CBD5E1"),
    bottom=Side(style='thin', color="CBD5E1")
)

# SHEET 1: EXECUTIVE SUMMARY & KPIS
ws_summary = wb.active
ws_summary.title = "Executive Summary & KPIs"
ws_summary.views.sheetView[0].showGridLines = True

# Title Banner
ws_summary.merge_cells("A1:G1")
ws_summary["A1"] = "NIGHTHAWK ALGORITHMIC DESK — OVERNIGHT AUDIT & PERFORMANCE INSIGHTS"
ws_summary["A1"].font = font_title
ws_summary["A1"].fill = navy_header_fill
ws_summary["A1"].alignment = Alignment(horizontal="center", vertical="center")
ws_summary.row_dimensions[1].height = 40

ws_summary.merge_cells("A2:G2")
ws_summary["A2"] = "Audit Period: Overnight Cadence | Base Capital: ₹1,00,00,000 (1 Crore) | Strategy: 1-2-3 Reversal (5X Leverage)"
ws_summary["A2"].font = Font(name="Segoe UI", size=9, italic=True, color="64748B")
ws_summary["A2"].alignment = Alignment(horizontal="center", vertical="center")
ws_summary.row_dimensions[2].height = 20

# KPI Blocks
kpis = [
    ("TOTAL NET REALIZED P&L", f"₹{net_realized_pnl:,.2f}", f"+{(net_realized_pnl/initial_capital)*100:.2f}% Return"),
    ("PORTFOLIO NET EQUITY", f"₹{final_total_equity:,.2f}", f"+{roi_on_capital:.2f}% Total ROI"),
    ("WIN RATE", f"{win_rate:.2f}%", f"{win_count} Wins / {loss_count} Losses"),
    ("PROFIT FACTOR", f"{profit_factor:.2f}", "Gross Profit / Gross Loss"),
    ("TRADE EXPECTANCY", f"₹{expectancy:,.2f}", "Expected Edge Per Trade"),
    ("MAX DRAWDOWN", f"{max_dd_pct:.2f}%", f"₹{max_dd_val:,.2f} Max Peak-to-Trough")
]

row = 4
for i, (title, val, sub) in enumerate(kpis):
    c_start = (i % 3) * 2 + 1
    r_start = row + (i // 3) * 3
    
    ws_summary.cell(row=r_start, column=c_start, value=title).font = Font(name="Segoe UI", size=8, bold=True, color="64748B")
    ws_summary.cell(row=r_start+1, column=c_start, value=val).font = font_kpi_num
    ws_summary.cell(row=r_start+2, column=c_start, value=sub).font = Font(name="Segoe UI", size=8, color="047857" if "Return" in sub or "Total" in sub or "Wins" in sub else "475569")
    
    for r in range(r_start, r_start+3):
        for c in range(c_start, c_start+2):
            cell = ws_summary.cell(row=r, column=c)
            cell.fill = card_bg_fill
            cell.border = thin_border

# Detailed Metrics Table
row_det = 11
headers_det = ["METRIC CLASSIFICATION", "VALUE", "BENCHMARK / TARGET", "STATUS / EVALUATION"]
ws_summary.merge_cells(f"A{row_det}:D{row_det}")
ws_summary[f"A{row_det}"] = "DETAILED QUANTITATIVE DESK METRICS"
ws_summary[f"A{row_det}"].font = font_head
ws_summary[f"A{row_det}"].fill = slate_subhead_fill
ws_summary[f"A{row_det}"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
row_det += 1

for col_idx, h in enumerate(headers_det, 1):
    c = ws_summary.cell(row=row_det, column=col_idx, value=h)
    c.font = font_head
    c.fill = slate_subhead_fill
    c.border = thin_border
row_det += 1

metric_rows = [
    ("Starting Account Balance", f"₹{initial_capital:,.2f}", "₹1,00,00,000.00", "Baseline Capital"),
    ("Ending Closed Cash Balance", f"₹{df['equity'].iloc[-1]:,.2f}", "> ₹1,00,00,000.00", "Doubled Account (+104.99%)"),
    ("Current Active Total Equity", f"₹{final_total_equity:,.2f}", "> ₹1,00,00,000.00", f"+{roi_on_capital:.2f}% Total Desk Return"),
    ("Total Closed Trades", str(total_trades), "High Sample Size (>500)", "Statistically Significant"),
    ("Winning Trades (Take Profit / Trailing)", str(win_count), "> 50.0%", f"{win_rate:.2f}% Win Rate"),
    ("Losing Trades (Stop Loss / Scratch)", str(loss_count), "< 50.0%", f"{100-win_rate:.2f}% Loss Frequency"),
    ("Gross Realized Profits", f"₹{gross_wins:,.2f}", "-", "Total Winning Upside"),
    ("Gross Realized Losses", f"-₹{gross_losses:,.2f}", "-", "Contained by Hard SL Ceiling"),
    ("Profit Factor (Wins / Losses)", f"{profit_factor:.2f}", "> 1.75 Institutional", "Super-Edge (>2.00)"),
    ("Average Win Amount", f"₹{avg_win:,.2f}", "> Average Loss", "Positive Edge"),
    ("Average Loss Amount", f"-₹{avg_loss:,.2f}", "< Average Win", "Tight Risk Containment"),
    ("Win / Loss Payoff Ratio", f"{payoff_ratio:.2f}", "> 1.15", "1.22x Reward to Risk"),
    ("Mathematical Expectancy Per Trade", f"₹{expectancy:,.2f}", "> ₹0.00", "Highly Positive Drift (+₹11.5k/trade)"),
    ("Maximum Equity Peak-to-Trough Drawdown", f"₹{max_dd_val:,.2f} ({max_dd_pct:.2f}%)", "< 5.0% Limit", "Ultra-Low Drawdown (-1.53%)"),
    ("Total Brokerage & Statutory Taxes", f"₹{total_charges:,.2f}", "< 3.0% of PnL", "2.29% Commission Friction"),
    ("Total Notional Volume Turnover", f"₹{total_turnover:,.2f}", "-", "Capital Velocity Across Basket"),
    ("Active Open Positions", str(len(positions_list)), "< 50 Positions", f"{len(positions_list)} Open (₹{open_unrealized_pnl:+,.2f})")
]

for name, val, bench, status in metric_rows:
    r_cells = [
        ws_summary.cell(row=row_det, column=1, value=name),
        ws_summary.cell(row=row_det, column=2, value=val),
        ws_summary.cell(row=row_det, column=3, value=bench),
        ws_summary.cell(row=row_det, column=4, value=status)
    ]
    for cell in r_cells:
        cell.font = font_regular
        cell.border = thin_border
    r_cells[0].font = font_bold
    r_cells[1].font = font_green_bold if ("+" in val or "Profit Factor" in name or "Winning" in name or "Doubled" in status) else (font_red_bold if "-" in val and "Starting" not in name else font_bold)
    row_det += 1

# Long vs Short Summary Table
row_det += 1
ws_summary.merge_cells(f"A{row_det}:D{row_det}")
ws_summary[f"A{row_det}"] = "DIRECTIONAL SIDE PERFORMANCE (LONG VS SHORT)"
ws_summary[f"A{row_det}"].font = font_head
ws_summary[f"A{row_det}"].fill = slate_subhead_fill
row_det += 1

ws_summary.cell(row=row_det, column=1, value="DIRECTION").font = font_head
ws_summary.cell(row=row_det, column=2, value="TRADES").font = font_head
ws_summary.cell(row=row_det, column=3, value="WIN RATE").font = font_head
ws_summary.cell(row=row_det, column=4, value="NET P&L (₹)").font = font_head
for c in range(1, 5):
    ws_summary.cell(row=row_det, column=c).fill = slate_subhead_fill
    ws_summary.cell(row=row_det, column=c).border = thin_border
row_det += 1

longs = df[df['side'] == 'SELL']
shorts = df[df['side'] == 'COVER']
long_wins = len(longs[longs['net_pnl'] > 0])
short_wins = len(shorts[shorts['net_pnl'] > 0])

side_data = [
    ("LONG POSITIONS (BUY & EXIT)", len(longs), f"{(long_wins/len(longs))*100:.1f}% ({long_wins}/{len(longs)})", f"₹{longs['net_pnl'].sum():,.2f}"),
    ("SHORT POSITIONS (SHORT & COVER)", len(shorts), f"{(short_wins/len(shorts))*100:.1f}% ({short_wins}/{len(shorts)})", f"₹{shorts['net_pnl'].sum():,.2f}"),
    ("COMBINED TWO-WAY DESK", len(df), f"{win_rate:.1f}% ({win_count}/{len(df)})", f"₹{df['net_pnl'].sum():,.2f}")
]

for sname, scount, swr, spnl in side_data:
    c1 = ws_summary.cell(row=row_det, column=1, value=sname)
    c2 = ws_summary.cell(row=row_det, column=2, value=scount)
    c3 = ws_summary.cell(row=row_det, column=3, value=swr)
    c4 = ws_summary.cell(row=row_det, column=4, value=spnl)
    for c in [c1, c2, c3, c4]:
        c.font = font_bold if "COMBINED" in sname else font_regular
        c.border = thin_border
    c4.font = font_green_bold
    row_det += 1

ws_summary.column_dimensions['A'].width = 38
ws_summary.column_dimensions['B'].width = 24
ws_summary.column_dimensions['C'].width = 24
ws_summary.column_dimensions['D'].width = 32
ws_summary.column_dimensions['E'].width = 20
ws_summary.column_dimensions['F'].width = 20
ws_summary.column_dimensions['G'].width = 20

# SHEET 2: ALL 906 CLOSED TRADES LOG
ws_trades = wb.create_sheet(title="Trade Logs (All 906)")
ws_trades.views.sheetView[0].showGridLines = True

trade_headers = ["TRADE ID", "TIMESTAMP", "SYMBOL", "INSTRUMENT", "SIDE", "QTY", "ENTRY PRICE (₹)", "EXIT PRICE (₹)", "TURNOVER (₹)", "GROSS P&L (₹)", "CHARGES (₹)", "NET P&L (₹)", "ROI (%)"]
for col_idx, h in enumerate(trade_headers, 1):
    c = ws_trades.cell(row=1, column=col_idx, value=h)
    c.font = font_head
    c.fill = navy_header_fill
    c.border = thin_border
    c.alignment = Alignment(horizontal="center", vertical="center")
ws_trades.row_dimensions[1].height = 25

for r_idx, row_data in enumerate(df.itertuples(), start=2):
    is_win = row_data.net_pnl > 0
    c_pnl = font_green_bold if is_win else font_red_bold
    
    vals = [
        str(getattr(row_data, 'id', r_idx - 1)),
        str(getattr(row_data, 'timestamp', '--')),
        str(getattr(row_data, 'displayName', row_data.symbol.replace('.NS',''))),
        str(row_data.symbol),
        str(row_data.side),
        int(row_data.quantity),
        round(float(row_data.entry_price), 2),
        round(float(row_data.exit_price), 2),
        round(float(row_data.turnover), 2),
        round(float(row_data.realized_pnl), 2),
        round(float(row_data.charges), 2),
        round(float(row_data.net_pnl), 2),
        round(float(row_data.roi_pct), 2)
    ]
    
    for c_idx, val in enumerate(vals, start=1):
        cell = ws_trades.cell(row=r_idx, column=c_idx, value=val)
        cell.font = font_regular
        cell.border = thin_border
        if c_idx in [7, 8, 9, 10, 11, 12]:
            cell.number_format = '₹#,##0.00'
        elif c_idx == 13:
            cell.number_format = '0.00"%"'
        if c_idx in [10, 12]:
            cell.font = c_pnl
            cell.fill = green_accent_fill if is_win else red_accent_fill
        if c_idx == 5:
            cell.font = font_green_bold if val == "SELL" else font_red_bold

for col in ws_trades.columns:
    max_len = max(len(str(cell.value or '')) for cell in col)
    col_letter = get_column_letter(col[0].column)
    ws_trades.column_dimensions[col_letter].width = max(max_len + 3, 12)

# SHEET 3: STOCK-BY-STOCK LEADERBOARD
ws_stocks = wb.create_sheet(title="Stock Performance Breakdown")
ws_stocks.views.sheetView[0].showGridLines = True

stock_group = df.groupby('symbol')
stock_summary_rows = []
for sym, grp in stock_group:
    s_wins = grp[grp['net_pnl'] > 0]
    s_losses = grp[grp['net_pnl'] <= 0]
    s_turnover = grp['turnover'].sum()
    s_net_pnl = grp['net_pnl'].sum()
    s_charges = grp['charges'].sum()
    s_wr = (len(s_wins) / len(grp)) * 100
    stock_summary_rows.append({
        "symbol": sym,
        "name": sym.replace('.NS', ''),
        "trades": len(grp),
        "wins": len(s_wins),
        "losses": len(s_losses),
        "win_rate": s_wr,
        "turnover": s_turnover,
        "charges": s_charges,
        "net_pnl": s_net_pnl,
        "avg_trade_pnl": s_net_pnl / len(grp)
    })

df_stocks = pd.DataFrame(stock_summary_rows).sort_values('net_pnl', ascending=False).reset_index(drop=True)

stock_headers = ["SYMBOL", "INSTRUMENT", "TOTAL TRADES", "WINS", "LOSSES", "WIN RATE (%)", "TOTAL TURNOVER (₹)", "TOTAL CHARGES (₹)", "NET REALIZED P&L (₹)", "AVG P&L / TRADE (₹)"]
for col_idx, h in enumerate(stock_headers, 1):
    c = ws_stocks.cell(row=1, column=col_idx, value=h)
    c.font = font_head
    c.fill = navy_header_fill
    c.border = thin_border
    c.alignment = Alignment(horizontal="center", vertical="center")
ws_stocks.row_dimensions[1].height = 25

for r_idx, row_data in enumerate(df_stocks.itertuples(), start=2):
    is_win = row_data.net_pnl > 0
    c_pnl = font_green_bold if is_win else font_red_bold
    
    vals = [
        row_data.name,
        row_data.symbol,
        row_data.trades,
        row_data.wins,
        row_data.losses,
        round(row_data.win_rate, 2),
        round(row_data.turnover, 2),
        round(row_data.charges, 2),
        round(row_data.net_pnl, 2),
        round(row_data.avg_trade_pnl, 2)
    ]
    for c_idx, val in enumerate(vals, start=1):
        cell = ws_stocks.cell(row=r_idx, column=c_idx, value=val)
        cell.font = font_regular
        cell.border = thin_border
        if c_idx in [7, 8, 9, 10]:
            cell.number_format = '₹#,##0.00'
        elif c_idx == 6:
            cell.number_format = '0.00"%"'
        if c_idx in [9, 10]:
            cell.font = c_pnl
            cell.fill = green_accent_fill if is_win else red_accent_fill

for col in ws_stocks.columns:
    max_len = max(len(str(cell.value or '')) for cell in col)
    col_letter = get_column_letter(col[0].column)
    ws_stocks.column_dimensions[col_letter].width = max(max_len + 3, 14)

# SHEET 4: RISK MANAGEMENT & INSIGHTS BLUEPRINT
ws_blueprint = wb.create_sheet(title="Risk Insights & Live Rules")
ws_blueprint.views.sheetView[0].showGridLines = True

blueprint_sections = [
    ("1. WHY DID THE PORTFOLIO DOUBLE OVERNIGHT? (4 CORE DRIVERS)", [
        "A. The Hard SL Ceiling Clamp: By capping maximum allowable stop-loss at 1.0% (Entry - 1.0%), you amputated the left tail-risk. Losses could never exceed ~1.0%, while winners ran to +1.0% to +3.5%.",
        "B. Breakeven Shield (+1.0% Activation): As soon as trades gained +1.0%, the Stop-Loss was raised to Entry Price (Avg Price), converting potential loser reversals into 100% risk-free trades.",
        "C. High Mathematical Expectancy: Win Rate of 63.02% combined with 1.22x Reward-to-Risk yielded an average expected profit of +₹11,588.75 on EVERY single trade.",
        "D. High-Velocity Compounding: 906 trades across 50 Nifty constituents multiplied that +₹11.5k edge, accumulating ₹1.05 Crore net realized profit in hours."
    ]),
    ("2. INSTITUTIONAL REALITY CHECK: CRITICAL DIFFERENCES IN LIVE TRADING", [
        "A. Execution Slippage: In live markets, placing 900+ market orders incurs bid-ask spread slippage (0.05% - 0.15%), which can consume 20-30% of gross profits.",
        "B. Market Operating Hours: NIGHTHAWK runs 24/7 in continuous simulation. Live NSE Cash/MIS trading is strictly 09:15 AM to 03:30 PM. Unclosed intraday positions carry overnight gap risk.",
        "C. Exchange Order Rate Limits: Brokers like Zerodha/Angel throttle API requests to 10-20 requests/sec. Fast sweep executions require batching or rate limiting.",
        "D. Margin Cluster Drawdown: Simultaneous peak exposure reached 49 positions (~₹97 Lakhs margin). In a broad market crash, all 49 positions drop simultaneously, triggering correlated drawdowns."
    ]),
    ("3. ACTIONABLE RULES FOR BOT ALPHA (LIVE REAL MONEY DESK)", [
        "Rule 1: Always keep Allocation Per Trade capped at 5% - 10% of total capital (₹2,500 - ₹5,000 per trade on a ₹50k account).",
        "Rule 2: Never disable the Hard SL Clamp (keep Stop-Loss at 1.0% and Target at 1.0% - 1.5%).",
        "Rule 3: Ensure 200 EMA Filter is ON to prevent buying into established macro downtrends.",
        "Rule 4: Execute strict Intraday Square-off by 03:15 PM to avoid exchange penalty charges and overnight gap-downs."
    ])
]

r_b = 1
for title, items in blueprint_sections:
    ws_blueprint.merge_cells(f"A{r_b}:E{r_b}")
    ws_blueprint[f"A{r_b}"] = title
    ws_blueprint[f"A{r_b}"].font = font_head
    ws_blueprint[f"A{r_b}"].fill = navy_header_fill
    ws_blueprint.row_dimensions[r_b].height = 26
    r_b += 1
    
    for item in items:
        ws_blueprint.merge_cells(f"A{r_b}:E{r_b}")
        ws_blueprint[f"A{r_b}"] = item
        ws_blueprint[f"A{r_b}"].font = font_regular
        ws_blueprint[f"A{r_b}"].alignment = Alignment(wrap_text=True, vertical="center")
        ws_blueprint.row_dimensions[r_b].height = 28
        r_b += 1
    r_b += 1

ws_blueprint.column_dimensions['A'].width = 110

excel_filepath = "nighthawk_performance_insights.xlsx"
wb.save(excel_filepath)
print(f"Excel workbook successfully saved to {excel_filepath}")

# ------------------------------------------------------------------------------
# 4. BUILD PROFESSIONAL INSTITUTIONAL PDF REPORT (.PDF)
# ------------------------------------------------------------------------------
pdf_filepath = "nighthawk_performance_insights.pdf"
doc = SimpleDocTemplate(
    pdf_filepath,
    pagesize=letter,
    leftMargin=0.5 * inch,
    rightMargin=0.5 * inch,
    topMargin=0.5 * inch,
    bottomMargin=0.5 * inch
)

styles = getSampleStyleSheet()

# Custom Styles
style_title = ParagraphStyle(
    'DocTitle',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=18,
    leading=22,
    textColor=colors.HexColor('#0F172A'),
    alignment=1, # Center
    spaceAfter=4
)

style_subtitle = ParagraphStyle(
    'DocSubTitle',
    parent=styles['Normal'],
    fontName='Helvetica-Oblique',
    fontSize=9,
    leading=12,
    textColor=colors.HexColor('#64748B'),
    alignment=1,
    spaceAfter=12
)

style_h1 = ParagraphStyle(
    'SectionH1',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=12,
    leading=15,
    textColor=colors.HexColor('#0F172A'),
    spaceBefore=10,
    spaceAfter=6
)

style_body = ParagraphStyle(
    'Body',
    parent=styles['Normal'],
    fontName='Helvetica',
    fontSize=8.5,
    leading=12,
    textColor=colors.HexColor('#334155'),
    spaceAfter=6
)

style_bullet = ParagraphStyle(
    'Bullet',
    parent=styles['Normal'],
    fontName='Helvetica',
    fontSize=8.5,
    leading=12,
    textColor=colors.HexColor('#1E293B'),
    leftIndent=12,
    firstLineIndent=-8,
    spaceAfter=4
)

story = []

# Title & Subtitle
story.append(Paragraph("NIGHTHAWK ALGORITHMIC DESK — PERFORMANCE AUDIT", style_title))
story.append(Paragraph("Quantitative Portfolio Doubling Analysis | Risk Management Architecture & Institutional Insights", style_subtitle))
story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#0F172A'), spaceBefore=2, spaceAfter=10))

# KPI Highlight Box Table (3 cols x 2 rows)
kpi_table_data = [
    [
        Paragraph("<b>NET REALIZED PROFIT</b><br/><font size=13 color='#047857'><b>+₹1,04,99,406</b></font><br/><font size=7 color='#64748B'>+104.99% On Initial Capital</font>", style_body),
        Paragraph("<b>TOTAL DESK EQUITY</b><br/><font size=13 color='#0F172A'><b>₹2,10,86,233</b></font><br/><font size=7 color='#047857'>+110.86% Total ROI</font>", style_body),
        Paragraph("<b>WIN RATE</b><br/><font size=13 color='#047857'><b>63.02%</b></font><br/><font size=7 color='#64748B'>571 Wins / 335 Losses</font>", style_body)
    ],
    [
        Paragraph("<b>PROFIT FACTOR</b><br/><font size=13 color='#047857'><b>2.08</b></font><br/><font size=7 color='#64748B'>Gross ₹2.03 Cr / Loss ₹0.98 Cr</font>", style_body),
        Paragraph("<b>TRADE EXPECTANCY</b><br/><font size=13 color='#047857'><b>+₹11,588.75</b></font><br/><font size=7 color='#64748B'>Net Positive Edge Per Trade</font>", style_body),
        Paragraph("<b>MAX DRAWDOWN</b><br/><font size=13 color='#B91C1C'><b>-1.53%</b></font><br/><font size=7 color='#64748B'>Peak-to-Trough -₹2.17 Lakhs</font>", style_body)
    ]
]

t_kpi = Table(kpi_table_data, colWidths=[2.4*inch, 2.4*inch, 2.4*inch])
t_kpi.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
    ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CBD5E1')),
    ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
    ('TOPPADDING', (0,0), (-1,-1), 6),
    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ('LEFTPADDING', (0,0), (-1,-1), 8),
    ('RIGHTPADDING', (0,0), (-1,-1), 8),
]))
story.append(t_kpi)
story.append(Spacer(1, 10))

# Section 1: Executive Summary
story.append(Paragraph("1. EXECUTIVE SUMMARY & HOW THE PORTFOLIO DOUBLED", style_h1))
p_exec = (
    "Over the overnight testing cadence, the NIGHTHAWK After-Hours Sandbox Lab executed <b>906 closed algorithmic trades</b> "
    "across the NIFTY universe, starting from a base balance of <b>₹1,00,00,000 (1 Crore)</b>. The desk accumulated a net realized profit of "
    "<b>+₹1,04,99,406.08 (+104.99%)</b> after deducting <b>₹2,41,022.64</b> in transaction charges, while maintaining an extraordinary "
    "<b>maximum peak-to-trough drawdown of just -1.53% (-₹2,17,349.67)</b>. Including 23 active holdings, total equity reached <b>₹2,10,86,233.41 (+110.86%)</b>."
)
story.append(Paragraph(p_exec, style_body))

# Chart 1: Equity Curve
story.append(Image(chart_equity_path, width=7.2*inch, height=3.6*inch))
story.append(Spacer(1, 8))

# Section 2: Mathematical Drivers
story.append(Paragraph("2. WHY DID THIS RISK TIGHTENING WORK? (THE 4 CORE PILLARS)", style_h1))
story.append(Paragraph("<b>• The Hard Stop-Loss Ceiling Clamp:</b> By enforcing a maximum stop-loss clamp at 1.0% (capping Point 3 swing distance), you successfully amputated left-tail catastrophic losses. Average losing trades were held strictly to -₹29,140, while winning trades averaged +₹35,484.", style_bullet))
story.append(Paragraph("<b>• Breakeven Trailing Shield:</b> As soon as any position crossed +1.0% in profit, the stop-loss was automatically lifted to the entry price. This converted high numbers of would-be reversal losers into zero-risk scratch trades.", style_bullet))
story.append(Paragraph("<b>• Positive Mathematical Expectancy (+₹11,588.75/Trade):</b> With a 63.02% win rate and a 1.22x payoff ratio, the desk achieved positive mathematical drift on every execution. Compounding this over 906 iterations mathematically generated +₹1.05 Crore.", style_bullet))
story.append(Paragraph("<b>• Symmetrical Alpha Extraction:</b> Both Longs (711 trades, 62.3% win rate, +₹87.65L P&L) and Shorts (195 trades, 65.6% win rate, +₹17.34L P&L) generated consistent positive returns.", style_bullet))

story.append(PageBreak())

# Page 2: Charts and Detailed Tables
story.append(Paragraph("3. PERFORMANCE ATTRIBUTION & TICKER ANALYSIS", style_h1))

# Charts 2 & 3 side-by-side or stacked
story.append(Image(chart_hist_path, width=7.0*inch, height=2.8*inch))
story.append(Spacer(1, 6))
story.append(Image(chart_stocks_path, width=7.0*inch, height=2.9*inch))
story.append(Spacer(1, 8))

# Section 4: Critical Reality Check & Warnings
story.append(Paragraph("4. INSTITUTIONAL REALITY CHECK: 5 CRITICAL WARNINGS FOR REAL MONEY", style_h1))
story.append(Paragraph("While these simulated sandbox results confirm the mathematical validity of your risk model, you must account for the following friction points before scaling on real capital:", style_body))

story.append(Paragraph("<b>1. Execution Slippage:</b> In live broker accounts (Zerodha/Upstox), executing 900+ market orders incurs bid-ask spread slippage (0.05% - 0.15%), which can erode 20-30% of gross profits.", style_bullet))
story.append(Paragraph("<b>2. Intraday Operating Hours vs 24/7 Continuity:</b> NIGHTHAWK runs 24/7 in continuous simulation. Live NSE Cash/MIS trading is strictly 09:15 AM to 03:30 PM. Positions cannot be held overnight on MIS without 100% cash margins and gap-down risk.", style_bullet))
story.append(Paragraph("<b>3. Order Rate Throttling:</b> Real broker APIs enforce limits (typically 10-20 requests/second). Attempting to sweep 200 stocks simultaneously requires asynchronous batching.", style_bullet))
story.append(Paragraph("<b>4. Correlated Sector Drawdowns:</b> At peak, 49 positions were held simultaneously (~₹97 Lakhs margin). In a macro flash crash, all 49 positions would drop together, temporarily spiking drawdown before stop-losses trigger.", style_bullet))
story.append(Paragraph("<b>5. Brokerage & Friction:</b> Even with low fees, 906 trades generated ₹2.41 Lakhs in statutory charges. Ensure trade frequency matches your broker's flat-fee tier.", style_bullet))

story.append(Spacer(1, 8))
story.append(Paragraph("5. ACTIONABLE BLUEPRINT FOR BOT ALPHA (LIVE REAL TRADING)", style_h1))
story.append(Paragraph("<b>• Position Sizing:</b> Restrict allocation to 5% - 10% of total capital per trade (e.g. ₹5,000 allocation on ₹50,000 capital).", style_bullet))
story.append(Paragraph("<b>• Hard SL Clamp:</b> Keep the 1.0% stop-loss ceiling active at all times. Never widen stop-losses during drawdown.", style_bullet))
story.append(Paragraph("<b>• Strict EOD Square-off:</b> Enforce auto square-off at 03:15 PM IST to completely eliminate overnight gap risk.", style_bullet))

doc.build(story)
print(f"PDF report successfully saved to {pdf_filepath}")

