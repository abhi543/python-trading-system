import sys, os
sys.path.insert(0, os.getcwd())
from run import app
from app.services.bot import bot_instance

with app.app_context():
    bot_instance.is_running = True
    bot_instance.strategy = '123_REVERSAL'
    bot_instance.strict_ai_mode = False
    bot_instance.use_ai_filter = False
    bot_instance.use_volume_filter = False
    bot_instance.use_ema200_filter = False
    bot_instance.use_nifty_filter = False
    res = bot_instance.evaluate_signals(simulation_event='LIVE_SWEEP')
    print('Scan Mode:', res.get('scan_mode'))
    print('Total Scanned:', res.get('total_scanned'))
    print('Buys Found:', res.get('buys_found'))
    print('Sells Found:', res.get('sells_found'))
    print('Recent Sweep Trades:', res.get('recent_sweep_trades'))
    for r in res.get('scanner_results', [])[:10]:
        print(f"  {r['symbol']} [{r['signal']}]: action={r.get('action_executed')}, reason={r.get('reason')}")
