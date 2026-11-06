from flask import Blueprint, request, jsonify
from app import db
from app.models.trading import Account, WatchlistItem, TradeHistory, Position, CryptoAccount, CryptoPosition, CryptoTradeHistory
from app.services.market_data import MarketDataService
from app.services.paper_trader import PaperTradingEngine
from app.services.crypto_trader import CryptoTradingEngine
from app.services.strategy import StrategyBacktester

api_bp = Blueprint('api', __name__)

@api_bp.route('/quote/<symbol>', methods=['GET'])
def get_quote(symbol):
    data = MarketDataService.get_quote(symbol)
    if "error" in data:
        return jsonify(data), 404
    return jsonify(data)

@api_bp.route('/candles/<symbol>', methods=['GET'])
def get_candles(symbol):
    period = request.args.get('period', '1mo')
    interval = request.args.get('interval', '1d')
    candles = MarketDataService.get_historical_candles(symbol, period, interval)
    return jsonify({"symbol": symbol.upper(), "candles": candles})

@api_bp.route('/portfolio', methods=['GET'])
def get_portfolio():
    summary = PaperTradingEngine.get_portfolio_summary()
    return jsonify(summary)

@api_bp.route('/order', methods=['POST'])
def place_order():
    body = request.get_json() or {}
    symbol = body.get('symbol', '')
    side = body.get('side', '')
    quantity = body.get('quantity', 0)
    
    result = PaperTradingEngine.execute_order(symbol, side, quantity)
    if not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)

@api_bp.route('/trades', methods=['GET'])
def get_trades():
    trades = TradeHistory.query.order_by(TradeHistory.timestamp.desc()).limit(100).all()
    res = []
    for t in trades:
        cost_basis = t.total - (t.realized_pnl or 0.0)
        roi_pct = round(((t.realized_pnl or 0.0) / cost_basis) * 100, 2) if (cost_basis > 0 and t.side == "SELL") else 0.0
        entry_p = round(cost_basis / t.quantity, 2) if (t.quantity > 0 and cost_basis > 0) else t.price
        charges = round(getattr(t, 'charges', 0.0) or 0.0, 2)
        res.append({
            "id": t.id,
            "symbol": t.symbol,
            "displayName": t.symbol.replace('.NS', ''),
            "side": t.side,
            "quantity": t.quantity,
            "price": t.price,
            "entry_price": entry_p,
            "total": t.total,
            "charges": charges,
            "brokerage": round(getattr(t, 'brokerage', 0.0) or 0.0, 2),
            "stt": round(getattr(t, 'stt', 0.0) or 0.0, 2),
            "exchange_charges": round(getattr(t, 'exchange_charges', 0.0) or 0.0, 2),
            "sebi_charges": round(getattr(t, 'sebi_charges', 0.0) or 0.0, 2),
            "gst": round(getattr(t, 'gst', 0.0) or 0.0, 2),
            "stamp_duty": round(getattr(t, 'stamp_duty', 0.0) or 0.0, 2),
            "net_total": round(getattr(t, 'net_total', 0.0) or (t.total + charges if t.side == 'BUY' else t.total - charges), 2),
            "realized_pnl": t.realized_pnl or 0.0,
            "roi_pct": roi_pct,
            "timestamp": t.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        })
    return jsonify(res)

