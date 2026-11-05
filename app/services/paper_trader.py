from app import db
from app.models.trading import Account, Position, TradeHistory
from app.services.market_data import MarketDataService

class PaperTradingEngine:
    @staticmethod
    def get_portfolio_summary():
        account = Account.query.first()
        if not account:
            return {"error": "Account not initialized"}
        
        positions = Position.query.filter(Position.quantity > 0).all()
        portfolio_positions = []
        total_market_value = 0.0
        total_unrealized_pnl = 0.0
        
        for pos in positions:
            quote = MarketDataService.get_quote(pos.symbol)
            current_price = quote.get("price", pos.average_price) if "error" not in quote else pos.average_price
            
            market_value = pos.quantity * current_price
            cost_basis = pos.quantity * pos.average_price
            unrealized_pnl = market_value - cost_basis
            unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0.0
            
            total_market_value += market_value
            total_unrealized_pnl += unrealized_pnl
            
            portfolio_positions.append({
                "symbol": pos.symbol,
                "quantity": pos.quantity,
                "average_price": round(pos.average_price, 2),
                "current_price": round(current_price, 2),
                "market_value": round(market_value, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                "peak_price": round(getattr(pos, 'peak_price', 0.0) or pos.average_price, 2),
                "stop_loss": round(getattr(pos, 'stop_loss', 0.0) or 0.0, 2),
                "take_profit": round(getattr(pos, 'take_profit', 0.0) or 0.0, 2),
                "trailing_active": bool(getattr(pos, 'trailing_active', False))
            })
            
        total_equity = account.cash_balance + total_market_value
        
        account_data = {
            "cash_balance": round(account.cash_balance, 2),
            "market_value": round(total_market_value, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2)
        }

        return {
            "cash_balance": round(account.cash_balance, 2),
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
    def execute_order(symbol: str, side: str, quantity: float, execution_price: float = None):
        symbol = symbol.strip().upper()
        side = side.strip().upper()
        quantity = float(quantity)
        
        if quantity <= 0:
            return {"success": False, "message": "Quantity must be greater than zero"}
            
        if side not in ["BUY", "SELL"]:
            return {"success": False, "message": "Side must be BUY or SELL"}
            
        if execution_price is not None and execution_price > 0:
            price = round(float(execution_price), 2)
        else:
            quote = MarketDataService.get_quote(symbol)
            if "error" in quote:
                return {"success": False, "message": f"Could not fetch price: {quote['error']}"}
            price = quote["price"]
            
        turnover = round(quantity * price, 2)
        account = Account.query.first()
        if not account:
            return {"success": False, "message": "Account not initialized"}

        charges_data = PaperTradingEngine.calculate_transaction_charges(turnover, side)
        total_charges = charges_data["total_charges"]
        
        if side == "BUY":
            net_required = round(turnover + total_charges, 2)
            if account.cash_balance < net_required:
                return {
                    "success": False, 
                    "message": f"Insufficient funds. Required: ₹{net_required:,.2f} (Turnover: ₹{turnover:,.2f} + Charges: ₹{total_charges:.2f}), Available: ₹{account.cash_balance:,.2f}"
                }
            
            account.cash_balance -= net_required
            position = Position.query.filter_by(symbol=symbol).first()
            if not position:
                position = Position(
                    symbol=symbol, 
                    quantity=quantity, 
                    average_price=price,
                    peak_price=price,
                    stop_loss=0.0,
                    take_profit=0.0,
                    trailing_active=False
                )
                db.session.add(position)
            else:
                new_qty = position.quantity + quantity
                new_avg = ((position.quantity * position.average_price) + turnover) / new_qty
                position.quantity = new_qty
                position.average_price = new_avg
                if hasattr(position, 'peak_price'):
                    position.peak_price = max(position.peak_price or 0.0, price)
                
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
                net_total=net_required
            )
            db.session.add(trade)
            db.session.commit()
            return {
                "success": True, 
                "message": f"Bought {quantity} {symbol} @ ₹{price:.2f} (Charges: ₹{total_charges:.2f})", 
                "trade_id": trade.id,
                "charges": charges_data
            }
            
        elif side == "SELL":
            position = Position.query.filter_by(symbol=symbol).first()
            if not position or position.quantity < quantity:
                available = position.quantity if position else 0
                return {"success": False, "message": f"Insufficient position. Available: {available}, Requested: {quantity}"}
            
            net_proceeds = round(turnover - total_charges, 2)
            cost_basis = round(quantity * position.average_price, 2)
            gross_pnl = turnover - cost_basis
            net_realized_pnl = round(gross_pnl - total_charges, 2)
            
            account.cash_balance += net_proceeds
            position.quantity -= quantity
            
            if position.quantity == 0:
                db.session.delete(position)
                
            trade = TradeHistory(
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                price=price,
                total=turnover,
                realized_pnl=net_realized_pnl,
                charges=total_charges,
                brokerage=charges_data["brokerage"],
                stt=charges_data["stt"],
                exchange_charges=charges_data["exchange_charges"],
                sebi_charges=charges_data["sebi_charges"],
                gst=charges_data["gst"],
                stamp_duty=charges_data["stamp_duty"],
                net_total=net_proceeds
            )
            db.session.add(trade)
            db.session.commit()
            return {
                "success": True, 
                "message": f"Sold {quantity} {symbol} @ ₹{price:.2f}. Net Realized P&L: ₹{net_realized_pnl:,.2f} (Charges: ₹{total_charges:.2f})", 
                "trade_id": trade.id,
                "charges": charges_data
            }

