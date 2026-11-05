from flask import Blueprint, request, jsonify
from app import db
from app.models.trading import Account, WatchlistItem, TradeHistory, Position
from app.services.market_data import MarketDataService
from app.services.paper_trader import PaperTradingEngine
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
    trades = TradeHistory.query.order_by(TradeHistory.timestamp.desc()).limit(50).all()
    res = [{
        "id": t.id,
        "symbol": t.symbol,
        "side": t.side,
        "quantity": t.quantity,
        "price": t.price,
        "total": t.total,
        "realized_pnl": t.realized_pnl,
        "timestamp": t.timestamp.strftime("%Y-%m-%d %H:%M:%S")
    } for t in trades]
    return jsonify(res)

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
    db.session.commit()
    return jsonify({"success": True, "message": "Account reset to ₹10,00,000 and NIFTY 50 watchlist restored"})