@api_bp.route('/trades/closed', methods=['GET'])
def get_closed_trades():
    trades = TradeHistory.query.filter_by(side="SELL").order_by(TradeHistory.timestamp.desc()).all()
    closed = []
    total_pnl = 0.0
    total_charges = 0.0
    wins = 0
    losses = 0

    for t in trades:
        pnl = t.realized_pnl or 0.0
        charges = round(getattr(t, 'charges', 0.0) or 0.0, 2)
        total_pnl += pnl
        total_charges += charges
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

        cost_basis = t.total - pnl
        roi_pct = round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0
        entry_p = round(cost_basis / t.quantity, 2) if (t.quantity > 0 and cost_basis > 0) else t.price

        closed.append({
            "id": t.id,
            "symbol": t.symbol,
            "displayName": t.symbol.replace('.NS', ''),
            "quantity": t.quantity,
            "entry_price": entry_p,
            "exit_price": t.price,
            "total_value": t.total,
            "charges": charges,
            "brokerage": round(getattr(t, 'brokerage', 0.0) or 0.0, 2),
            "stt": round(getattr(t, 'stt', 0.0) or 0.0, 2),
            "exchange_charges": round(getattr(t, 'exchange_charges', 0.0) or 0.0, 2),
            "sebi_charges": round(getattr(t, 'sebi_charges', 0.0) or 0.0, 2),
            "gst": round(getattr(t, 'gst', 0.0) or 0.0, 2),
            "stamp_duty": round(getattr(t, 'stamp_duty', 0.0) or 0.0, 2),
            "net_proceeds": round(getattr(t, 'net_total', 0.0) or (t.total - charges), 2),
            "realized_pnl": round(pnl, 2),
            "roi_pct": roi_pct,
            "is_win": pnl >= 0,
            "timestamp": t.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        })

    win_rate = round((wins / len(closed)) * 100, 1) if closed else 0.0

    return jsonify({
        "trades": closed,
        "summary": {
            "total_closed": len(closed),
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "net_realized_pnl": round(total_pnl, 2),
            "total_charges": round(total_charges, 2),
            "avg_trade_pnl": round(total_pnl / len(closed), 2) if closed else 0.0
        }
    })

@api_bp.route('/watchlist', methods=['GET'])
def get_watchlist():
    items = WatchlistItem.query.all()
    results = []
    for item in items:
        quote = MarketDataService.get_quote(item.symbol)
        results.append(quote if "error" not in quote else {"symbol": item.symbol, "error": quote["error"]})
    return jsonify(results)

@api_bp.route('/watchlist', methods=['POST'])
def add_watchlist():
    body = request.get_json() or {}
    symbol = body.get('symbol', '').strip().upper()
    if not symbol:
        return jsonify({"error": "Symbol is required"}), 400
        
    existing = WatchlistItem.query.filter_by(symbol=symbol).first()
    if existing:
        return jsonify({"message": f"{symbol} already in watchlist"})
        
    item = WatchlistItem(symbol=symbol)
    db.session.add(item)
    db.session.commit()
    return jsonify({"success": True, "message": f"Added {symbol} to watchlist"})

@api_bp.route('/watchlist/<symbol>', methods=['DELETE'])
def remove_watchlist(symbol):
    symbol = symbol.strip().upper()
    item = WatchlistItem.query.filter_by(symbol=symbol).first()
    if item:
        db.session.delete(item)
        db.session.commit()
        return jsonify({"success": True, "message": f"Removed {symbol}"})
    return jsonify({"error": "Symbol not found"}), 404

@api_bp.route('/backtest', methods=['POST'])
def backtest():
    body = request.get_json() or {}
    symbol = body.get('symbol', 'AAPL')
    short_w = int(body.get('short_window', 20))
    long_w = int(body.get('long_window', 50))
    period = body.get('period', '1y')
    capital = float(body.get('capital', 10000.0))
    
    result = StrategyBacktester.sma_crossover(symbol, short_w, long_w, period, capital)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)

@api_bp.route('/reset-account', methods=['POST'])
def reset_account():
    Position.query.delete()
    TradeHistory.query.delete()
    WatchlistItem.query.delete()
    from app.models.trading import NIFTY_50_DEFAULT
    for s in NIFTY_50_DEFAULT:
        db.session.add(WatchlistItem(symbol=s))
    account = Account.query.first()
    if account:
        account.cash_balance = 1000000.0
    else:
        account = Account(cash_balance=1000000.0)
        db.session.add(account)
    db.session.commit()

    from app.services.bot import bot_instance
    bot_instance.logs = []
    bot_instance.last_scan_summary = {
        "total_scanned": 0,
        "buys_found": 0,
        "sells_found": 0,
        "trades_executed": 0,
        "scanner_results": []
    }
    return jsonify({
        "success": True, 
        "message": "Account reset to ₹10,00,000 and NIFTY 50 watchlist restored",
        "cash_balance": 1000000.0,
        "total_equity": 1000000.0
    })

