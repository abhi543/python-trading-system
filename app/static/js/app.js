let activeSymbol = 'AAPL';
let activePeriod = '1mo';
let activeInterval = '1d';
let chart = null;
let candleSeries = null;
let volumeSeries = null;
let backtestChart = null;
let backtestLineSeries = null;
let currentQuotePrice = 0.0;

document.addEventListener('DOMContentLoaded', () => {
    initChart();
    setupEventListeners();
    loadWatchlist();
    loadPortfolio();
    loadTrades();
    selectSymbol(activeSymbol);

    // Periodic portfolio & quote refresh every 15s
    setInterval(() => {
        loadPortfolio();
        refreshActiveQuote();
    }, 15000);
});

/* --- CHART INITIALIZATION --- */
function initChart() {
    const container = document.getElementById('chart-container');
    if (!container) return;

    chart = LightweightCharts.createChart(container, {
        layout: {
            background: { color: '#161b22' },
            textColor: '#8b949e',
        },
        grid: {
            vertLines: { color: 'rgba(48, 54, 61, 0.4)' },
            horzLines: { color: 'rgba(48, 54, 61, 0.4)' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: '#30363d',
        },
        timeScale: {
            borderColor: '#30363d',
            timeVisible: true,
        },
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#00c087',
        downColor: '#ff3b69',
        borderDownColor: '#ff3b69',
        borderUpColor: '#00c087',
        wickDownColor: '#ff3b69',
        wickUpColor: '#00c087',
    });

    volumeSeries = chart.addHistogramSeries({
        color: '#26a69a',
        priceFormat: {
            type: 'volume',
        },
        priceScaleId: '', // overlay
        scaleMargins: {
            top: 0.8,
            bottom: 0,
        },
    });

    // Responsive resize
    window.addEventListener('resize', () => {
        if (chart && container) {
            chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
        }
    });
}

/* --- LOAD CANDLES --- */
async function loadCandles(symbol, period, interval) {
    try {
        const res = await fetch(`/api/candles/${symbol}?period=${period}&interval=${interval}`);
        const data = await res.json();
        if (data.candles && data.candles.length > 0) {
            candleSeries.setData(data.candles);
            
            const volumeData = data.candles.map(c => ({
                time: c.time,
                value: c.volume,
                color: c.close >= c.open ? 'rgba(0, 192, 135, 0.4)' : 'rgba(255, 59, 105, 0.4)'
            }));
            volumeSeries.setData(volumeData);
            chart.timeScale().fitContent();
        }
    } catch (err) {
        console.error('Error fetching candles:', err);
    }
}

/* --- TICKER SELECTION & QUOTE --- */
async function selectSymbol(symbol) {
    activeSymbol = symbol.toUpperCase();
    document.getElementById('active-symbol').innerText = activeSymbol;
    document.getElementById('order-symbol').value = activeSymbol;
    updateOrderButton();

    // Highlight watchlist item
    document.querySelectorAll('.watchlist-row').forEach(row => {
        row.classList.toggle('active', row.dataset.symbol === activeSymbol);
    });

    await refreshActiveQuote();
    await loadCandles(activeSymbol, activePeriod, activeInterval);
}

async function refreshActiveQuote() {
    try {
        const res = await fetch(`/api/quote/${activeSymbol}`);
        const data = await res.json();
        if (data.price) {
            currentQuotePrice = data.price;
            document.getElementById('active-price').innerText = `$${data.price.toFixed(2)}`;
            
            const changeEl = document.getElementById('active-change');
            const sign = data.change >= 0 ? '+' : '';
            changeEl.innerText = `${sign}${data.change.toFixed(2)} (${sign}${data.percent_change.toFixed(2)}%)`;
            changeEl.className = `change-display ${data.change >= 0 ? 'text-green' : 'text-red'}`;

            document.getElementById('order-summary-price').innerText = `$${data.price.toFixed(2)}`;
            calculateOrderTotal();
        }
    } catch (err) {
        console.error('Error refreshing quote:', err);
    }
}

function calculateOrderTotal() {
    const qty = parseFloat(document.getElementById('order-qty').value) || 0;
    const total = qty * currentQuotePrice;
    document.getElementById('order-summary-total').innerText = `$${total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/* --- WATCHLIST --- */
async function loadWatchlist() {
    const container = document.getElementById('watchlist-items');
    try {
        const res = await fetch('/api/watchlist');
        const list = await res.json();
        
        container.innerHTML = '';
        list.forEach(item => {
            const row = document.createElement('div');
            row.className = `watchlist-row ${item.symbol === activeSymbol ? 'active' : ''}`;
            row.dataset.symbol = item.symbol;
            
            const priceText = item.price ? `$${item.price.toFixed(2)}` : 'N/A';
            const changeText = item.percent_change ? `${item.percent_change >= 0 ? '+' : ''}${item.percent_change.toFixed(2)}%` : '--';
            const colorClass = item.percent_change >= 0 ? 'text-green' : 'text-red';

            row.innerHTML = `
                <div class="wl-symbol">${item.symbol}</div>
                <div class="wl-quote">
                    <div class="wl-price">${priceText}</div>
                    <div class="wl-change ${colorClass}">${changeText}</div>
                </div>
            `;

            row.addEventListener('click', () => selectSymbol(item.symbol));
            container.appendChild(row);
        });
    } catch (err) {
        container.innerHTML = `<div class="p-3 text-red">Error loading watchlist</div>`;
    }
}

/* --- PORTFOLIO & POSITIONS --- */
async function loadPortfolio() {
    try {
        const res = await fetch('/api/portfolio');
        const data = await res.json();

        // Update nav metrics
        document.getElementById('nav-total-equity').innerText = `$${data.total_equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        document.getElementById('nav-cash-balance').innerText = `$${data.cash_balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        
        const pnlEl = document.getElementById('nav-pnl');
        const sign = data.unrealized_pnl >= 0 ? '+' : '';
        pnlEl.innerText = `${sign}$${data.unrealized_pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        pnlEl.className = `metric-value ${data.unrealized_pnl >= 0 ? 'text-green' : 'text-red'}`;

        // Populate positions table
        const tbody = document.getElementById('positions-table-body');
        if (!data.positions || data.positions.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center">No open positions. Place a buy order to begin.</td></tr>`;
            return;
        }

        tbody.innerHTML = '';
        data.positions.forEach(pos => {
            const tr = document.createElement('tr');
            const pnlColor = pos.unrealized_pnl >= 0 ? 'text-green' : 'text-red';
            const sign = pos.unrealized_pnl >= 0 ? '+' : '';
            
            tr.innerHTML = `
                <td><strong>${pos.symbol}</strong></td>
                <td>${pos.quantity}</td>
                <td>$${pos.average_price.toFixed(2)}</td>
                <td>$${pos.current_price.toFixed(2)}</td>
                <td>$${pos.market_value.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                <td class="${pnlColor}">${sign}$${pos.unrealized_pnl.toFixed(2)} (${sign}${pos.unrealized_pnl_pct.toFixed(2)}%)</td>
                <td>
                    <button class="btn-sm btn-sell" onclick="closePosition('${pos.symbol}', ${pos.quantity})">Close</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error('Error loading portfolio:', err);
    }
}

/* --- TRADES HISTORY --- */
async function loadTrades() {
    try {
        const res = await fetch('/api/trades');
        const trades = await res.json();
        const tbody = document.getElementById('trades-table-body');
        
        if (!trades || trades.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center">No trade history yet.</td></tr>`;
            return;
        }

        tbody.innerHTML = '';
        trades.forEach(t => {
            const tr = document.createElement('tr');
            const sideColor = t.side === 'BUY' ? 'text-green' : 'text-red';
            const pnlDisplay = t.side === 'SELL' ? `$${t.realized_pnl.toFixed(2)}` : '--';
            const pnlClass = t.realized_pnl >= 0 ? 'text-green' : 'text-red';

            tr.innerHTML = `
                <td>${t.timestamp}</td>
                <td><strong>${t.symbol}</strong></td>
                <td class="${sideColor}"><strong>${t.side}</strong></td>
                <td>${t.quantity}</td>
                <td>$${t.price.toFixed(2)}</td>
                <td>$${t.total.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                <td class="${t.side === 'SELL' ? pnlClass : ''}">${pnlDisplay}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error('Error loading trades:', err);
    }
}

/* --- CLOSE POSITION ACTION --- */
window.closePosition = async function(symbol, quantity) {
    if (!confirm(`Sell all ${quantity} shares of ${symbol} at market price?`)) return;
    
    try {
        const res = await fetch('/api/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, side: 'SELL', quantity })
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, 'success');
            loadPortfolio();
            loadTrades();
        } else {
            showToast(data.message || 'Trade failed', 'error');
        }
    } catch (err) {
        showToast('Execution error', 'error');
    }
};

/* --- EVENT LISTENERS --- */
function setupEventListeners() {
    // Timeframe selector
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            activePeriod = e.target.dataset.period;
            activeInterval = e.target.dataset.interval;
            loadCandles(activeSymbol, activePeriod, activeInterval);
        });
    });

    // Side selector (BUY / SELL)
    document.querySelectorAll('input[name="side"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            document.querySelectorAll('.side-btn').forEach(btn => btn.classList.remove('active'));
            e.target.closest('.side-btn').classList.add('active');
            updateOrderButton();
        });
    });

    // Quantity change
    document.getElementById('order-qty').addEventListener('input', calculateOrderTotal);

    // Order submission
    document.getElementById('order-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const side = document.querySelector('input[name="side"]:checked').value;
        const qty = parseFloat(document.getElementById('order-qty').value);

        try {
            const res = await fetch('/api/order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: activeSymbol, side, quantity: qty })
            });
            const data = await res.json();
            if (data.success) {
                showToast(data.message, 'success');
                loadPortfolio();
                loadTrades();
            } else {
                showToast(data.message || 'Order failed', 'error');
            }
        } catch (err) {
            showToast('Order failed: ' + err.message, 'error');
        }
    });

    // Add Watchlist Symbol
    document.getElementById('add-symbol-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const input = document.getElementById('new-symbol-input');
        const symbol = input.value.trim().toUpperCase();
        if (!symbol) return;

        try {
            const res = await fetch('/api/watchlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol })
            });
            const data = await res.json();
            if (data.success) {
                showToast(`Added ${symbol} to watchlist`, 'success');
                input.value = '';
                loadWatchlist();
            } else {
                showToast(data.error || data.message, 'error');
            }
        } catch (err) {
            showToast('Failed to add symbol', 'error');
        }
    });

    // Tabs toggle
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            const target = btn.dataset.tab;
            btn.classList.add('active');
            document.getElementById(target).classList.add('active');
        });
    });

    // Account Reset
    document.getElementById('reset-account-btn').addEventListener('click', async () => {
        if (!confirm('Reset cash back to $100,000 and clear all positions/trades?')) return;
        try {
            const res = await fetch('/api/reset-account', { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                showToast('Account reset successfully', 'success');
                loadPortfolio();
                loadTrades();
            }
        } catch (err) {
            showToast('Reset failed', 'error');
        }
    });

    // Backtest Form
    document.getElementById('backtest-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const symbol = document.getElementById('bt-symbol').value.trim().toUpperCase();
        const short_window = parseInt(document.getElementById('bt-short').value);
        const long_window = parseInt(document.getElementById('bt-long').value);
        const capital = parseFloat(document.getElementById('bt-capital').value);
        const period = document.getElementById('bt-period').value;

        showToast('Running backtest simulation...', 'success');

        try {
            const res = await fetch('/api/backtest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol, short_window, long_window, capital, period })
            });
            const data = await res.json();
            if (data.error) {
                showToast(data.error, 'error');
                return;
            }

            renderBacktestResults(data);
        } catch (err) {
            showToast('Backtest simulation failed', 'error');
        }
    });
}

function updateOrderButton() {
    const side = document.querySelector('input[name="side"]:checked').value;
    const btn = document.getElementById('submit-order-btn');
    btn.className = `btn btn-block ${side === 'BUY' ? 'btn-buy' : 'btn-sell'}`;
    btn.innerText = `${side} ${activeSymbol}`;
}

/* --- BACKTEST RENDERING --- */
function renderBacktestResults(data) {
    document.getElementById('backtest-results').classList.remove('hidden');
    
    const stratRetEl = document.getElementById('bt-res-strategy-return');
    stratRetEl.innerText = `${data.strategy_return_pct >= 0 ? '+' : ''}${data.strategy_return_pct}%`;
    stratRetEl.className = `stat-val ${data.strategy_return_pct >= 0 ? 'text-green' : 'text-red'}`;

    const mktRetEl = document.getElementById('bt-res-market-return');
    mktRetEl.innerText = `${data.benchmark_return_pct >= 0 ? '+' : ''}${data.benchmark_return_pct}%`;
    mktRetEl.className = `stat-val ${data.benchmark_return_pct >= 0 ? 'text-green' : 'text-red'}`;

    document.getElementById('bt-res-ending-equity').innerText = `$${data.final_strategy_equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
    document.getElementById('bt-res-trades').innerText = data.total_trades;

    // Render equity curve chart
    const container = document.getElementById('backtest-chart-container');
    container.innerHTML = '';
    
    backtestChart = LightweightCharts.createChart(container, {
        layout: { background: { color: '#0e1117' }, textColor: '#8b949e' },
        grid: { vertLines: { color: 'rgba(48, 54, 61, 0.3)' }, horzLines: { color: 'rgba(48, 54, 61, 0.3)' } },
        rightPriceScale: { borderColor: '#30363d' },
        timeScale: { borderColor: '#30363d' },
    });

    const stratSeries = backtestChart.addLineSeries({
        color: '#00c087',
        lineWidth: 2,
        title: 'Strategy Equity ($)'
    });

    const benchSeries = backtestChart.addLineSeries({
        color: '#8b949e',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        title: 'Buy & Hold ($)'
    });

    const stratLineData = data.chart.dates.map((d, i) => ({ time: d, value: data.chart.strategy_equity[i] }));
    const benchLineData = data.chart.dates.map((d, i) => ({ time: d, value: data.chart.benchmark_equity[i] }));

    stratSeries.setData(stratLineData);
    benchSeries.setData(benchLineData);
    backtestChart.timeScale().fitContent();
}

/* --- TOAST NOTIFICATIONS --- */
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerText = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.remove();
    }, 4000);
}

/* --- DAY P&L CALENDAR IMPORT HELPERS --- */
const IMPORTED_DAY_PNL_KEY = 'trading_imported_day_pnl_v1';

function getImportedDayPnlList() {
    try {
        const raw = localStorage.getItem(IMPORTED_DAY_PNL_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
        return [];
    }
}

function saveImportedDayPnlList(list) {
    try {
        localStorage.setItem(IMPORTED_DAY_PNL_KEY, JSON.stringify(list || []));
    } catch (e) {}
}

function toggleCalImportDrawer() {
    const drawer = document.getElementById('cal-import-drawer');
    if (!drawer) return;
    drawer.classList.toggle('open');
    const dateInput = document.getElementById('cal-imp-date');
    if (drawer.classList.contains('open') && dateInput && !dateInput.value) {
        const nowIst = new Date(new Date().getTime() + (5.5 * 3600 * 1000));
        dateInput.value = nowIst.toISOString().slice(0, 10);
    }
}

function saveSingleImportedDayPnl() {
    const dateEl = document.getElementById('cal-imp-date');
    const deskEl = document.getElementById('cal-imp-desk');
    const pnlEl = document.getElementById('cal-imp-pnl');
    const tradesEl = document.getElementById('cal-imp-trades');
    const chargesEl = document.getElementById('cal-imp-charges');

    const dateStr = (dateEl?.value || '').trim();
    const pnlVal = parseFloat(pnlEl?.value);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr) || isNaN(pnlVal)) {
        if (typeof showToast === 'function') showToast('Please enter a valid Date (YYYY-MM-DD) and Net P&L.', 'error');
        return;
    }

    const desk = (deskEl?.value || 'ALPHA').toUpperCase();
    const tradesCount = Math.max(1, parseInt(tradesEl?.value || '1', 10) || 1);
    const charges = Math.max(0, parseFloat(chargesEl?.value || '0') || 0);

    const list = getImportedDayPnlList();
    list.push({
        id: 'imp_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
        date: dateStr,
        desk: desk,
        net_pnl: Math.round(pnlVal * 100) / 100,
        trades_count: tradesCount,
        charges: Math.round(charges * 100) / 100,
        timestamp: dateStr + ' 15:30:00'
    });
    saveImportedDayPnlList(list);

    if (pnlEl) pnlEl.value = '';
    if (typeof calSelectedDate !== 'undefined') calSelectedDate = dateStr;
    if (typeof renderCalendarMonth === 'function') renderCalendarMonth();
    if (typeof showToast === 'function') showToast(`Imported ${dateStr} (${desk}): ${pnlVal >= 0 ? '+' : ''}₹${pnlVal.toFixed(2)}`, 'success');
}

function parseAndImportDayPnlLines(rawText) {
    const deskEl = document.getElementById('cal-imp-desk');
    const desk = (deskEl?.value || 'ALPHA').toUpperCase();
    const lines = String(rawText || '').split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const list = getImportedDayPnlList();
    let added = 0;

    lines.forEach(line => {
        if (/^date/i.test(line)) return; // Skip CSV header
        const parts = line.split(/[,\t;]+/).map(s => s.trim());
        if (parts.length < 2) return;
        const dateStr = parts[0];
        const pnlVal = parseFloat(parts[1].replace(/[₹,\s]/g, ''));
        if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr) || isNaN(pnlVal)) return;
        const tradesCount = parts[2] ? Math.max(1, parseInt(parts[2], 10) || 1) : 1;
        const charges = parts[3] ? Math.max(0, parseFloat(parts[3]) || 0) : 0;

        list.push({
            id: 'imp_' + Date.now() + '_' + added + '_' + Math.random().toString(36).slice(2, 6),
            date: dateStr,
            desk: desk,
            net_pnl: Math.round(pnlVal * 100) / 100,
            trades_count: tradesCount,
            charges: Math.round(charges * 100) / 100,
            timestamp: dateStr + ' 15:30:00'
        });
        added++;
    });

    if (added > 0) {
        saveImportedDayPnlList(list);
        if (typeof renderCalendarMonth === 'function') renderCalendarMonth();
        if (typeof showToast === 'function') showToast(`Imported ${added} Day P&L record${added > 1 ? 's' : ''} into calendar.`, 'success');
    } else {
        if (typeof showToast === 'function') showToast('No valid rows found. Use format: YYYY-MM-DD, Net_PnL', 'error');
    }
    return added;
}

function importBulkDayPnl() {
    const bulkEl = document.getElementById('cal-imp-bulk');
    if (!bulkEl || !bulkEl.value.trim()) {
        if (typeof showToast === 'function') showToast('Paste Day P&L rows first (YYYY-MM-DD, PnL).', 'warning');
        return;
    }
    const added = parseAndImportDayPnlLines(bulkEl.value);
    if (added > 0) bulkEl.value = '';
}

function handleCalCsvFile(input) {
    const file = input?.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
        parseAndImportDayPnlLines(e.target.result || '');
        input.value = '';
    };
    reader.readAsText(file);
}

