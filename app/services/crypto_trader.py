from datetime import datetime, timezone
from app import db
from app.models.trading import CryptoAccount, CryptoPosition, CryptoTradeHistory, CRYPTO_DEFAULT_PAIRS
from app.services.market_data import MarketDataService

class CryptoTradingEngine:
    @staticmethod
    def get_account():
        acc = CryptoAccount.query.first()
        if not acc:
            acc = CryptoAccount(usdt_balance=50000.0)
            db.session.add(acc)
            db.session.commit()
        return acc

    @staticmethod
    def get_crypto_markets():
        """Fetches live quotes for default crypto pairs with 24h stats"""
        markets = []
        for symbol in CRYPTO_DEFAULT_PAIRS:
            try:
                chart, sym = MarketDataService._fetch_chart_data(symbol, interval="1d", range_val="5d")
                if not chart:
                    continue
                meta = chart.get('meta', {})
                current_price = meta.get('regularMarketPrice')
                prev_close = meta.get('chartPreviousClose') or meta.get('previousClose')
                
                if current_price is None:
                    quotes = chart.get('indicators', {}).get('quote', [{}])[0]
                    closes = [c for c in quotes.get('close', []) if c is not None]
                    current_price = closes[-1] if closes else 0.0
                    prev_close = closes[-2] if len(closes) > 1 else current_price

                if not prev_close:
                    prev_close = current_price

                change = current_price - prev_close
                change_pct = (change / prev_close * 100) if prev_close else 0.0
                day_high = meta.get('regularMarketDayHigh') or current_price
                day_low = meta.get('regularMarketDayLow') or current_price
                volume = meta.get('regularMarketVolume') or 0

                name = symbol.replace('-USD', '')
                markets.append({
                    "symbol": symbol,
                    "name": name,
                    "price": round(float(current_price), 4 if current_price < 1 else 2),
                    "change": round(float(change), 4 if abs(change) < 1 else 2),
                    "change_pct": round(float(change_pct), 2),
                    "day_high": round(float(day_high), 4 if day_high < 1 else 2),
                    "day_low": round(float(day_low), 4 if day_low < 1 else 2),
                    "volume": int(volume),
                    "currency": "$"
                })
            except Exception:
                continue
        return markets

    @staticmethod
    def get_portfolio_summary():
        account = CryptoTradingEngine.get_account()
        positions = CryptoPosition.query.all()
        
        pos_list = []
        total_margin_used = 0.0
        total_unrealized_pnl = 0.0

        for pos in positions:
            q = MarketDataService.get_quote(pos.symbol)
            cur_price = q.get('price', pos.entry_price) if "error" not in q else pos.entry_price
            
            # Unrealized P&L
            if pos.side == 'LONG':
                pnl = (cur_price - pos.entry_price) * pos.quantity
            else:  # SHORT
                pnl = (pos.entry_price - cur_price) * pos.quantity

            margin = pos.margin if pos.margin > 0 else (pos.quantity * pos.entry_price) / max(pos.leverage, 1)
            roi_pct = (pnl / margin * 100) if margin > 0 else 0.0

            total_margin_used += margin
            total_unrealized_pnl += pnl

            pos_list.append({
                "id": pos.id,
                "symbol": pos.symbol,
                "name": pos.symbol.replace('-USD', ''),
                "side": pos.side,
                "quantity": round(float(pos.quantity), 6),
                "entry_price": round(float(pos.entry_price), 4 if pos.entry_price < 1 else 2),
                "current_price": round(float(cur_price), 4 if cur_price < 1 else 2),
                "leverage": pos.leverage,
                "margin": round(float(margin), 2),
                "liquidation_price": round(float(pos.liquidation_price), 4 if pos.liquidation_price < 1 else 2),
                "stop_loss": round(float(pos.stop_loss or 0.0), 2),
                "take_profit": round(float(pos.take_profit or 0.0), 2),
                "unrealized_pnl": round(float(pnl), 2),
                "roi_pct": round(float(roi_pct), 2)
            })

        total_equity = account.usdt_balance + total_margin_used + total_unrealized_pnl

        return {
            "usdt_balance": round(account.usdt_balance, 2),
            "margin_used": round(total_margin_used, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "positions": pos_list
        }

    @staticmethod
    def execute_order(symbol: str, side: str, quantity: float, leverage: int = 1,
                      order_type: str = "MARKET", limit_price: float = 0.0,
                      stop_loss: float = 0.0, take_profit: float = 0.0):
        symbol = symbol.strip().upper()
        if not symbol.endswith('-USD') and '.' not in symbol:
            symbol = f"{symbol}-USD"

        side = side.strip().upper()  # BUY, SELL, SHORT
        quantity = float(quantity)
        leverage = max(1, min(int(leverage), 20))  # 1x to 20x

        if quantity <= 0:
            return {"success": False, "message": "Quantity must be greater than zero."}

        account = CryptoTradingEngine.get_account()

        # Fetch execution price
        if order_type.upper() == "LIMIT" and limit_price > 0:
            exec_price = float(limit_price)
        else:
            q = MarketDataService.get_quote(symbol)
            if "error" in q:
                return {"success": False, "message": f"Unable to fetch market price for {symbol}."}
            exec_price = float(q["price"])

        notional_value = quantity * exec_price
        required_margin = notional_value / leverage
        fee = notional_value * 0.0005  # 0.05% exchange fee

        # Check existing positions for closing or flipping
        existing_pos = CryptoPosition.query.filter_by(symbol=symbol).first()

        # If user wants to SELL and holds a LONG, or user BUYs and holds a SHORT -> Close/Reduce position
        if existing_pos and ((side in ["SELL", "CLOSE"] and existing_pos.side == "LONG") or
                             (side in ["BUY", "CLOSE"] and existing_pos.side == "SHORT")):
            close_qty = min(quantity, existing_pos.quantity)
            close_ratio = close_qty / existing_pos.quantity

            if existing_pos.side == "LONG":
                realized_pnl = (exec_price - existing_pos.entry_price) * close_qty
            else:
                realized_pnl = (existing_pos.entry_price - exec_price) * close_qty

            released_margin = existing_pos.margin * close_ratio
            close_fee = (close_qty * exec_price) * 0.0005

            net_credit = released_margin + realized_pnl - close_fee
            account.usdt_balance += net_credit

            trade = CryptoTradeHistory(
                symbol=symbol,
                side=f"CLOSE_{existing_pos.side}",
                quantity=close_qty,
                price=exec_price,
                leverage=existing_pos.leverage,
                total=close_qty * exec_price,
                margin=released_margin,
                fee=close_fee,
                realized_pnl=realized_pnl
            )
            db.session.add(trade)

            if close_qty >= existing_pos.quantity:
                db.session.delete(existing_pos)
            else:
                existing_pos.quantity -= close_qty
                existing_pos.margin -= released_margin

            db.session.commit()
            return {
                "success": True,
                "message": f"Successfully closed {close_qty} {symbol} @ ${exec_price:,.2f}. Realized P&L: {'+' if realized_pnl >= 0 else ''}${realized_pnl:,.2f}",
                "realized_pnl": realized_pnl,
                "account": CryptoTradingEngine.get_portfolio_summary()
            }

        # Otherwise: Open a new position (LONG for BUY, SHORT for SHORT/SELL)
        order_side = "SHORT" if side in ["SHORT", "SELL"] else "LONG"

        if account.usdt_balance < (required_margin + fee):
            return {
                "success": False,
                "message": f"Insufficient USDT Balance. Required: ${(required_margin + fee):,.2f}, Available: ${account.usdt_balance:,.2f}"
            }

        account.usdt_balance -= (required_margin + fee)

        # Liquidation price estimation
        if order_side == "LONG":
            liq_price = exec_price * (1.0 - (0.9 / leverage)) if leverage > 1 else 0.0
        else:
            liq_price = exec_price * (1.0 + (0.9 / leverage)) if leverage > 1 else exec_price * 2.0

        if existing_pos and existing_pos.side == order_side:
            # Average down / scale into existing position
            new_qty = existing_pos.quantity + quantity
            avg_price = ((existing_pos.quantity * existing_pos.entry_price) + (quantity * exec_price)) / new_qty
            existing_pos.quantity = new_qty
            existing_pos.entry_price = avg_price
            existing_pos.margin += required_margin
            existing_pos.liquidation_price = liq_price
            if stop_loss > 0: existing_pos.stop_loss = stop_loss
            if take_profit > 0: existing_pos.take_profit = take_profit
        else:
            new_pos = CryptoPosition(
                symbol=symbol,
                side=order_side,
                quantity=quantity,
                entry_price=exec_price,
                leverage=leverage,
                margin=required_margin,
                liquidation_price=liq_price,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
            db.session.add(new_pos)

        trade = CryptoTradeHistory(
            symbol=symbol,
            side=f"OPEN_{order_side}",
            quantity=quantity,
            price=exec_price,
            leverage=leverage,
            total=notional_value,
            margin=required_margin,
            fee=fee,
            realized_pnl=0.0
        )
        db.session.add(trade)
        db.session.commit()

        return {
            "success": True,
            "message": f"Opened {leverage}x {order_side} {quantity} {symbol} @ ${exec_price:,.2f}",
            "order": {
                "symbol": symbol,
                "side": order_side,
                "quantity": quantity,
                "price": exec_price,
                "leverage": leverage,
                "margin": required_margin,
                "fee": fee
            },
            "account": CryptoTradingEngine.get_portfolio_summary()
        }

    @staticmethod
    def close_position_by_id(position_id: int):
        pos = CryptoPosition.query.get(position_id)
        if not pos:
            return {"success": False, "message": "Position not found."}

        q = MarketDataService.get_quote(pos.symbol)
        cur_price = float(q.get("price", pos.entry_price)) if "error" not in q else pos.entry_price

        if pos.side == "LONG":
            pnl = (cur_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - cur_price) * pos.quantity

        fee = (pos.quantity * cur_price) * 0.0005
        net_credit = pos.margin + pnl - fee

        account = CryptoTradingEngine.get_account()
        account.usdt_balance += net_credit

        trade = CryptoTradeHistory(
            symbol=pos.symbol,
            side=f"CLOSE_{pos.side}",
            quantity=pos.quantity,
            price=cur_price,
            leverage=pos.leverage,
            total=pos.quantity * cur_price,
            margin=pos.margin,
            fee=fee,
            realized_pnl=pnl
        )
        db.session.add(trade)
        db.session.delete(pos)
        db.session.commit()

        return {
            "success": True,
            "message": f"Closed {pos.side} {pos.symbol} @ ${cur_price:,.2f}. P&L: {'+' if pnl >= 0 else ''}${pnl:,.2f}",
            "realized_pnl": pnl,
            "account": CryptoTradingEngine.get_portfolio_summary()
        }

    @staticmethod
    def reset_account():
        CryptoPosition.query.delete()
        CryptoTradeHistory.query.delete()
        acc = CryptoTradingEngine.get_account()
        acc.usdt_balance = 50000.0
        db.session.commit()
        return {"success": True, "message": "Crypto desk successfully reset to $50,000.00 USDT!"}
