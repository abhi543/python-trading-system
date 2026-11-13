from flask import Blueprint, request, jsonify
from app import db
from app.models.trading import (
    Account, WatchlistItem, TradeHistory, Position,
    AfterHoursAccount, AfterHoursPosition, AfterHoursTradeHistory
)
from app.services.market_data import MarketDataService
from app.services.paper_trader import PaperTradingEngine, AfterHoursPaperTradingEngine
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

@api_bp.route('/nse/stocks', methods=['GET'])
def get_nse_stocks():
    query = request.args.get('q', '').strip().upper()
    limit = int(request.args.get('limit', 100))
    from app.models.nse_universe import _NSE_STOCKS_DATA
    if not query:
        return jsonify({"total": len(_NSE_STOCKS_DATA), "stocks": _NSE_STOCKS_DATA[:limit]})
    matches = [s for s in _NSE_STOCKS_DATA if query in s['ticker'] or query in s['name'].upper()][:limit]
    return jsonify({"total": len(matches), "query": query, "stocks": matches})

def sync_positions_from_client(client_positions, explicit_clear=False):
    if client_positions is None or not isinstance(client_positions, list):
        return
    try:
        from app.models.trading import Position, TradeHistory
        from app import db
        from app.services.paper_trader import PaperTradingEngine
        from app.services.bot import is_nse_market_open, is_intraday_squareoff_time

        # Intraday EOD Guard: After market close (>= 15:20 IST), all intraday positions must be flat.
        # Prevent stale client caches from ever resurrecting intraday positions overnight!
        if is_intraday_squareoff_time() or not is_nse_market_open():
            if Position.query.count() > 0:
                Position.query.delete()
                db.session.commit()
                PaperTradingEngine.reconcile_account_balance()
            return

        if len(client_positions) == 0:
            if explicit_clear:
                Position.query.delete()
                db.session.commit()
                PaperTradingEngine.reconcile_account_balance()
            return

        existing = {p.symbol: p for p in Position.query.all()}
        client_symbols = set()
        for cp in client_positions:
            sym = cp.get('symbol')
            if not sym:
                continue

            # Anti-resurrection guard: if this symbol was already exited (latest trade is SELL/COVER),
            # never allow stale client state to recreate it as open!
            latest_trade = TradeHistory.query.filter_by(symbol=sym).order_by(TradeHistory.timestamp.desc(), TradeHistory.id.desc()).first()
            if latest_trade and latest_trade.side in ("SELL", "COVER"):
                if sym in existing:
                    db.session.delete(existing[sym])
                continue

            client_symbols.add(sym)
            p = existing.get(sym)
            if not p:
                p = Position(
                    symbol=sym,
                    side=cp.get('side', 'LONG'),
                    quantity=float(cp.get('quantity', 0)),
                    average_price=float(cp.get('average_price', 0)),
                    peak_price=float(cp.get('peak_price', 0) or cp.get('average_price', 0)),
                    stop_loss=float(cp.get('stop_loss', 0)),
                    take_profit=float(cp.get('take_profit', 0)),
                    trailing_active=bool(cp.get('trailing_active', False))
                )
                db.session.add(p)
            else:
                p.side = cp.get('side', p.side)
                p.quantity = float(cp.get('quantity', p.quantity))
                p.average_price = float(cp.get('average_price', p.average_price))
        
        # Prune only if client provided a non-empty list of symbols
        if len(client_symbols) > 0:
            for sym, p in existing.items():
                if sym not in client_symbols:
                    db.session.delete(p)

        db.session.commit()
        PaperTradingEngine.reconcile_account_balance()
    except Exception:
        pass

def sync_after_hours_positions_from_client(client_positions, explicit_clear=False):
    if client_positions is None or not isinstance(client_positions, list):
        return
    try:
        from app.models.trading import AfterHoursPosition
        from app import db
        from app.services.paper_trader import AfterHoursPaperTradingEngine

        if len(client_positions) == 0:
            if explicit_clear:
                AfterHoursPosition.query.delete()
                db.session.commit()
                AfterHoursPaperTradingEngine.reconcile_account_balance()
            return

        existing = {p.symbol: p for p in AfterHoursPosition.query.all()}
        client_symbols = set()
        for cp in client_positions:
            sym = cp.get('symbol')
            if not sym:
                continue
            client_symbols.add(sym)
            p = existing.get(sym)
            if not p:
                p = AfterHoursPosition(
                    symbol=sym,
                    side=cp.get('side', 'LONG'),
                    quantity=float(cp.get('quantity', 0)),
                    average_price=float(cp.get('average_price', 0)),
                    peak_price=float(cp.get('peak_price', 0) or cp.get('average_price', 0)),
                    stop_loss=float(cp.get('stop_loss', 0)),
                    take_profit=float(cp.get('take_profit', 0)),
                    trailing_active=bool(cp.get('trailing_active', False))
                )
                db.session.add(p)
            else:
                p.side = cp.get('side', p.side)
                p.quantity = float(cp.get('quantity', p.quantity))
                p.average_price = float(cp.get('average_price', p.average_price))
        
        if len(client_symbols) > 0:
            for sym, p in existing.items():
                if sym not in client_symbols:
                    db.session.delete(p)

        db.session.commit()
        AfterHoursPaperTradingEngine.reconcile_account_balance()
    except Exception:
        pass

def sync_closed_trades_from_client(client_closed_trades):
    if not client_closed_trades or not isinstance(client_closed_trades, list):
        return
    try:
        from app.models.trading import TradeHistory
        from app import db
        from datetime import datetime
        existing_count = TradeHistory.query.count()
        if existing_count == 0 and len(client_closed_trades) > 0:
            for ct in client_closed_trades[:100]:
                ts_str = ct.get("timestamp")
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S") if ts_str else datetime.now()
                except Exception:
                    ts = datetime.now()
                trade = TradeHistory(
                    symbol=ct.get("symbol", "NIFTY"),
                    side=ct.get("side", "SELL"),
                    quantity=float(ct.get("quantity", 0)),
                    price=float(ct.get("price") or ct.get("exit_price") or 0.0),
                    total_value=float(ct.get("total_value", 0.0)),
                    realized_pnl=float(ct.get("realized_pnl", 0.0)),
                    charges=float(ct.get("charges", 0.0)),
                    timestamp=ts
                )
                db.session.add(trade)
            db.session.commit()
    except Exception:
        pass