function clearImportedDayPnl() {
    saveImportedDayPnlList([]);
    if (typeof renderCalendarMonth === 'function') renderCalendarMonth();
    if (typeof showToast === 'function') showToast('Cleared all imported Day P&L entries.', 'info');
}

function getMergedCalendarDaysMap(baseDaysMap, deskFilter) {
    const merged = {};
    const src = baseDaysMap || {};
    Object.keys(src).forEach(dStr => {
        const d = src[dStr];
        merged[dStr] = {
            ...d,
            trades: Array.isArray(d.trades) ? [...d.trades] : []
        };
    });

    const filterNorm = String(deskFilter || 'all').toLowerCase();
    const imported = getImportedDayPnlList();
    imported.forEach(item => {
        if (!item || !item.date) return;
        const itemDesk = String(item.desk || 'ALPHA').toLowerCase();
        if (filterNorm !== 'all' && itemDesk !== filterNorm) return;

        if (!merged[item.date]) {
            merged[item.date] = {
                net_pnl: 0.0,
                charges: 0.0,
                trades_count: 0,
                wins: 0,
                win_rate: 0,
                trades: []
            };
        }
        const dayObj = merged[item.date];
        dayObj.net_pnl = Math.round(((dayObj.net_pnl || 0) + item.net_pnl) * 100) / 100;
        dayObj.charges = Math.round(((dayObj.charges || 0) + (item.charges || 0)) * 100) / 100;
        dayObj.trades_count = (dayObj.trades_count || 0) + (item.trades_count || 1);
        if (item.net_pnl >= 0) dayObj.wins = (dayObj.wins || 0) + 1;
        dayObj.win_rate = dayObj.trades_count > 0 ? Math.round((dayObj.wins / dayObj.trades_count) * 100) : 0;
        dayObj.trades.push({
            id: item.id,
            time_str: 'IMPORTED',
            timestamp: item.timestamp,
            desk: item.desk || 'ALPHA',
            displayName: 'DAY P&L IMPORT',
            symbol: 'DAY_PNL',
            side: item.net_pnl >= 0 ? 'BUY' : 'SELL',
            quantity: item.trades_count || 1,
            entry_price: 0,
            exit_price: 0,
            charges: item.charges || 0,
            realized_pnl: item.net_pnl,
            roi_pct: 0,
            is_imported: true
        });
    });

    return merged;
}

