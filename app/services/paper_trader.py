from datetime import datetime, timezone, timedelta
from app import db
from app.models.trading import (
    Account, Position, TradeHistory,
    AfterHoursAccount, AfterHoursPosition, AfterHoursTradeHistory
)
from app.services.market_data import MarketDataService

def prune_old_trades(trade_model, max_trades=100):
    """Retains only the latest max_trades records and deletes older ones."""
    try:
        from app import db
        total = trade_model.query.count()
        if total > max_trades:
            keep_ids = [r[0] for r in db.session.query(trade_model.id).order_by(trade_model.timestamp.desc(), trade_model.id.desc()).limit(max_trades).all()]
            if keep_ids:
                db.session.query(trade_model).filter(~trade_model.id.in_(keep_ids)).delete(synchronize_session=False)
                db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

class PaperTradingEngine:
    LEVERAGE = 5.0  # NSE Intraday MIS Leverage: 5x (20% margin requirement)

    @staticmethod
    def reconcile_account_balance():
        from app.models.trading import Account, Position, TradeHistory
        from app import db
        account = Account.query.first()
        if not account:
            account = Account(cash_balance=50000.0)
            db.session.add(account)
            db.session.commit()
            return account

        positions = Position.query.filter(Position.quantity > 0).all()
        total_margin_used = sum((p.quantity * p.average_price) / PaperTradingEngine.LEVERAGE for p in positions)
        total_realized_pnl = db.session.query(db.func.sum(TradeHistory.realized_pnl)).scalar() or 0.0
        
        # Canonical cash balance = Starting cash (₹50k) + Realized P&L - Active Margin Blocked
        expected_cash = round(max(0.0, 50000.0 + total_realized_pnl - total_margin_used), 2)
        account.cash_balance = expected_cash
        db.session.commit()
        return account

    @staticmethod
    def get_portfolio_summary():
        account = PaperTradingEngine.reconcile_account_balance()
        if not account:
            return {"error": "Account not initialized"}
        
        positions = Position.query.filter(Position.quantity > 0).all()
        portfolio_positions = []
        total_market_value = 0.0
        total_margin_used = 0.0
        total_unrealized_pnl = 0.0
        
        for pos in positions:
            quote = MarketDataService.get_quote(pos.symbol)
            current_price = quote.get("price", pos.average_price) if "error" not in quote else pos.average_price
            
            side = getattr(pos, 'side', 'LONG') or 'LONG'
            cost_basis = pos.quantity * pos.average_price
            market_value = pos.quantity * current_price
            margin_used = cost_basis / PaperTradingEngine.LEVERAGE
            
            if side == 'SHORT':
                unrealized_pnl = cost_basis - market_value
            else:
                unrealized_pnl = market_value - cost_basis

            # Calculate return on actual margin deployed
            unrealized_pnl_pct = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0.0
            
            total_market_value += market_value
            total_margin_used += margin_used
            total_unrealized_pnl += unrealized_pnl
            
            # Calculate Locked Profit from Trailing Stop / Breakeven
            sl_val = float(getattr(pos, 'stop_loss', 0.0) or 0.0)
            is_trailing = bool(getattr(pos, 'trailing_active', False))
            locked_pnl = None
            locked_pct = None
            if side == 'SHORT':
                if sl_val > 0 and sl_val <= pos.average_price:
                    locked_pnl = round((pos.average_price - sl_val) * pos.quantity, 2)
                    locked_pct = round(((pos.average_price - sl_val) / pos.average_price) * 100, 2) if pos.average_price > 0 else 0.0
                elif is_trailing:
                    locked_pnl = 0.0
                    locked_pct = 0.0
            else:
                if sl_val >= pos.average_price:
                    locked_pnl = round((sl_val - pos.average_price) * pos.quantity, 2)
                    locked_pct = round(((sl_val - pos.average_price) / pos.average_price) * 100, 2) if pos.average_price > 0 else 0.0
                elif is_trailing:
                    locked_pnl = 0.0
                    locked_pct = 0.0

            portfolio_positions.append({
                "symbol": pos.symbol,
                "side": side,
                "quantity": pos.quantity,
                "average_price": round(pos.average_price, 2),
                "current_price": round(current_price, 2),
                "market_value": round(market_value, 2),
                "margin_used": round(margin_used, 2),
                "leverage": 5,
                "unrealized_pnl": round(unrealized_pnl, 2),
                "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                "peak_price": round(getattr(pos, 'peak_price', 0.0) or pos.average_price, 2),
                "stop_loss": round(getattr(pos, 'stop_loss', 0.0) or 0.0, 2),
                "take_profit": round(getattr(pos, 'take_profit', 0.0) or 0.0, 2),
                "trailing_active": bool(getattr(pos, 'trailing_active', False)),
                "locked_pnl": locked_pnl,
                "locked_pct": locked_pct
            })
            
        total_equity = account.cash_balance + total_margin_used + total_unrealized_pnl
        buying_power = account.cash_balance * PaperTradingEngine.LEVERAGE
        
        account_data = {
            "cash_balance": round(account.cash_balance, 2),
            "available_margin": round(account.cash_balance, 2),
            "margin_used": round(total_margin_used, 2),
            "buying_power": round(buying_power, 2),
            "leverage": 5,
            "market_value": round(total_market_value, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2)
        }

        return {
            "cash_balance": round(account.cash_balance, 2),
            "available_margin": round(account.cash_balance, 2),
            "margin_used": round(total_margin_used, 2),
            "buying_power": round(buying_power, 2),
            "leverage": 5,
            "market_value": round(total_market_value, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "account": account_data,
            "positions": portfolio_positions
        }

    @staticmethod
    def calculate_transaction_charges(turnover: float, side: str) -> dict:
        """
        Computes realistic Indian equity transaction charges & statutory taxes:
        - Brokerage: 0.03% or ₹20 per executed order, whichever is lower.
        - STT: 0.025% on the sell side.
        - Exchange transaction charges: 0.00307% on total buy and sell turnover.
        - SEBI charges: ₹10 per crore (0.000001) on total buy and sell turnover.
        - GST: 18% on (brokerage + exchange transaction charges + SEBI charges).
        - Stamp duty: 0.003% on the buy side.
        """
        turnover = float(turnover)
        side = side.strip().upper()

        # 1. Brokerage: 0.03% or ₹20 per executed order, whichever is lower
        brokerage = round(min(turnover * 0.0003, 20.0), 2)

        # 2. STT: 0.025% on the sell side
        stt = round(turnover * 0.00025, 2) if side == "SELL" else 0.0

        # 3. Exchange transaction charges: 0.00307% on turnover
        exchange_charges = round(turnover * 0.0000307, 2)

        # 4. SEBI charges: ₹10 per crore (₹10 / 10,000,000)
        sebi_charges = round(turnover * 0.000001, 2)

        # 5. GST: 18% on (brokerage + exchange charges + SEBI charges)
        gst_taxable = brokerage + exchange_charges + sebi_charges
        gst = round(gst_taxable * 0.18, 2)

        # 6. Stamp duty: 0.003% on the buy side
        stamp_duty = round(turnover * 0.00003, 2) if side == "BUY" else 0.0

        # Total statutory and brokerage charges
        total_charges = round(brokerage + stt + exchange_charges + sebi_charges + gst + stamp_duty, 2)

        return {
            "turnover": round(turnover, 2),
            "brokerage": brokerage,
            "stt": stt,
            "exchange_charges": exchange_charges,
            "sebi_charges": sebi_charges,
            "gst": gst,
            "stamp_duty": stamp_duty,
            "total_charges": total_charges
        }

    @staticmethod
    def is_symbol_traded_today(symbol: str) -> bool:
        """
        Checks if a stock has already had an intraday entry trade (BUY or SHORT) today in IST,
        or is currently held in an open position.
        """
        try:
            # 1. Check open position
            pos = Position.query.filter_by(symbol=symbol).first()
            if pos and pos.quantity > 0:
                return True

            # 2. Check TradeHistory for today in IST (UTC+5:30)
            now_utc = datetime.now(timezone.utc)
            now_ist = now_utc + timedelta(hours=5, minutes=30)
            start_of_ist_day_utc = datetime(now_ist.year, now_ist.month, now_ist.day, 0, 0, 0) - timedelta(hours=5, minutes=30)
            
            trade = TradeHistory.query.filter(
                TradeHistory.symbol == symbol,
                TradeHistory.side.in_(["BUY", "SHORT"]),
                TradeHistory.timestamp >= start_of_ist_day_utc
            ).first()
            return trade is not None
        except Exception:
            return False

    @staticmethod
    def execute_order(symbol: str, side: str, quantity: float, execution_price: float = None, force_bypass_day_limit: bool = True):
        symbol = symbol.strip().upper()
        side = side.strip().upper()
        quantity = float(quantity)
        
        if quantity <= 0:
            return {"success": False, "message": "Quantity must be greater than zero"}
            
        if side not in ["BUY", "SELL", "SHORT", "COVER"]:
            return {"success": False, "message": "Side must be BUY, SELL, SHORT, or COVER"}
            
        if execution_price is not None and execution_price > 0:
            price = round(float(execution_price), 2)
        else:
            quote = MarketDataService.get_quote(symbol)
            if "error" in quote:
                return {"success": False, "message": f"Could not fetch price: {quote['error']}"}
            price = quote["price"]
            
        turnover = round(quantity * price, 2)
        account = PaperTradingEngine.reconcile_account_balance()
        if not account:
            return {"success": False, "message": "Account not initialized"}

        clean_sym = symbol.replace('.NS', '').strip().upper()
        ns_sym = f"{clean_sym}.NS"
        position = Position.query.filter(
            (Position.symbol == symbol) |
            (Position.symbol == clean_sym) |
            (Position.symbol == ns_sym)
        ).first()
        pos_side = (getattr(position, 'side', 'LONG') or 'LONG') if position else None

        # -------------------------------------------------------------
        # 1. SHORT EXIT: BUY TO COVER (closing or reducing SHORT position)
        # -------------------------------------------------------------
        if (side in ["BUY", "COVER"]) and position and pos_side == "SHORT":
            charges_data = PaperTradingEngine.calculate_transaction_charges(turnover, "BUY")
            total_charges = charges_data["total_charges"]

            qty_to_cover = min(quantity, position.quantity)
            cost_basis = round(qty_to_cover * position.average_price, 2)
            margin_originally_blocked = round(cost_basis / PaperTradingEngine.LEVERAGE, 2)
            cover_turnover = round(qty_to_cover * price, 2)
            # Short profit = entry price - cover price
            gross_pnl = cost_basis - cover_turnover
            net_realized_pnl = round(gross_pnl - total_charges, 2)

            cash_returned = round(margin_originally_blocked + net_realized_pnl, 2)
            account.cash_balance += cash_returned
            position.quantity -= qty_to_cover

            if position.quantity <= 0:
                db.session.delete(position)

            trade = TradeHistory(
                symbol=symbol,
                side="COVER",
                quantity=qty_to_cover,
                price=price,
                total=cover_turnover,
                realized_pnl=net_realized_pnl,
                charges=total_charges,
                brokerage=charges_data["brokerage"],
                stt=charges_data["stt"],
                exchange_charges=charges_data["exchange_charges"],
                sebi_charges=charges_data["sebi_charges"],
                gst=charges_data["gst"],
                stamp_duty=charges_data["stamp_duty"],
                net_total=cash_returned
            )
            db.session.add(trade)
            db.session.commit()
            prune_old_trades(TradeHistory, 100)
            return {
                "success": True,
                "action": "COVER",
                "price": price,
                "entry_price": position.average_price if position else price,
                "realized_pnl": net_realized_pnl,
                "message": f"Covered {qty_to_cover} {symbol} @ ₹{price:.2f} [Short Exit]. Net Realized P&L: ₹{net_realized_pnl:,.2f} (Charges: ₹{total_charges:.2f})",
                "trade_id": trade.id,
                "charges": charges_data
            }

        # -------------------------------------------------------------
        # 2. LONG EXIT: SELL TO CLOSE (liquidating or reducing LONG position)
        # -------------------------------------------------------------
        elif (side == "SELL") and position and pos_side == "LONG":
            charges_data = PaperTradingEngine.calculate_transaction_charges(turnover, "SELL")
            total_charges = charges_data["total_charges"]

            qty_to_sell = min(quantity, position.quantity)
            cost_basis = round(qty_to_sell * position.average_price, 2)
            margin_originally_blocked = round(cost_basis / PaperTradingEngine.LEVERAGE, 2)
            sell_turnover = round(qty_to_sell * price, 2)
            # Long profit = sell price - entry price
            gross_pnl = sell_turnover - cost_basis
            net_realized_pnl = round(gross_pnl - total_charges, 2)

            cash_returned = round(margin_originally_blocked + net_realized_pnl, 2)
            account.cash_balance += cash_returned
            position.quantity -= qty_to_sell

            if position.quantity <= 0:
                db.session.delete(position)

            trade = TradeHistory(
                symbol=symbol,
                side="SELL",
                quantity=qty_to_sell,
                price=price,
                total=sell_turnover,
                realized_pnl=net_realized_pnl,
                charges=total_charges,
                brokerage=charges_data["brokerage"],
                stt=charges_data["stt"],
                exchange_charges=charges_data["exchange_charges"],
                sebi_charges=charges_data["sebi_charges"],
                gst=charges_data["gst"],
                stamp_duty=charges_data["stamp_duty"],
                net_total=cash_returned
            )
            db.session.add(trade)
            db.session.commit()
            prune_old_trades(TradeHistory, 100)
            return {
                "success": True,
                "action": "SELL",
                "price": price,
                "entry_price": position.average_price if position else price,
                "realized_pnl": net_realized_pnl,
                "message": f"Sold {qty_to_sell} {symbol} @ ₹{price:.2f} [Long Exit]. Net Realized P&L: ₹{net_realized_pnl:,.2f} (Charges: ₹{total_charges:.2f})",
                "trade_id": trade.id,
                "charges": charges_data
            }

        # -------------------------------------------------------------
        # 3. LONG ENTRY: BUY TO OPEN
        # -------------------------------------------------------------
        elif side == "BUY":
            # Guard A: Intraday Averaging is strictly disabled
            if position and position.quantity > 0:
                return {
                    "success": False,
                    "message": f"Position already exists for {symbol} ({int(position.quantity)} shares @ ₹{position.average_price:.2f}). Intraday averaging is disabled."
                }

            # Guard B: Strict Intraday 1-Trade-Per-Stock limit (no re-entry if traded today)
            if not force_bypass_day_limit and PaperTradingEngine.is_symbol_traded_today(symbol):
                return {
                    "success": False,
                    "message": f"Intraday 1-trade limit reached for {symbol}. Stock has already been traded today."
                }

            charges_data = PaperTradingEngine.calculate_transaction_charges(turnover, "BUY")
            total_charges = charges_data["total_charges"]
            margin_required = round((turnover / PaperTradingEngine.LEVERAGE) + total_charges, 2)

            if account.cash_balance < margin_required:
                return {
                    "success": False,
                    "message": f"Insufficient margin (5x Leverage). Required: ₹{margin_required:,.2f} (Turnover: ₹{turnover:,.2f} + Charges: ₹{total_charges:.2f}), Available: ₹{account.cash_balance:,.2f}"
                }

            account.cash_balance -= margin_required
            if not position:
                position = Position(
                    symbol=symbol,
                    side="LONG",
                    quantity=quantity,
                    average_price=price,
                    peak_price=price,
                    stop_loss=0.0,
                    take_profit=0.0,
                    trailing_active=False
                )
                db.session.add(position)
            else:
                position.quantity = quantity
                position.average_price = price
                position.side = "LONG"
                position.peak_price = price
                position.stop_loss = 0.0
                position.take_profit = 0.0
                position.trailing_active = False

            trade = TradeHistory(
                symbol=symbol,
                side="BUY",
                quantity=quantity,
                price=price,
                total=turnover,
                realized_pnl=0.0,
                charges=total_charges,
                brokerage=charges_data["brokerage"],
                stt=charges_data["stt"],
                exchange_charges=charges_data["exchange_charges"],
                sebi_charges=charges_data["sebi_charges"],
                gst=charges_data["gst"],
                stamp_duty=charges_data["stamp_duty"],
                net_total=margin_required
            )
            db.session.add(trade)
            db.session.commit()
            prune_old_trades(TradeHistory, 100)
            return {
                "success": True,
                "action": "BUY",
                "price": price,
                "entry_price": price,
                "realized_pnl": 0.0,
                "message": f"Bought {quantity} {symbol} @ ₹{price:.2f} [5x Leverage | Margin: ₹{margin_required:,.2f}] (Charges: ₹{total_charges:.2f})",
                "trade_id": trade.id,
                "charges": charges_data
            }

        # -------------------------------------------------------------
        # 4. SHORT ENTRY: SELL SHORT (initiated when not holding long)
        # -------------------------------------------------------------
        elif side in ["SELL", "SHORT"]:
            # Guard A: Intraday Averaging is strictly disabled
            if position and position.quantity > 0:
                return {
                    "success": False,
                    "message": f"Position already exists for {symbol} ({int(position.quantity)} shares @ ₹{position.average_price:.2f}). Intraday averaging is disabled."
                }

            # Guard B: Strict Intraday 1-Trade-Per-Stock limit (no re-entry if traded today)
            if not force_bypass_day_limit and PaperTradingEngine.is_symbol_traded_today(symbol):
                return {
                    "success": False,
                    "message": f"Intraday 1-trade limit reached for {symbol}. Stock has already been traded today."
                }

            charges_data = PaperTradingEngine.calculate_transaction_charges(turnover, "SELL")
            total_charges = charges_data["total_charges"]
            margin_required = round((turnover / PaperTradingEngine.LEVERAGE) + total_charges, 2)

            if account.cash_balance < margin_required:
                return {
                    "success": False,
                    "message": f"Insufficient margin (5x Leverage). Required: ₹{margin_required:,.2f} (Turnover: ₹{turnover:,.2f} + Charges: ₹{total_charges:.2f}), Available: ₹{account.cash_balance:,.2f}"
                }

            account.cash_balance -= margin_required
            if not position:
                position = Position(
                    symbol=symbol,
                    side="SHORT",
                    quantity=quantity,
                    average_price=price,
                    peak_price=price,
                    stop_loss=0.0,
                    take_profit=0.0,
                    trailing_active=False
                )
                db.session.add(position)
            else:
                position.quantity = quantity
                position.average_price = price
                position.side = "SHORT"
                position.peak_price = price
                position.stop_loss = 0.0
                position.take_profit = 0.0
                position.trailing_active = False

            trade = TradeHistory(
                symbol=symbol,
                side="SHORT",
                quantity=quantity,
                price=price,
                total=turnover,
                realized_pnl=0.0,
                charges=total_charges,
                brokerage=charges_data["brokerage"],
                stt=charges_data["stt"],
                exchange_charges=charges_data["exchange_charges"],
                sebi_charges=charges_data["sebi_charges"],
                gst=charges_data["gst"],
                stamp_duty=charges_data["stamp_duty"],
                net_total=margin_required
            )
            db.session.add(trade)
            db.session.commit()
            prune_old_trades(TradeHistory, 100)
            return {
                "success": True,
                "action": "SHORT",
                "price": price,
                "entry_price": price,
                "realized_pnl": 0.0,
                "message": f"Shorted {quantity} {symbol} @ ₹{price:.2f} [5x Leverage | Margin: ₹{margin_required:,.2f}] (Charges: ₹{total_charges:.2f})",
                "trade_id": trade.id,
                "charges": charges_data
            }


# ==============================================================================
# NIGHTHAWK ISOLATED PAPER TRADING ENGINE (₹1 CRORE / ₹10,000,000 BASE BALANCE)
# ==============================================================================
class AfterHoursPaperTradingEngine:
    LEVERAGE = 5.0
    INITIAL_CASH = 10000000.0  # ₹1 Crore (₹10,000,000)
    slippage_pct = 0.0

    @staticmethod
    def reconcile_account_balance():
        from app.models.trading import AfterHoursAccount, AfterHoursPosition, AfterHoursTradeHistory
        from app import db
        account = AfterHoursAccount.query.first()
        if not account:
            account = AfterHoursAccount(cash_balance=AfterHoursPaperTradingEngine.INITIAL_CASH)
            db.session.add(account)
            db.session.commit()
            return account

        positions = AfterHoursPosition.query.filter(AfterHoursPosition.quantity > 0).all()
        total_margin_used = sum((p.quantity * p.average_price) / AfterHoursPaperTradingEngine.LEVERAGE for p in positions)
        total_realized_pnl = db.session.query(db.func.sum(AfterHoursTradeHistory.realized_pnl)).scalar() or 0.0

        # Canonical cash balance = Starting cash (₹1 Crore) + Realized P&L - Active Margin Blocked
        expected_cash = round(max(0.0, AfterHoursPaperTradingEngine.INITIAL_CASH + total_realized_pnl - total_margin_used), 2)
        account.cash_balance = expected_cash
        db.session.commit()
        return account

    @staticmethod
    def get_portfolio_summary():
        account = AfterHoursPaperTradingEngine.reconcile_account_balance()
        if not account:
            return {"error": "After-hours account not initialized"}

        positions = AfterHoursPosition.query.filter(AfterHoursPosition.quantity > 0).all()
        portfolio_positions = []
        total_market_value = 0.0
        total_margin_used = 0.0
        total_unrealized_pnl = 0.0
        from app.services.bot import get_after_hours_price

        for pos in positions:
            quote = MarketDataService.get_quote(pos.symbol)
            base_price = getattr(pos, 'average_price', None) or (quote.get("price", 100.0) if "error" not in quote else 100.0)
            current_price = get_after_hours_price(pos.symbol, base_price, pos)

            side = getattr(pos, 'side', 'LONG') or 'LONG'
            cost_basis = pos.quantity * pos.average_price
            market_value = pos.quantity * current_price
            margin_used = cost_basis / AfterHoursPaperTradingEngine.LEVERAGE

            if side == 'SHORT':
                unrealized_pnl = cost_basis - market_value
            else:
                unrealized_pnl = market_value - cost_basis

            unrealized_pnl_pct = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0.0

            total_market_value += market_value
            total_margin_used += margin_used
            total_unrealized_pnl += unrealized_pnl

            # Calculate Locked Profit from Trailing Stop / Breakeven
            sl_val = float(getattr(pos, 'stop_loss', 0.0) or 0.0)
            is_trailing = bool(getattr(pos, 'trailing_active', False))
            locked_pnl = None
            locked_pct = None
            if side == 'SHORT':
                if sl_val > 0 and sl_val <= pos.average_price:
                    locked_pnl = round((pos.average_price - sl_val) * pos.quantity, 2)
                    locked_pct = round(((pos.average_price - sl_val) / pos.average_price) * 100, 2) if pos.average_price > 0 else 0.0
                elif is_trailing:
                    locked_pnl = 0.0
                    locked_pct = 0.0
            else:
                if sl_val >= pos.average_price:
                    locked_pnl = round((sl_val - pos.average_price) * pos.quantity, 2)
                    locked_pct = round(((sl_val - pos.average_price) / pos.average_price) * 100, 2) if pos.average_price > 0 else 0.0
                elif is_trailing:
                    locked_pnl = 0.0
                    locked_pct = 0.0

            portfolio_positions.append({
                "symbol": pos.symbol,
                "side": side,
                "quantity": pos.quantity,
                "average_price": round(pos.average_price, 2),
                "current_price": round(current_price, 2),
                "market_value": round(market_value, 2),
                "margin_used": round(margin_used, 2),
                "leverage": 5,
                "unrealized_pnl": round(unrealized_pnl, 2),
                "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                "peak_price": round(getattr(pos, 'peak_price', 0.0) or pos.average_price, 2),
                "stop_loss": round(getattr(pos, 'stop_loss', 0.0) or 0.0, 2),
                "take_profit": round(getattr(pos, 'take_profit', 0.0) or 0.0, 2),
                "trailing_active": bool(getattr(pos, 'trailing_active', False)),
                "locked_pnl": locked_pnl,
                "locked_pct": locked_pct
            })

        total_equity = account.cash_balance + total_margin_used + total_unrealized_pnl
        buying_power = account.cash_balance * AfterHoursPaperTradingEngine.LEVERAGE

        account_data = {
            "cash_balance": round(account.cash_balance, 2),
            "available_margin": round(account.cash_balance, 2),
            "margin_used": round(total_margin_used, 2),
            "buying_power": round(buying_power, 2),
            "leverage": 5,
            "market_value": round(total_market_value, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2)
        }

        return {
            "cash_balance": round(account.cash_balance, 2),
            "available_margin": round(account.cash_balance, 2),
            "margin_used": round(total_margin_used, 2),
            "buying_power": round(buying_power, 2),
            "leverage": 5,
            "market_value": round(total_market_value, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "positions": portfolio_positions,
            "account": account_data
        }

    @staticmethod
    def is_symbol_traded_today(symbol: str) -> bool:
        # Limit of trades removed: symbols can be traded without daily restriction
        return False

    @staticmethod
    def execute_order(symbol: str, side: str, quantity: float, execution_price: float = None, force_bypass_day_limit: bool = True):
        symbol = symbol.strip().upper()
        side = side.strip().upper()
        quantity = float(quantity)

        if quantity <= 0:
            return {"success": False, "message": "Quantity must be greater than zero"}

        if side not in ["BUY", "SELL", "SHORT", "COVER"]:
            return {"success": False, "message": "Side must be BUY, SELL, SHORT, or COVER"}

        if execution_price is not None and execution_price > 0:
            price = round(float(execution_price), 2)
        else:
            quote = MarketDataService.get_quote(symbol)
            if "error" in quote:
                return {"success": False, "message": f"Could not fetch price: {quote['error']}"}
            price = quote["price"]

        turnover = round(quantity * price, 2)
        # Apply simulated spread & slippage friction if configured
        slip = float(getattr(AfterHoursPaperTradingEngine, 'slippage_pct', 0.0) or 0.0)
        if slip > 0:
            if side in ["BUY", "COVER"]:
                price = round(price * (1.0 + slip / 100.0), 2)
            elif side in ["SELL", "SHORT"]:
                price = round(price * (1.0 - slip / 100.0), 2)
            turnover = round(quantity * price, 2)

        account = AfterHoursPaperTradingEngine.reconcile_account_balance()
        if not account:
            return {"success": False, "message": "After-hours account not initialized"}

        clean_sym = symbol.replace('.NS', '').strip().upper()
        ns_sym = f"{clean_sym}.NS"
        position = AfterHoursPosition.query.filter(
            (AfterHoursPosition.symbol == symbol) |
            (AfterHoursPosition.symbol == clean_sym) |
            (AfterHoursPosition.symbol == ns_sym)
        ).first()
        pos_side = (getattr(position, 'side', 'LONG') or 'LONG') if position else None

        # 1. SHORT EXIT: COVER
        if (side in ["BUY", "COVER"]) and position and pos_side == "SHORT":
            charges_data = PaperTradingEngine.calculate_transaction_charges(turnover, "BUY")
            total_charges = charges_data["total_charges"]
            qty_to_cover = min(quantity, position.quantity)
            cost_basis = round(qty_to_cover * position.average_price, 2)
            margin_originally_blocked = round(cost_basis / AfterHoursPaperTradingEngine.LEVERAGE, 2)
            cover_turnover = round(qty_to_cover * price, 2)
            gross_pnl = cost_basis - cover_turnover
            net_realized_pnl = round(gross_pnl - total_charges, 2)
            cash_returned = round(margin_originally_blocked + net_realized_pnl, 2)
            account.cash_balance += cash_returned
            position.quantity -= qty_to_cover
            if position.quantity <= 0:
                db.session.delete(position)
            trade = AfterHoursTradeHistory(
                symbol=symbol, side="COVER", quantity=qty_to_cover, price=price, total=cover_turnover,
                realized_pnl=net_realized_pnl, charges=total_charges, brokerage=charges_data["brokerage"],
                stt=charges_data["stt"], exchange_charges=charges_data["exchange_charges"],
                sebi_charges=charges_data["sebi_charges"], gst=charges_data["gst"],
                stamp_duty=charges_data["stamp_duty"], net_total=cash_returned
            )
            db.session.add(trade)
            db.session.commit()
            prune_old_trades(AfterHoursTradeHistory, 100)
            return {
                "success": True, "action": "COVER", "price": price, "entry_price": position.average_price if position else price,
                "realized_pnl": net_realized_pnl, "roi_pct": round((net_realized_pnl / margin_originally_blocked) * 100, 2) if margin_originally_blocked > 0 else 0.0,
                "message": f"Covered {qty_to_cover} {symbol} @ ₹{price:.2f} | P&L: ₹{net_realized_pnl:+,.2f}",
                "trade_id": trade.id, "charges": charges_data
            }

        # 2. LONG EXIT: SELL
        elif (side in ["SELL", "COVER"]) and position and pos_side != "SHORT":
            charges_data = PaperTradingEngine.calculate_transaction_charges(turnover, "SELL")
            total_charges = charges_data["total_charges"]
            qty_to_sell = min(quantity, position.quantity)
            cost_basis = round(qty_to_sell * position.average_price, 2)
            margin_originally_blocked = round(cost_basis / AfterHoursPaperTradingEngine.LEVERAGE, 2)
            gross_pnl = turnover - cost_basis
            net_realized_pnl = round(gross_pnl - total_charges, 2)
            cash_returned = round(margin_originally_blocked + net_realized_pnl, 2)
            account.cash_balance += cash_returned
            position.quantity -= qty_to_sell
            if position.quantity <= 0:
                db.session.delete(position)
            trade = AfterHoursTradeHistory(
                symbol=symbol, side="SELL", quantity=qty_to_sell, price=price, total=turnover,
                realized_pnl=net_realized_pnl, charges=total_charges, brokerage=charges_data["brokerage"],
                stt=charges_data["stt"], exchange_charges=charges_data["exchange_charges"],
                sebi_charges=charges_data["sebi_charges"], gst=charges_data["gst"],
                stamp_duty=charges_data["stamp_duty"], net_total=cash_returned
            )
            db.session.add(trade)
            db.session.commit()
            prune_old_trades(AfterHoursTradeHistory, 100)
            return {
                "success": True, "action": "SELL", "price": price, "entry_price": position.average_price if position else price,
                "realized_pnl": net_realized_pnl, "roi_pct": round((net_realized_pnl / margin_originally_blocked) * 100, 2) if margin_originally_blocked > 0 else 0.0,
                "message": f"Sold {qty_to_sell} {symbol} @ ₹{price:.2f} | P&L: ₹{net_realized_pnl:+,.2f}",
                "trade_id": trade.id, "charges": charges_data
            }

        # 3. LONG ENTRY: BUY
        elif side in ["BUY"]:
            if position and position.quantity > 0:
                return {"success": False, "message": f"Position already exists for {symbol}. Intraday averaging is disabled."}
            charges_data = PaperTradingEngine.calculate_transaction_charges(turnover, "BUY")
            total_charges = charges_data["total_charges"]
            margin_required = round((turnover / AfterHoursPaperTradingEngine.LEVERAGE) + total_charges, 2)
            if account.cash_balance < margin_required:
                return {"success": False, "message": f"Insufficient margin (5x Leverage). Required: ₹{margin_required:,.2f}, Available: ₹{account.cash_balance:,.2f}"}
            account.cash_balance -= margin_required
            if not position:
                position = AfterHoursPosition(
                    symbol=symbol, side="LONG", quantity=quantity, average_price=price,
                    peak_price=price, stop_loss=0.0, take_profit=0.0, trailing_active=False
                )
                db.session.add(position)
            else:
                position.quantity = quantity
                position.average_price = price
                position.side = "LONG"
                position.peak_price = price
            trade = AfterHoursTradeHistory(
                symbol=symbol, side="BUY", quantity=quantity, price=price, total=turnover,
                realized_pnl=0.0, charges=total_charges, brokerage=charges_data["brokerage"],
                stt=charges_data["stt"], exchange_charges=charges_data["exchange_charges"],
                sebi_charges=charges_data["sebi_charges"], gst=charges_data["gst"],
                stamp_duty=charges_data["stamp_duty"], net_total=margin_required
            )
            db.session.add(trade)
            db.session.commit()
            prune_old_trades(AfterHoursTradeHistory, 100)
            return {
                "success": True, "action": "BUY", "price": price, "entry_price": price, "realized_pnl": 0.0,
                "message": f"Bought {quantity} {symbol} @ ₹{price:.2f} [5x Leverage | Margin: ₹{margin_required:,.2f}]",
                "trade_id": trade.id, "charges": charges_data
            }

        # 4. SHORT ENTRY: SHORT
        elif side in ["SELL", "SHORT"]:
            if position and position.quantity > 0:
                return {"success": False, "message": f"Position already exists for {symbol}. Intraday averaging is disabled."}
            charges_data = PaperTradingEngine.calculate_transaction_charges(turnover, "SELL")
            total_charges = charges_data["total_charges"]
            margin_required = round((turnover / AfterHoursPaperTradingEngine.LEVERAGE) + total_charges, 2)
            if account.cash_balance < margin_required:
                return {"success": False, "message": f"Insufficient margin (5x Leverage). Required: ₹{margin_required:,.2f}, Available: ₹{account.cash_balance:,.2f}"}
            account.cash_balance -= margin_required
            if not position:
                position = AfterHoursPosition(
                    symbol=symbol, side="SHORT", quantity=quantity, average_price=price,
                    peak_price=price, stop_loss=0.0, take_profit=0.0, trailing_active=False
                )
                db.session.add(position)
            else:
                position.quantity = quantity
                position.average_price = price
                position.side = "SHORT"
                position.peak_price = price
            trade = AfterHoursTradeHistory(
                symbol=symbol, side="SHORT", quantity=quantity, price=price, total=turnover,
                realized_pnl=0.0, charges=total_charges, brokerage=charges_data["brokerage"],
                stt=charges_data["stt"], exchange_charges=charges_data["exchange_charges"],
                sebi_charges=charges_data["sebi_charges"], gst=charges_data["gst"],
                stamp_duty=charges_data["stamp_duty"], net_total=margin_required
            )
            db.session.add(trade)
            db.session.commit()
            prune_old_trades(AfterHoursTradeHistory, 100)
            return {
                "success": True, "action": "SHORT", "price": price, "entry_price": price, "realized_pnl": 0.0,
                "message": f"Shorted {quantity} {symbol} @ ₹{price:.2f} [5x Leverage | Margin: ₹{margin_required:,.2f}]",
                "trade_id": trade.id, "charges": charges_data
            }

