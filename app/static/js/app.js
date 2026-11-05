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

