import requests

status = requests.get('https://python-trading-system.vercel.app/api/bot/status').json()
results = status.get('scan_summary', {}).get('scanner_results', [])
print(f"Strategy: {status.get('strategy')}")
print(f"Active Positions Count: {status.get('scan_summary', {}).get('active_positions_count')}")
for r in results:
    sym = r.get('symbol')
    sig = r.get('signal')
    act = r.get('action_executed')
    reas = r.get('reason')
    bias = r.get('ai_bias')
    exp = r.get('ai_expected_pct')
    print(f"{sym:<15} | Bias: {bias:<14} ({exp}%) | Sig: {sig:<6} | Act: {str(act):<18} | Reason: {reas}")
