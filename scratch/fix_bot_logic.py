import re

path = 'app/services/bot.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove forced strategy and scan_mode overwrites
pattern1 = r'''        self\.strategy = persisted\.get\("strategy", default_strat\)\s+if self\.strategy in \["123_REVERSAL", "SUPER_TREND"\]:\s+self\.strategy = "KRONOS_AI"\s+if self\.scan_mode in \["ALL_NIFTY_200", "TOP_200_TURNOVER"\]:\s+self\.scan_mode = "KRONOS_UNIVERSE"'''

replacement1 = '''        self.strategy = persisted.get("strategy", default_strat)'''

new_content, count1 = re.subn(pattern1, replacement1, content)
print(f"Applied replacement 1 (remove forced overwrites): count={count1}")

# 2. Default strict_ai_mode to False
pattern2 = r'self\.strict_ai_mode = bool\(persisted\.get\("strict_ai_mode", True\)\)'
replacement2 = 'self.strict_ai_mode = bool(persisted.get("strict_ai_mode", False))'
new_content, count2 = re.subn(pattern2, replacement2, new_content)
print(f"Applied replacement 2 (default strict_ai_mode False): count={count2}")

# 3. Dynamic trend projection in KRONOS_AI when strict_ai_mode is False
pattern3 = r'''(if self\.strategy == "KRONOS_AI":[\s\S]*?)(else:\s+signal = "HOLD"\s+reason = "Kronos AI: No active directional projection")'''

replacement3 = r'''\1elif not getattr(self, "strict_ai_mode", False):
                # When Strict AI is OFF, dynamically project directional moves across universe stocks
                if trend_200 == "BULLISH" and change_pct > 0:
                    signal = "BUY"
                    ai_exp_pct = max(0.8, min(3.5, round(change_pct * 1.25, 2)))
                    ai_bias = "BULLISH"
                    suggested_sl = round(current_price * 0.99, 2)
                    suggested_tp = round(current_price * (1.0 + ai_exp_pct / 100.0), 2)
                    reason = f"🔮 Kronos Dynamic Trend Buy (+{ai_exp_pct:.2f}%) | Target: ₹{suggested_tp:.2f}"
                elif trend_200 == "BEARISH" and change_pct < 0:
                    signal = "SELL"
                    ai_exp_pct = max(-3.5, min(-0.8, round(change_pct * 1.25, 2)))
                    ai_bias = "BEARISH"
                    suggested_sl = round(current_price * 1.01, 2)
                    suggested_tp = round(current_price * (1.0 + ai_exp_pct / 100.0), 2)
                    reason = f"🔮 Kronos Dynamic Trend Short ({ai_exp_pct:.2f}%) | Target: ₹{suggested_tp:.2f}"
                else:
                    signal = "HOLD"
                    reason = "Kronos AI: Neutral momentum consolidation"
            \2'''

new_content, count3 = re.subn(pattern3, replacement3, new_content)
print(f"Applied replacement 3 (dynamic trend projection in KRONOS_AI): count={count3}")

assert count1 > 0, "Failed to apply replacement 1"
assert count2 > 0, "Failed to apply replacement 2"
assert count3 > 0, "Failed to apply replacement 3"

with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("SUCCESS: bot.py updated cleanly!")