def sync_after_hours_closed_trades_from_client(client_closed_trades):
    if not client_closed_trades or not isinstance(client_closed_trades, list):
        return
    try:
        from app.models.trading import AfterHoursTradeHistory
        from app import db
        from datetime import datetime
        existing_count = AfterHoursTradeHistory.query.count()
        if existing_count == 0 and len(client_closed_trades) > 0:
            for ct in client_closed_trades[:100]:
                ts_str = ct.get("timestamp")
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S") if ts_str else datetime.now()
                except Exception:
                    ts = datetime.now()
                trade = AfterHoursTradeHistory(
                    symbol=ct.get("symbol", "NIFTY"),
                    side=ct.get("side", "SELL"),
                    quantity=float(ct.get("quantity", 0)),
                    price=float(ct.get("price") or ct.get("exit_price") or 0.0),
                    total_value=float(ct.get("total_value", 0.0)),
                    realized_pnl=float(ct.get("realized_pnl", 0.0)),
                    charges=float(ct.get("charges", 0.0)),
                    timestamp=ts
                )
                db.session.add(trade)
            db.session.commit()
    except Exception:
        pass

@api_bp.route('/portfolio', methods=['GET', 'POST'])
def get_portfolio():
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        client_positions = body.get('client_positions')
        sync_positions_from_client(client_positions)

    summary = PaperTradingEngine.get_portfolio_summary()
    return jsonify(summary)