@api_bp.route('/bot/status', methods=['GET'])
def get_bot_status():
    from app.services.bot import bot_instance
    return jsonify(bot_instance.get_status())

@api_bp.route('/bot/start', methods=['POST'])
def start_bot():
    from app.services.bot import bot_instance
    config = request.get_json() or {}
    res = bot_instance.start(config)
    return jsonify(res)

@api_bp.route('/bot/stop', methods=['POST'])
def stop_bot():
    from app.services.bot import bot_instance
    res = bot_instance.stop()
    return jsonify(res)

@api_bp.route('/bot/config', methods=['POST'])
def update_bot_config():
    from app.services.bot import bot_instance
    config = request.get_json(silent=True) or {}
    bot_instance.update_config(config)
    return jsonify({
        "status": "config_updated",
        "bot_state": bot_instance.get_status()
    })

@api_bp.route('/bot/tick', methods=['POST'])
def tick_bot():
    from app.services.bot import bot_instance
    body = request.get_json(silent=True) or {}
    cfg = body.get("config") or body
    if cfg and isinstance(cfg, dict):
        bot_instance.update_config(cfg)

    # Honor client's active status so serverless instances know the bot is active
    client_running = body.get('is_running')
    if client_running is None and isinstance(cfg, dict):
        client_running = cfg.get('is_running')
    if client_running is not None:
        bot_instance.is_running = bool(client_running)

    event = body.get('event')
    symbol = body.get('symbol')
    res = bot_instance.evaluate_signals(simulation_event=event, target_symbol=symbol)
    return jsonify({
        "status": "evaluated",
        "result": res,
        "bot_state": bot_instance.get_status()
    })

@api_bp.route('/bot/simulate', methods=['POST'])
def simulate_bot():
    from app.services.bot import bot_instance
    body = request.get_json(silent=True) or {}
    if "strategy" in body or "config" in body:
        cfg = body.get("config") or body
        bot_instance.update_config(cfg)
    event = body.get('event', 'RANDOM_WALK')
    symbol = body.get('symbol')
    res = bot_instance.evaluate_signals(simulation_event=event, target_symbol=symbol)
    return jsonify({
        "status": "simulated",
        "event": event,
        "result": res,
        "bot_state": bot_instance.get_status()
    })

@api_bp.route('/replay/data', methods=['GET'])
def get_replay_data():
    symbol = request.args.get('symbol', 'RELIANCE.NS').strip().upper()
    interval = request.args.get('interval', '5m')
    range_val = request.args.get('range', '5d')
    from app.services.market_data import MarketDataService
    data = MarketDataService.get_replay_data(symbol, range_val, interval)
    if "error" in data:
        return jsonify(data), 404
    return jsonify(data)

# ==========================================
# CRYPTO TRADING API ENDPOINTS
# ==========================================
@api_bp.route('/crypto/markets', methods=['GET'])
def get_crypto_markets():
    markets = CryptoTradingEngine.get_crypto_markets()
    return jsonify(markets)

@api_bp.route('/crypto/portfolio', methods=['GET'])
def get_crypto_portfolio():
    summary = CryptoTradingEngine.get_portfolio_summary()
    return jsonify(summary)

@api_bp.route('/crypto/order', methods=['POST'])
def place_crypto_order():
    body = request.get_json(silent=True) or {}
    symbol = body.get('symbol', 'BTC-USD')
    side = body.get('side', 'BUY')
    quantity = float(body.get('quantity', 0.0))
    leverage = int(body.get('leverage', 1))
    order_type = body.get('order_type', 'MARKET')
    limit_price = float(body.get('limit_price', 0.0))
    stop_loss = float(body.get('stop_loss', 0.0))
    take_profit = float(body.get('take_profit', 0.0))

    res = CryptoTradingEngine.execute_order(
        symbol=symbol,
        side=side,
        quantity=quantity,
        leverage=leverage,
        order_type=order_type,
        limit_price=limit_price,
        stop_loss=stop_loss,
        take_profit=take_profit
    )
    if not res.get("success"):
        return jsonify(res), 400
    return jsonify(res)

