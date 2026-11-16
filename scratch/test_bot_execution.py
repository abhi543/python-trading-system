import sys, os
sys.path.insert(0, os.getcwd())
from run import app
from app.services.bot import AutoTradingBot, bot_instance, after_hours_bot_instance

with app.app_context():
    print("=== TEST 1: UPDATE CONFIG TO HYBRID / 123_REVERSAL / TOP_200 ===")
    cfg = {
        "strategy": "123_REVERSAL",
        "scan_mode": "TOP_200_TURNOVER",
        "strict_ai_mode": False,
        "is_running": True
    }
    bot_instance.update_config(cfg)
    bot_instance.is_running = True
    bot_instance.persist_state()

    print(f"ALPHA strategy: {bot_instance.strategy}")
    print(f"ALPHA scan_mode: {bot_instance.scan_mode}")
    print(f"ALPHA strict_ai_mode: {bot_instance.strict_ai_mode}")

    assert bot_instance.strategy == "123_REVERSAL", f"Expected 123_REVERSAL, got {bot_instance.strategy}"
    assert bot_instance.scan_mode == "TOP_200_TURNOVER", f"Expected TOP_200_TURNOVER, got {bot_instance.scan_mode}"
    assert bot_instance.strict_ai_mode is False, f"Expected False, got {bot_instance.strict_ai_mode}"

    print("\n=== TEST 2: REINITIALIZATION / COLD START PERSISTENCE ===")
    # Create fresh bot instance loading from persistent file
    reloaded_bot = AutoTradingBot(state_filename="trading_bot_state.json", name="ALPHA", is_after_hours_forced=False)
    print(f"Reloaded strategy: {reloaded_bot.strategy}")
    print(f"Reloaded scan_mode: {reloaded_bot.scan_mode}")
    print(f"Reloaded strict_ai_mode: {reloaded_bot.strict_ai_mode}")

    assert reloaded_bot.strategy == "123_REVERSAL", "Auto-revert bug still present on strategy!"
    assert reloaded_bot.scan_mode == "TOP_200_TURNOVER", "Auto-revert bug still present on scan_mode!"
    assert reloaded_bot.strict_ai_mode is False, "Auto-revert bug still present on strict_ai_mode!"

    print("\n=== TEST 3: DYNAMIC AI TREND SCANNING WHEN STRATEGY IS KRONOS_AI ===")
    cfg_ai = {
        "strategy": "KRONOS_AI",
        "scan_mode": "TOP_200_TURNOVER",
        "strict_ai_mode": False,
        "is_running": True
    }
    bot_instance.update_config(cfg_ai)
    bot_instance.is_running = True
    
    # Evaluate single stock outside 19 list, e.g. BDL
    fake_stock_data = {
        "price": 1050.0,
        "percent_change": 1.5,
        "closes": [1000 + i for i in range(30)],
        "highs": [1005 + i for i in range(30)],
        "lows": [995 + i for i in range(30)],
        "volumes": [100000] * 30,
        "ema_200": 980.0,
        "trend_200": "BULLISH",
        "vol_ratio": 1.5,
        "atr_14": 15.0
    }
    eval_res = bot_instance.evaluate_single_stock("BDL.NS", {}, fake_stock_data, allow_order_execution=True)
    print(f"BDL.NS in KRONOS_AI (strict_ai=False): signal={eval_res.get('signal')}, bias={eval_res.get('ai_bias')}, reason={eval_res.get('reason')}")
    assert eval_res.get("signal") == "BUY", f"Expected BUY, got {eval_res.get('signal')}"
    assert "BULLISH" in eval_res.get("ai_bias", ""), f"Expected BULLISH bias, got {eval_res.get('ai_bias')}"

    print("\nALL BOT LOGIC & PERSISTENCE TESTS PASSED CLEANLY!")