@api_bp.route('/order', methods=['POST'])
def place_order():
    body = request.get_json(silent=True) or {}
    symbol = body.get('symbol', '')
    side = body.get('side') or body.get('order_type') or ''
    quantity = float(body.get('quantity', 0) or 0)
    avg_price = float(body.get('average_price', 0) or 0)

    # Ensure position exists in current container if exiting
    if side in ("SELL", "COVER") and symbol:
        try:
            pos = Position.query.filter_by(symbol=symbol).first()
            if not pos:
                pos = Position(
                    symbol=symbol,
                    side="SHORT" if side == "COVER" else "LONG",
                    quantity=quantity if quantity > 0 else 1.0,
                    average_price=avg_price if avg_price > 0 else 1.0
                )
                db.session.add(pos)
                db.session.commit()
            elif pos.quantity < quantity:
                pos.quantity = quantity
                db.session.commit()
        except Exception:
            pass

    result = PaperTradingEngine.execute_order(symbol, side, quantity)
    if not result.get("success"):
        return jsonify(result), 400

    # Ensure position is completely expunged from DB if full exit
    if side in ("SELL", "COVER") and result.get("success"):
        try:
            Position.query.filter_by(symbol=symbol).delete()
            db.session.commit()
        except Exception:
            pass
        
    # Build complete closed trade details if this was a position liquidation/cover
    closed_trade = None
    if side in ("SELL", "COVER") and result.get("success"):
        from datetime import datetime
        entry_p = float(result.get("entry_price") or avg_price or result.get("price", 0.0))
        exit_p = float(result.get("price", 0.0))
        pnl = float(result.get("realized_pnl", 0.0))
        cost_basis = round(quantity * (entry_p if entry_p > 0 else 1.0), 2)
        roi_pct = round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0
        chg_dict = result.get("charges") if isinstance(result.get("charges"), dict) else {}
        chg_val = float(chg_dict.get("total_charges", 0.0) if isinstance(chg_dict, dict) else 0.0)
        closed_trade = {
            "id": result.get("trade_id") or int(datetime.now().timestamp() * 1000),
            "symbol": symbol,
            "displayName": symbol.replace('.NS', ''),
            "side": side,
            "quantity": quantity,
            "entry_price": entry_p,
            "exit_price": exit_p,
            "price": exit_p,
            "total_value": round(quantity * exit_p, 2),
            "charges": chg_val,
            "brokerage": round(float(chg_dict.get("brokerage", 0.0)), 2),
            "stt": round(float(chg_dict.get("stt", 0.0)), 2),
            "exchange_charges": round(float(chg_dict.get("exchange_charges", 0.0)), 2),
            "sebi_charges": round(float(chg_dict.get("sebi_charges", 0.0)), 2),
            "gst": round(float(chg_dict.get("gst", 0.0)), 2),
            "stamp_duty": round(float(chg_dict.get("stamp_duty", 0.0)), 2),
            "net_proceeds": round((quantity * exit_p) - chg_val, 2),
            "realized_pnl": pnl,
            "roi_pct": roi_pct,
            "is_win": pnl >= 0,
            "action": side,
            "signal": "MANUAL_EXIT",
            "reason": f"Manual {side} exit by operator",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        result["closed_trade"] = closed_trade

    # Record manual exit or entry into bot session report if active
    try:
        from app.services.bot import bot_instance
        from datetime import datetime
        if closed_trade:
            bot_instance.record_session_trade(closed_trade)
        else:
            bot_instance.record_session_trade({
                "symbol": symbol,
                "displayName": symbol.replace('.NS', ''),
                "side": side,
                "quantity": quantity,
                "entry_price": result.get("entry_price", result.get("price", 0.0)),
                "exit_price": result.get("price", 0.0) if side == "SELL" else None,
                "price": result.get("price", 0.0),
                "total_value": round(quantity * (result.get("price") or 0.0), 2),
                "charges": result.get("charges", {}).get("total_charges", 0.0) if isinstance(result.get("charges"), dict) else 0.0,
                "realized_pnl": result.get("realized_pnl", 0.0),
                "roi_pct": 0.0,
                "action": side,
                "signal": "MANUAL_ORDER",
                "reason": f"Manual {side} order executed by operator",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
    except Exception:
        pass

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

def _build_closed_trades_summary(trades):
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

        cost_basis = (t.total + pnl) if t.side == 'COVER' else (t.total - pnl)
        roi_pct = round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0
        entry_p = round(cost_basis / t.quantity, 2) if (t.quantity > 0 and cost_basis > 0) else t.price

        closed.append({
            "id": t.id,
            "symbol": t.symbol,
            "displayName": t.symbol.replace('.NS', ''),
            "side": t.side,
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

    return {
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
    }

@api_bp.route('/trades/closed', methods=['GET'])
def get_closed_trades():
    trades = TradeHistory.query.filter(TradeHistory.side.in_(["SELL", "COVER"])).order_by(TradeHistory.timestamp.desc()).all()
    return jsonify(_build_closed_trades_summary(trades))

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
    WatchlistItem.query.delete()
    from app.models.trading import NIFTY_50_DEFAULT
    for s in NIFTY_50_DEFAULT:
        db.session.add(WatchlistItem(symbol=s))
    account = Account.query.first()
    if account:
        account.cash_balance = 50000.0
    else:
        account = Account(cash_balance=50000.0)
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
    bot_instance.start_new_session()
    return jsonify({
        "success": True, 
        "message": "Account reset to ₹50,000 cash capital and NIFTY 50 watchlist restored",
        "cash_balance": 50000.0,
        "total_equity": 50000.0
    })

@api_bp.route('/positions/exit-all', methods=['POST'])
def exit_all_positions():
    """
    Manually exits/liquidates all open positions at current market quotes,
    calculates realized P&L, records trade history and session exits,
    and returns freed margin to cash balance.
    """
    body = request.get_json(silent=True) or {}
    client_positions = body.get('client_positions')
    if client_positions and isinstance(client_positions, list):
        sync_positions_from_client(client_positions)

    from app.services.bot import bot_instance
    from datetime import datetime

    positions = Position.query.filter(Position.quantity > 0).all()
    if not positions:
        return jsonify({"success": True, "message": "No open positions to exit.", "exited_count": 0, "results": [], "closed_trades": []})

    results = []
    closed_trades = []
    for pos in positions:
        sym = pos.symbol
        qty = pos.quantity
        side = getattr(pos, 'side', 'LONG') or 'LONG'
        exit_side = "COVER" if side == "SHORT" else "SELL"
        
        res = PaperTradingEngine.execute_order(sym, exit_side, qty)
        if res.get("success"):
            pnl = float(res.get("realized_pnl", 0.0))
            chg_dict = res.get("charges") if isinstance(res.get("charges"), dict) else {}
            chg = float(chg_dict.get("total_charges", 0.0) if isinstance(chg_dict, dict) else 0.0)
            entry_p = float(res.get("entry_price") or pos.average_price or res.get("price", 0.0))
            exit_p = float(res.get("price", 0.0))
            cost_basis = round(qty * (entry_p if entry_p > 0 else 1.0), 2)
            roi_pct = round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0
            
            trade_record = {
                "id": res.get("trade_id") or (int(datetime.now().timestamp() * 1000) + len(closed_trades)),
                "symbol": sym,
                "displayName": sym.replace('.NS', ''),
                "side": exit_side,
                "quantity": qty,
                "entry_price": entry_p,
                "exit_price": exit_p,
                "price": exit_p,
                "total_value": round(qty * exit_p, 2),
                "charges": chg,
                "brokerage": round(float(chg_dict.get("brokerage", 0.0)), 2),
                "stt": round(float(chg_dict.get("stt", 0.0)), 2),
                "exchange_charges": round(float(chg_dict.get("exchange_charges", 0.0)), 2),
                "sebi_charges": round(float(chg_dict.get("sebi_charges", 0.0)), 2),
                "gst": round(float(chg_dict.get("gst", 0.0)), 2),
                "stamp_duty": round(float(chg_dict.get("stamp_duty", 0.0)), 2),
                "net_proceeds": round((qty * exit_p) - chg, 2),
                "realized_pnl": pnl,
                "roi_pct": roi_pct,
                "is_win": pnl >= 0,
                "action": exit_side,
                "signal": "MANUAL_SQUARE_OFF",
                "reason": f"Manual Square-Off by Operator ({exit_side})",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            closed_trades.append(trade_record)
            bot_instance.record_session_trade(trade_record)
            bot_instance.add_log(f"🛑 [MANUAL SQUARE OFF] Exited {qty} shares of {sym.replace('.NS', '')} @ ₹{exit_p:.2f} | P&L: ₹{pnl:+,.2f}", "SUCCESS" if pnl >= 0 else "WARNING")
            results.append({"symbol": sym, "success": True, "pnl": pnl})
        else:
            results.append({"symbol": sym, "success": False, "message": res.get("message")})

    return jsonify({
        "success": True,
        "message": f"Successfully squared off {len([r for r in results if r['success']])} open positions.",
        "exited_count": len([r for r in results if r['success']]),
        "results": results,
        "closed_trades": closed_trades
    })

@api_bp.route('/bot/reset-and-restart', methods=['POST'])
def reset_and_restart_bot():
    """
    Squares off any open positions, resets intraday limits, restores cash to ₹50,000,
    starts a fresh session report, and restarts the bot cleanly.
    """
    open_positions = Position.query.filter(Position.quantity > 0).all()
    for pos in open_positions:
        exit_side = "COVER" if getattr(pos, 'side', 'LONG') == "SHORT" else "SELL"
        PaperTradingEngine.execute_order(pos.symbol, exit_side, pos.quantity)
    
    Position.query.delete()
    WatchlistItem.query.delete()
    from app.models.trading import NIFTY_50_DEFAULT
    for s in NIFTY_50_DEFAULT:
        db.session.add(WatchlistItem(symbol=s))
    account = Account.query.first()
    if account:
        account.cash_balance = 50000.0
    else:
        account = Account(cash_balance=50000.0)
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
    bot_instance.start()
    return jsonify({
        "success": True,
        "message": "Bot successfully reset back to ₹50,000 and restarted fresh!",
        "cash_balance": 50000.0,
        "total_equity": 50000.0,
        "is_running": bot_instance.is_running
    })

@api_bp.route('/bot/overview', methods=['GET', 'POST'])
def get_bot_overview():
    """
    Consolidated single-flight endpoint for ALPHA:
    Combines status, portfolio metrics, active holdings, and closed trades summary in 1 request.
    Reduces Vercel serverless function invocations by 66%.
    """
    from app.services.bot import bot_instance
    from app.services.paper_trader import PaperTradingEngine
    from app.models.trading import TradeHistory

    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        client_positions = body.get('client_positions')
        explicit_clear = bool(body.get('explicit_clear', False))
        sync_positions_from_client(client_positions, explicit_clear=explicit_clear)
        client_closed_trades = body.get('client_closed_trades')
        sync_closed_trades_from_client(client_closed_trades)
        client_running = body.get('is_running')
        if client_running is not None:
            bot_instance.is_running = bool(client_running)

    status_data = bot_instance.get_status()
    portfolio_data = PaperTradingEngine.get_portfolio_summary()
    trades = TradeHistory.query.filter(TradeHistory.side.in_(["SELL", "COVER"])).order_by(TradeHistory.timestamp.desc()).all()
    closed_data = _build_closed_trades_summary(trades)

    return jsonify({
        "status": status_data,
        "portfolio": portfolio_data,
        "closed_trades": closed_data
    })

@api_bp.route('/bot/status', methods=['GET'])
def get_bot_status():
    from app.services.bot import bot_instance
    return jsonify(bot_instance.get_status())

@api_bp.route('/bot/session-report', methods=['GET', 'POST', 'DELETE'])
def bot_session_report():
    from app.services.bot import bot_instance
    if request.method == 'GET':
        status = bot_instance.get_status()
        return jsonify({
            "success": True,
            "session": status.get("session", {}),
            "is_running": bot_instance.is_running
        })
    elif request.method == 'POST':
        body = request.get_json(silent=True) or {}
        if "trade" in body:
            bot_instance.record_session_trade(body["trade"])
        return jsonify({
            "success": True,
            "session": bot_instance.get_status().get("session", {})
        })
    elif request.method == 'DELETE':
        bot_instance.start_new_session()
        return jsonify({
            "success": True,
            "message": "Session trades report reset for new run",
            "session": bot_instance.get_status().get("session", {})
        })

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

@api_bp.route('/bot/stop-all', methods=['POST'])
def stop_all_bots():
    """Emergency master kill switch to halt both ALPHA and NIGHTHAWK bots simultaneously."""
    from app.services.bot import bot_instance, after_hours_bot_instance
    alpha_res = {}
    nighthawk_res = {}
    try:
        alpha_res = bot_instance.stop()
    except Exception as e:
        alpha_res = {"success": False, "error": str(e)}

    try:
        nighthawk_res = after_hours_bot_instance.stop()
    except Exception as e:
        nighthawk_res = {"success": False, "error": str(e)}

    return jsonify({
        "success": True,
        "message": "Both ALPHA (Day Bot) and NIGHTHAWK (Night Bot) have been STOPPED immediately.",
        "alpha": alpha_res,
        "nighthawk": nighthawk_res
    })

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

    # Sync positions with client state if provided to prevent container drift
    client_positions = body.get('client_positions')
    sync_positions_from_client(client_positions)

    client_traded_symbols = body.get('client_traded_symbols')

    event = body.get('event')
    symbol = body.get('symbol')
    res = bot_instance.evaluate_signals(
        simulation_event=event, 
        target_symbol=symbol, 
        client_traded_symbols=client_traded_symbols
    )
    return jsonify({
        "status": "evaluated",
        "result": res,
        "bot_state": bot_instance.get_status()
    })

@api_bp.route('/bot/cron', methods=['GET', 'POST'])
def bot_cron():
    """
    Automated Background Cron trigger called every 1 minute by Vercel Cron or GitHub Actions.
    Triggers autonomous scanning, after-hours simulation, and order executions for NIFTY Bot.
    """
    from datetime import datetime
    from app.services.bot import bot_instance

    nifty_res = bot_instance.evaluate_signals()

    return jsonify({
        "status": "cron_executed",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "nifty_scanned": len(nifty_res),
        "nifty_bot_running": bot_instance.is_running
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

# ==============================================================================
# DEDICATED AFTER-HOURS BOT API ENDPOINTS (ISOLATED RULES & ENGINE)
# ==============================================================================
@api_bp.route('/after-hours/overview', methods=['GET', 'POST'])
def get_after_hours_overview():
    """
    Consolidated single-flight endpoint for NIGHTHAWK:
    Combines status, portfolio metrics, active holdings, and closed trades summary in 1 request.
    Reduces Vercel serverless function invocations by 66%.
    """
    from app.services.bot import after_hours_bot_instance
    from app.services.paper_trader import AfterHoursPaperTradingEngine
    from app.models.trading import AfterHoursTradeHistory

    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        client_positions = body.get('client_positions')
        explicit_clear = bool(body.get('explicit_clear', False))
        sync_after_hours_positions_from_client(client_positions, explicit_clear=explicit_clear)
        client_closed_trades = body.get('client_closed_trades')
        sync_after_hours_closed_trades_from_client(client_closed_trades)
        client_running = body.get('is_running')
        if client_running is not None:
            after_hours_bot_instance.is_running = bool(client_running)

    status_data = after_hours_bot_instance.get_status()
    portfolio_data = AfterHoursPaperTradingEngine.get_portfolio_summary()
    trades = AfterHoursTradeHistory.query.filter(AfterHoursTradeHistory.side.in_(["SELL", "COVER"])).order_by(AfterHoursTradeHistory.timestamp.desc()).all()
    closed_data = _build_after_hours_closed_trades_summary(trades)

    return jsonify({
        "status": status_data,
        "portfolio": portfolio_data,
        "closed_trades": closed_data
    })

@api_bp.route('/after-hours/status', methods=['GET'])
def get_after_hours_status():
    from app.services.bot import after_hours_bot_instance
    return jsonify(after_hours_bot_instance.get_status())

@api_bp.route('/after-hours/config', methods=['POST'])
def update_after_hours_config():
    from app.services.bot import after_hours_bot_instance
    config = request.get_json(silent=True) or {}
    after_hours_bot_instance.update_config(config)
    return jsonify({
        "status": "config_updated",
        "bot_state": after_hours_bot_instance.get_status()
    })

@api_bp.route('/after-hours/start', methods=['POST'])
def start_after_hours_bot():
    from app.services.bot import after_hours_bot_instance
    config = request.get_json(silent=True) or {}
    res = after_hours_bot_instance.start(config)
    return jsonify(res)

@api_bp.route('/after-hours/stop', methods=['POST'])
def stop_after_hours_bot():
    from app.services.bot import after_hours_bot_instance
    res = after_hours_bot_instance.stop()
    return jsonify(res)

@api_bp.route('/after-hours/tick', methods=['POST'])
def tick_after_hours_bot():
    from app.services.bot import after_hours_bot_instance
    body = request.get_json(silent=True) or {}
    cfg = body.get("config") or body
    if cfg and isinstance(cfg, dict):
        after_hours_bot_instance.update_config(cfg)

    client_running = body.get('is_running')
    if client_running is not None:
        after_hours_bot_instance.is_running = bool(client_running)

    client_positions = body.get('client_positions')
    sync_after_hours_positions_from_client(client_positions)

    event = body.get('event') or 'AFTER_HOURS_WALK'
    symbol = body.get('symbol')
    res = after_hours_bot_instance.evaluate_signals(simulation_event=event, target_symbol=symbol)
    return jsonify({
        "status": "evaluated",
        "result": res,
        "bot_state": after_hours_bot_instance.get_status()
    })

@api_bp.route('/after-hours/portfolio', methods=['GET', 'POST'])
def get_after_hours_portfolio():
    """Returns dedicated portfolio metrics and holdings for NIGHTHAWK (₹1 Crore base balance)"""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        client_positions = data.get('client_positions')
        if client_positions is not None and isinstance(client_positions, list):
            sync_after_hours_positions_from_client(client_positions)

    summary = AfterHoursPaperTradingEngine.get_portfolio_summary()
    return jsonify(summary)

@api_bp.route('/after-hours/order', methods=['POST'])
def place_after_hours_order():
    """Manual order / position adjustment for NIGHTHAWK desk"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "No data provided"}), 400

    symbol = data.get("symbol")
    side = data.get("side")
    quantity = data.get("quantity")
    execution_price = data.get("execution_price")

    if not symbol or not side or quantity is None:
        return jsonify({"success": False, "message": "symbol, side, and quantity are required"}), 400

    try:
        quantity = float(quantity)
    except ValueError:
        return jsonify({"success": False, "message": "quantity must be a valid number"}), 400

    exec_price = float(execution_price) if execution_price else None
    result = AfterHoursPaperTradingEngine.execute_order(
        symbol=symbol, side=side, quantity=quantity, execution_price=exec_price, force_bypass_day_limit=True
    )
    
    # Build complete closed trade details if exiting
    if side in ("SELL", "COVER") and result.get("success"):
        from datetime import datetime
        entry_p = float(result.get("entry_price", 0.0) or execution_price or result.get("price", 0.0))
        exit_p = float(result.get("price", 0.0))
        pnl = float(result.get("realized_pnl", 0.0))
        cost_basis = round(quantity * (entry_p if entry_p > 0 else 1.0), 2)
        roi_pct = round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0
        chg_dict = result.get("charges") if isinstance(result.get("charges"), dict) else {}
        chg_val = float(chg_dict.get("total_charges", 0.0) if isinstance(chg_dict, dict) else 0.0)
        closed_trade = {
            "id": result.get("trade_id") or int(datetime.now().timestamp() * 1000),
            "symbol": symbol,
            "displayName": symbol.replace('.NS', ''),
            "side": side,
            "quantity": quantity,
            "entry_price": entry_p,
            "exit_price": exit_p,
            "price": exit_p,
            "total_value": round(quantity * exit_p, 2),
            "charges": chg_val,
            "brokerage": round(float(chg_dict.get("brokerage", 0.0)), 2),
            "stt": round(float(chg_dict.get("stt", 0.0)), 2),
            "exchange_charges": round(float(chg_dict.get("exchange_charges", 0.0)), 2),
            "sebi_charges": round(float(chg_dict.get("sebi_charges", 0.0)), 2),
            "gst": round(float(chg_dict.get("gst", 0.0)), 2),
            "stamp_duty": round(float(chg_dict.get("stamp_duty", 0.0)), 2),
            "net_proceeds": round((quantity * exit_p) - chg_val, 2),
            "realized_pnl": pnl,
            "roi_pct": roi_pct,
            "is_win": pnl >= 0,
            "action": side,
            "signal": "MANUAL_EXIT",
            "reason": f"Manual {side} exit by operator",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        result["closed_trade"] = closed_trade

    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code

@api_bp.route('/after-hours/positions/exit-all', methods=['POST'])
def exit_all_after_hours_positions():
    """Square off all NIGHTHAWK positions and return margin to 1 Crore cash balance"""
    body = request.get_json(silent=True) or {}
    client_positions = body.get('client_positions')
    if client_positions and isinstance(client_positions, list):
        sync_after_hours_positions_from_client(client_positions)

    from app.services.bot import after_hours_bot_instance
    from datetime import datetime

    positions = AfterHoursPosition.query.filter(AfterHoursPosition.quantity > 0).all()
    if not positions:
        return jsonify({
            "success": True,
            "message": "No open NIGHTHAWK positions to square off.",
            "exited_count": 0,
            "total_pnl": 0.0,
            "portfolio": AfterHoursPaperTradingEngine.get_portfolio_summary(),
            "closed_trades": []
        })

    exited_count = 0
    total_realized_pnl = 0.0
    closed_trades = []

    for pos in positions:
        sym = pos.symbol
        qty = pos.quantity
        pos_side = getattr(pos, 'side', 'LONG') or 'LONG'
        quote = MarketDataService.get_quote(sym)
        p = quote.get("price", pos.average_price) if "error" not in quote else pos.average_price

        exit_side = "COVER" if pos_side == "SHORT" else "SELL"
        res = AfterHoursPaperTradingEngine.execute_order(sym, exit_side, qty, execution_price=p, force_bypass_day_limit=True)
        if res.get("success"):
            exited_count += 1
            pnl = float(res.get("realized_pnl") or 0.0)
            total_realized_pnl += pnl
            chg_dict = res.get("charges") if isinstance(res.get("charges"), dict) else {}
            chg = float(chg_dict.get("total_charges", 0.0) if isinstance(chg_dict, dict) else 0.0)
            entry_p = float(pos.average_price or p)
            cost_basis = round(qty * (entry_p if entry_p > 0 else 1.0), 2)
            roi_pct = round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0

            trade_record = {
                "id": res.get("trade_id") or (int(datetime.now().timestamp() * 1000) + len(closed_trades)),
                "symbol": sym,
                "displayName": sym.replace(".NS", ""),
                "side": exit_side,
                "quantity": qty,
                "entry_price": entry_p,
                "exit_price": p,
                "price": p,
                "total_value": round(qty * p, 2),
                "charges": chg,
                "brokerage": round(float(chg_dict.get("brokerage", 0.0)), 2),
                "stt": round(float(chg_dict.get("stt", 0.0)), 2),
                "exchange_charges": round(float(chg_dict.get("exchange_charges", 0.0)), 2),
                "sebi_charges": round(float(chg_dict.get("sebi_charges", 0.0)), 2),
                "gst": round(float(chg_dict.get("gst", 0.0)), 2),
                "stamp_duty": round(float(chg_dict.get("stamp_duty", 0.0)), 2),
                "net_proceeds": round((qty * p) - chg, 2),
                "realized_pnl": pnl,
                "roi_pct": roi_pct,
                "is_win": pnl >= 0,
                "action": exit_side,
                "signal": "MANUAL_EXIT_ALL",
                "reason": f"Manual Operator Square-Off All Positions @ ₹{p:.2f}",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            closed_trades.append(trade_record)
            after_hours_bot_instance.record_session_trade(trade_record)

    after_hours_bot_instance.add_log(f"⚡ [MANUAL SQUARE OFF] Liquidated {exited_count} NIGHTHAWK positions | Net P&L: ₹{total_realized_pnl:+,.2f}", "WARNING")

    return jsonify({
        "success": True,
        "message": f"Successfully squared off {exited_count} NIGHTHAWK positions.",
        "exited_count": exited_count,
        "total_pnl": round(total_realized_pnl, 2),
        "portfolio": AfterHoursPaperTradingEngine.get_portfolio_summary(),
        "closed_trades": closed_trades
    })

@api_bp.route('/after-hours/reset-account', methods=['POST'])
def reset_after_hours_account():
    """Resets NIGHTHAWK account back to ₹1,00,00,000 (1 Crore) and clears after-hours holdings"""
    AfterHoursPosition.query.delete()
    account = AfterHoursAccount.query.first()
    if account:
        account.cash_balance = 10000000.0  # 1 Crore
    else:
        account = AfterHoursAccount(cash_balance=10000000.0)
        db.session.add(account)
    db.session.commit()

    from app.services.bot import after_hours_bot_instance
    after_hours_bot_instance.logs = []
    after_hours_bot_instance.last_scan_summary = {
        "total_scanned": 0, "buys_found": 0, "sells_found": 0, "trades_executed": 0, "scanner_results": []
    }
    after_hours_bot_instance.session_trades = []
    after_hours_bot_instance.session_metrics = {
        "total_trades": 0, "total_buys": 0, "total_sells": 0, "net_realized_pnl": 0.0,
        "total_charges": 0.0, "win_rate": 0.0, "wins": 0, "losses": 0
    }
    after_hours_bot_instance.persist_state()

    return jsonify({
        "success": True,
        "message": "NIGHTHAWK portfolio reset to ₹1,00,00,000 (1 Crore) fresh capital!",
        "portfolio": AfterHoursPaperTradingEngine.get_portfolio_summary()
    })

@api_bp.route('/after-hours/reset-and-restart', methods=['POST'])
def reset_and_restart_after_hours_bot():
    """Squares off NIGHTHAWK positions, restores ₹1 Crore capital, and restarts NIGHTHAWK"""
    from app.services.bot import after_hours_bot_instance
    AfterHoursPosition.query.delete()
    account = AfterHoursAccount.query.first()
    if account:
        account.cash_balance = 10000000.0
    else:
        account = AfterHoursAccount(cash_balance=10000000.0)
        db.session.add(account)
    db.session.commit()

    after_hours_bot_instance.session_count = (after_hours_bot_instance.session_count or 1) + 1
    after_hours_bot_instance.session_id = f"sess_{int(datetime.now().timestamp()*1000)}"
    after_hours_bot_instance.session_trades = []
    after_hours_bot_instance.session_metrics = {
        "total_trades": 0, "total_buys": 0, "total_sells": 0, "net_realized_pnl": 0.0,
        "total_charges": 0.0, "win_rate": 0.0, "wins": 0, "losses": 0
    }
    after_hours_bot_instance.logs = []
    after_hours_bot_instance.is_running = True
    after_hours_bot_instance.session_status = "RUNNING"
    after_hours_bot_instance.session_start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    after_hours_bot_instance.persist_state()
    after_hours_bot_instance.add_log(f"🔄 [RESET & RESTART] NIGHTHAWK reset with ₹1 Crore capital. Starting Session #{after_hours_bot_instance.session_count}", "SUCCESS")

    return jsonify({
        "success": True,
        "message": f"NIGHTHAWK reset with ₹1 Crore capital and restarted (Session #{after_hours_bot_instance.session_count})",
        "bot_state": after_hours_bot_instance.get_status(),
        "portfolio": AfterHoursPaperTradingEngine.get_portfolio_summary()
    })

def _build_after_hours_closed_trades_summary(trades):
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

        cost_basis = (t.total + pnl) if t.side == 'COVER' else (t.total - pnl)
        roi_pct = round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0
        entry_p = round(cost_basis / t.quantity, 2) if (t.quantity > 0 and cost_basis > 0) else t.price

        closed.append({
            "id": t.id,
            "symbol": t.symbol,
            "displayName": t.symbol.replace(".NS", ""),
            "side": t.side,
            "quantity": t.quantity,
            "entry_price": entry_p,
            "exit_price": t.price,
            "price": t.price,
            "total_value": round(t.total, 2),
            "turnover": round(t.total, 2),
            "realized_pnl": round(pnl, 2),
            "charges": charges,
            "net_pnl": round(pnl - charges, 2),
            "roi_pct": roi_pct,
            "timestamp": t.timestamp.strftime("%Y-%m-%d %H:%M:%S") if t.timestamp else ""
        })

    win_rate = round((wins / len(closed) * 100), 1) if closed else 0.0

    return {
        "trades": closed,
        "summary": {
            "total_closed": len(closed),
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "net_realized_pnl": round(total_pnl, 2),
            "total_charges": round(total_charges, 2),
            "net_after_charges": round(total_pnl - total_charges, 2)
        }
    }

@api_bp.route('/after-hours/closed-trades', methods=['GET'])
@api_bp.route('/after-hours/trades/closed', methods=['GET'])
def get_after_hours_closed_trades():
    trades = AfterHoursTradeHistory.query.filter(AfterHoursTradeHistory.side.in_(["SELL", "COVER"])).order_by(AfterHoursTradeHistory.timestamp.desc()).all()
    return jsonify(_build_after_hours_closed_trades_summary(trades))

@api_bp.route('/after-hours/reset', methods=['POST'])
def reset_after_hours_bot():
    from app.services.bot import after_hours_bot_instance
    after_hours_bot_instance.start_new_session()
    after_hours_bot_instance.logs = []
    after_hours_bot_instance.last_scan_summary = {
        "total_scanned": 0,
        "buys_found": 0,
        "sells_found": 0,
        "trades_executed": 0,
        "scanner_results": []
    }
    after_hours_bot_instance.persist_state()
    return jsonify({
        "success": True,
        "message": "After-Hours Bot session and logs reset successfully!",
        "bot_state": after_hours_bot_instance.get_status()
    })

@api_bp.route('/after-hours/session-report', methods=['GET'])
def get_after_hours_session_report():
    from app.services.bot import after_hours_bot_instance
    return jsonify({
        "session": {
            "session_id": after_hours_bot_instance.session_id,
            "session_count": after_hours_bot_instance.session_count,
            "status": after_hours_bot_instance.session_status,
            "start_time": after_hours_bot_instance.session_start_time,
            "stop_time": after_hours_bot_instance.session_stop_time,
            "trades": after_hours_bot_instance.session_trades,
            "metrics": after_hours_bot_instance.session_metrics,
            "past_sessions": after_hours_bot_instance.past_sessions
        }
    })

# ==============================================================================
# SWING TRADING BOT (TITAN) API ROUTES
# ==============================================================================

def sync_swing_positions_from_client(client_positions, explicit_clear=False):
    if client_positions is None or not isinstance(client_positions, list):
        return
    try:
        from app.models.trading import SwingPosition, SwingAccount
        from app import db
        if len(client_positions) == 0:
            if explicit_clear:
                SwingPosition.query.delete()
                db.session.commit()
            return

        db_positions = SwingPosition.query.all()
        if len(db_positions) == 0 and len(client_positions) > 0:
            for cp in client_positions:
                sym = cp.get("symbol")
                qty = float(cp.get("quantity", 0))
                if sym and qty > 0:
                    pos = SwingPosition(
                        symbol=sym,
                        side=cp.get("side", "LONG"),
                        quantity=qty,
                        average_price=float(cp.get("average_price", 0)),
                        peak_price=float(cp.get("peak_price", cp.get("average_price", 0))),
                        stop_loss=float(cp.get("stop_loss", 0)),
                        take_profit=float(cp.get("take_profit", 0)),
                        strategy=cp.get("strategy", "VORTEX"),
                        confluence_score=int(cp.get("confluence_score", 85)),
                        trailing_active=bool(cp.get("trailing_active", False))
                    )
                    db.session.add(pos)
            db.session.commit()
    except Exception as e:
        db.session.rollback()

@api_bp.route('/swing/overview', methods=['GET', 'POST'])
def get_swing_overview():
    from app.services.swing_bot import swing_bot_instance, SwingPaperTradingEngine
    from app.models.trading import SwingTradeHistory

    if request.method == 'POST':
        client_data = request.get_json(silent=True) or {}
        client_positions = client_data.get('client_positions')
        explicit_clear = client_data.get('explicit_clear', False)
        if client_positions is not None:
            sync_swing_positions_from_client(client_positions, explicit_clear=explicit_clear)

        client_running = client_data.get('is_running')
        if client_running is not None:
            swing_bot_instance.is_running = bool(client_running)

    portfolio = SwingPaperTradingEngine.get_portfolio_summary()
    status = swing_bot_instance.get_status()
    scorecard = swing_bot_instance.get_strategy_scorecard()

    trades = SwingTradeHistory.query.order_by(SwingTradeHistory.timestamp.desc()).limit(50).all()
    trades_data = []
    for t in trades:
        trades_data.append({
            "id": t.id,
            "symbol": t.symbol,
            "displayName": t.symbol.replace('.NS', '').replace('.BO', ''),
            "side": t.side,
            "quantity": t.quantity,
            "price": t.price,
            "entry_price": t.entry_price,
            "total": t.total,
            "realized_pnl": t.realized_pnl,
            "roi_pct": t.roi_pct,
            "charges": t.charges,
            "strategy": t.strategy,
            "days_held": t.days_held,
            "exit_reason": t.exit_reason,
            "timestamp": t.timestamp.strftime("%Y-%m-%d %H:%M") if t.timestamp else ""
        })

    return jsonify({
        "status": status,
        "portfolio": portfolio,
        "strategy_scorecard": scorecard,
        "trades": trades_data
    })

@api_bp.route('/swing/scan', methods=['POST'])
def run_swing_scan():
    from app.services.swing_bot import swing_bot_instance
    body = request.get_json(silent=True) or {}
    client_running = body.get('is_running')
    if client_running is not None:
        swing_bot_instance.is_running = bool(client_running)

    client_positions = body.get('client_positions')
    if client_positions is not None:
        sync_swing_positions_from_client(client_positions)

    scan_summary = swing_bot_instance.scan_and_evaluate()
    return jsonify({
        "success": True,
        "scan_summary": scan_summary
    })

@api_bp.route('/swing/config', methods=['POST'])
def update_swing_config():
    from app.services.swing_bot import swing_bot_instance
    data = request.get_json() or {}
    if "selected_strategy" in data:
        swing_bot_instance.selected_strategy = str(data["selected_strategy"]).upper()
    if "allocation_per_trade" in data:
        swing_bot_instance.allocation_per_trade = float(data["allocation_per_trade"])
    if "max_positions" in data:
        swing_bot_instance.max_positions = int(data["max_positions"])
    if "scan_mode" in data:
        swing_bot_instance.scan_mode = str(data["scan_mode"])

    swing_bot_instance.persist_state()
    return jsonify({
        "success": True,
        "config": {
            "selected_strategy": swing_bot_instance.selected_strategy,
            "allocation_per_trade": swing_bot_instance.allocation_per_trade,
            "max_positions": swing_bot_instance.max_positions,
            "scan_mode": swing_bot_instance.scan_mode
        }
    })

@api_bp.route('/swing/start', methods=['POST'])
def start_swing_bot():
    from app.services.swing_bot import swing_bot_instance
    data = request.get_json(silent=True) or {}
    res = swing_bot_instance.start(config=data)
    return jsonify(res)

@api_bp.route('/swing/stop', methods=['POST'])
def stop_swing_bot():
    from app.services.swing_bot import swing_bot_instance
    res = swing_bot_instance.stop()
    return jsonify(res)

@api_bp.route('/swing/order', methods=['POST'])
def execute_swing_order():
    from app.services.swing_bot import SwingPaperTradingEngine
    data = request.get_json() or {}
    symbol = data.get("symbol")
    side = data.get("side", "BUY").upper()
    quantity = float(data.get("quantity", 0))
    price = float(data.get("price", 0))
    strategy = data.get("strategy", "VORTEX")
    sl = float(data.get("stop_loss", 0))
    tp = float(data.get("take_profit", 0))
    confluence = int(data.get("confluence_score", 85))

    if not symbol or quantity <= 0 or price <= 0:
        return jsonify({"success": False, "message": "Invalid order parameters"}), 400

    res = SwingPaperTradingEngine.execute_order(
        symbol=symbol, side=side, quantity=quantity, execution_price=price,
        strategy=strategy, sl=sl, tp=tp, confluence=confluence
    )
    return jsonify(res)

@api_bp.route('/swing/positions/exit-all', methods=['POST'])
def exit_all_swing_positions():
    from app.services.swing_bot import SwingPaperTradingEngine
    from app.models.trading import SwingPosition
    from app.services.market_data import MarketDataService

    positions = SwingPosition.query.filter(SwingPosition.quantity > 0).all()
    results = []
    for p in positions:
        quote = MarketDataService.get_quote(p.symbol)
        curr_price = quote.get("price", p.average_price) if "error" not in quote else p.average_price
        res = SwingPaperTradingEngine.execute_order(
            p.symbol, "SELL", p.quantity, curr_price, strategy=p.strategy
        )
        results.append({"symbol": p.symbol, "res": res})

    return jsonify({"success": True, "closed_positions": len(results), "details": results})

@api_bp.route('/swing/reset-account', methods=['POST'])
def reset_swing_account():
    from app.models.trading import SwingAccount, SwingPosition, SwingTradeHistory
    from app import db
    try:
        SwingPosition.query.delete()
        SwingTradeHistory.query.delete()
        acc = SwingAccount.query.first()
        if acc:
            acc.cash_balance = 100000.0
        else:
            db.session.add(SwingAccount(cash_balance=100000.0))
        db.session.commit()
        return jsonify({"success": True, "message": "Swing trading account reset to ₹1,00,000.00 cash balance."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@api_bp.route('/swing/gtt-basket', methods=['GET', 'POST'])
def generate_swing_gtt_basket():
    """Generates a Zerodha Kite 3-Leg GTT (OCO) order payload for swing positions"""
    data = request.get_json(silent=True) or {}
    sym = data.get("symbol", "RELIANCE.NS").replace(".NS", "").replace(".BO", "")
    qty = int(data.get("quantity", 10))
    entry_price = float(data.get("price", 2500.0))
    sl_price = float(data.get("stop_loss", entry_price * 0.965))
    target_price = float(data.get("take_profit", entry_price * 1.12))

    # Format compliant with Zerodha Kite GTT / Basket Import
    gtt_basket = [
        {
            "tradingsymbol": sym,
            "exchange": "NSE",
            "transaction_type": "BUY",
            "order_type": "LIMIT",
            "quantity": qty,
            "price": round(entry_price, 2),
            "product": "CNC",
            "validity": "DAY",
            "tag": "TITAN_SWING_ENTRY"
        },
        {
            "type": "two-leg",
            "condition": {
                "exchange": "NSE",
                "tradingsymbol": sym,
                "trigger_values": [round(sl_price, 2), round(target_price, 2)],
                "last_price": round(entry_price, 2)
            },
            "orders": [
                {
                    "transaction_type": "SELL",
                    "quantity": qty,
                    "order_type": "LIMIT",
                    "product": "CNC",
                    "price": round(sl_price * 0.998, 2) # Limit slightly below trigger for guaranteed execution
                },
                {
                    "transaction_type": "SELL",
                    "quantity": qty,
                    "order_type": "LIMIT",
                    "product": "CNC",
                    "price": round(target_price, 2)
                }
            ],
            "tag": "TITAN_SWING_OCO"
        }
    ]

    return jsonify({
        "success": True,
        "symbol": sym,
        "basket": gtt_basket,
        "summary": {
            "entry": round(entry_price, 2),
            "stop_loss": round(sl_price, 2),
            "target": round(target_price, 2),
            "risk_pct": round(((sl_price - entry_price) / entry_price * 100), 2),
            "reward_pct": round(((target_price - entry_price) / entry_price * 100), 2)
        }
    })