@api_bp.route('/crypto/close/<int:position_id>', methods=['POST'])
def close_crypto_position(position_id):
    res = CryptoTradingEngine.close_position_by_id(position_id)
    if not res.get("success"):
        return jsonify(res), 400
    return jsonify(res)

@api_bp.route('/crypto/trades', methods=['GET'])
def get_crypto_trades():
    trades = CryptoTradeHistory.query.order_by(CryptoTradeHistory.timestamp.desc()).limit(100).all()
    res = []
    for t in trades:
        res.append({
            "id": t.id,
            "symbol": t.symbol,
            "displayName": t.symbol.replace('-USD', ''),
            "side": t.side,
            "quantity": round(float(t.quantity), 6),
            "price": round(float(t.price), 4 if t.price < 1 else 2),
            "leverage": t.leverage,
            "total": round(float(t.total), 2),
            "margin": round(float(t.margin), 2),
            "fee": round(float(t.fee), 4),
            "realized_pnl": round(float(t.realized_pnl or 0.0), 2),
            "timestamp": t.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        })
    return jsonify(res)

@api_bp.route('/crypto/reset', methods=['POST'])
def reset_crypto_account():
    res = CryptoTradingEngine.reset_account()
    return jsonify(res)

# ==========================================
# CRYPTO AUTO-TRADING BOT API ENDPOINTS
# ==========================================
@api_bp.route('/crypto/bot/status', methods=['GET'])
def get_crypto_bot_status():
    from app.services.crypto_bot import crypto_bot_instance
    return jsonify(crypto_bot_instance.get_status())

@api_bp.route('/crypto/bot/config', methods=['POST'])
def update_crypto_bot_config():
    from app.services.crypto_bot import crypto_bot_instance
    body = request.get_json(silent=True) or {}
    crypto_bot_instance.update_config(body)
    return jsonify({"success": True, "status": crypto_bot_instance.get_status()})

@api_bp.route('/crypto/bot/toggle', methods=['POST'])
def toggle_crypto_bot():
    from app.services.crypto_bot import crypto_bot_instance
    body = request.get_json(silent=True) or {}
    start_req = body.get('start', not crypto_bot_instance.is_running)
    cfg = body.get('config')
    if start_req:
        res = crypto_bot_instance.start(cfg)
    else:
        res = crypto_bot_instance.stop()
    return jsonify(res)

@api_bp.route('/crypto/bot/scan', methods=['POST'])
def scan_crypto_bot():
    from app.services.crypto_bot import crypto_bot_instance
    body = request.get_json(silent=True) or {}
    cfg = body.get('config')
    if cfg:
        crypto_bot_instance.update_config(cfg)
    target = body.get('symbol')
    results = crypto_bot_instance.evaluate_signals(target_symbol=target)
    return jsonify({
        "success": True,
        "scanned": len(results),
        "results": results,
        "bot_state": crypto_bot_instance.get_status()
    })

@api_bp.route('/crypto/bot/tick', methods=['POST'])
def tick_crypto_bot():
    from app.services.crypto_bot import crypto_bot_instance
    body = request.get_json(silent=True) or {}
    cfg = body.get("config") or body
    if cfg and isinstance(cfg, dict):
        crypto_bot_instance.update_config(cfg)

    client_running = body.get('is_running')
    if client_running is None and isinstance(cfg, dict):
        client_running = cfg.get('is_running')
    if client_running is not None:
        crypto_bot_instance.is_running = bool(client_running)

    target = body.get('symbol')
    results = crypto_bot_instance.evaluate_signals(target_symbol=target)
    return jsonify({
        "status": "evaluated",
        "result": results,
        "bot_state": crypto_bot_instance.get_status()
    })

@api_bp.route('/crypto/bot/scanner', methods=['GET'])
def get_crypto_scanner_results():
    from app.services.crypto_bot import crypto_bot_instance
    if not crypto_bot_instance.scanner_results:
        crypto_bot_instance.evaluate_signals()
    return jsonify(crypto_bot_instance.scanner_results)


