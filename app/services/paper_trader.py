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
                "unrealized_pnl_pct": round(unrealized_pnl_pct, 2)
            })
            
        total_equity = account.cash_balance + total_market_value
        
        return {
            "cash_balance": round(account.cash_balance, 2),
            "market_value": round(total_market_value, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "positions": portfolio_positions
        }

    @staticmethod
    def execute_order(symbol: str, side: str, quantity: float):
        symbol = symbol.strip().upper()
        side = side.strip().upper()
        quantity = float(quantity)
        
        if quantity <= 0:
            return {"success": False, "message": "Quantity must be greater than zero"}
            
        if side not in ["BUY", "SELL"]:
            return {"success": False, "message": "Side must be BUY or SELL"}
            
        quote = MarketDataService.get_quote(symbol)
        if "error" in quote:
            return {"success": False, "message": f"Could not fetch price: {quote['error']}"}
            
        price = quote["price"]
        total_cost = round(quantity * price, 2)
        account = Account.query.first()
        
        if side == "BUY":
            if account.cash_balance < total_cost:
                return {
                    "success": False, 
                    "message": f"Insufficient funds. Required: ${total_cost:,.2f}, Available: ${account.cash_balance:,.2f}"
                }
            
            account.cash_balance -= total_cost
            position = Position.query.filter_by(symbol=symbol).first()
            if not position:
                position = Position(symbol=symbol, quantity=quantity, average_price=price)
                db.session.add(position)
            else:
                new_qty = position.quantity + quantity
                new_avg = ((position.quantity * position.average_price) + total_cost) / new_qty
                position.quantity = new_qty
                position.average_price = new_avg
                
            trade = TradeHistory(
                symbol=symbol,
                side="BUY",
                quantity=quantity,
                price=price,
                total=total_cost,
                realized_pnl=0.0
            )
            db.session.add(trade)
            db.session.commit()
            return {"success": True, "message": f"Bought {quantity} {symbol} @ ${price:.2f}", "trade_id": trade.id}
            
        elif side == "SELL":
            position = Position.query.filter_by(symbol=symbol).first()
            if not position or position.quantity < quantity:
                available = position.quantity if position else 0
                return {"success": False, "message": f"Insufficient position. Available: {available}, Requested: {quantity}"}
            
            cost_basis = quantity * position.average_price
            realized_pnl = total_cost - cost_basis
            
            account.cash_balance += total_cost
            position.quantity -= quantity
            
            if position.quantity == 0:
                db.session.delete(position)
                
            trade = TradeHistory(
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                price=price,
                total=total_cost,
                realized_pnl=round(realized_pnl, 2)
            )
            db.session.add(trade)
            db.session.commit()
            return {"success": True, "message": f"Sold {quantity} {symbol} @ ${price:.2f}. Realized PnL: ${realized_pnl:,.2f}", "trade_id": trade.id}

