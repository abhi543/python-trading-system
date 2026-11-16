path = 'app/services/market_data.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target = '''        # Option A: Volume Surge Ratio (vs 20-day Average Volume)
        vol_ratio = 1.0
        if len(df) >= 20:
            vol_avg20 = float(df['volume'].rolling(20).mean().iloc[-1])
            curr_vol = float(df['volume'].iloc[-1])
            if vol_avg20 > 0:
                vol_ratio = round(curr_vol / vol_avg20, 2)'''

replacement = '''        # Option A: Volume Surge Ratio (vs 20-day Average Volume pro-rated to elapsed market session)
        vol_ratio = 1.0
        if len(df) >= 20:
            vol_avg20 = float(df['volume'].rolling(20).mean().iloc[-1])
            curr_vol = float(df['volume'].iloc[-1])
            if vol_avg20 > 0:
                from datetime import datetime, timezone, timedelta
                now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
                market_mins = (now_ist.hour * 60 + now_ist.minute) - (9 * 60 + 15)
                # If during active NSE market hours (09:15 - 15:30 IST = 375 minutes)
                if 0 <= market_mins <= 375 and now_ist.weekday() < 5:
                    day_fraction = max(0.08, min(1.0, market_mins / 375.0))
                    expected_vol = vol_avg20 * day_fraction
                    vol_ratio = round(curr_vol / expected_vol, 2)
                else:
                    vol_ratio = round(curr_vol / vol_avg20, 2)'''

assert target in content, "Target not found in market_data.py"
content = content.replace(target, replacement)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("market_data.py updated cleanly!")
