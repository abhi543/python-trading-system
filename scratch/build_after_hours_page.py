with open('app/templates/bot.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace titles and branding
content = content.replace('{% block title %}NIFTY Auto-Trading Bot | ALGO-TRADE Pro{% endblock %}', '{% block title %}NIFTY After-Hours Bot | ALGO-TRADE Pro{% endblock %}')
content = content.replace('NIFTY 50 Auto-Trading Bot Cockpit', 'NIFTY 50 After-Hours Strategy Lab & Bot')
content = content.replace('Fully automated algorithmic trade execution, live risk management and order routing across NIFTY 50 stocks.', 'Dedicated 24/7 After-Hours testing ground with isolated rules, simulated price walks, and independent session reporting.')
content = content.replace('SESSION: LIVE NSE MARKET SESSION (09:15 - 15:30 IST)', 'SESSION: AFTER-HOURS SIMULATION ENGINE (24/7)')
content = content.replace('SESSION: AFTER-HOURS ENGINE (24/7)', 'SESSION: AFTER-HOURS SIMULATION ENGINE (24/7)')
content = content.replace('START NIFTY BOT', 'START AFTER-HOURS BOT')
content = content.replace('SCAN BASKET NOW', 'SIMULATE BASKET SWEEP')
content = content.replace('SCAN ALL 200 NOW', 'SWEEP 200 STOCKS NOW')

# Replace API endpoints
content = content.replace('/api/bot/status', '/api/after-hours/status')
content = content.replace('/api/bot/config', '/api/after-hours/config')
content = content.replace('/api/bot/start', '/api/after-hours/start')
content = content.replace('/api/bot/stop', '/api/after-hours/stop')
content = content.replace('/api/bot/tick', '/api/after-hours/tick')
content = content.replace('/api/bot/reset-and-restart', '/api/after-hours/reset')
content = content.replace('/api/bot/session-report', '/api/after-hours/session-report')

# Replace storage keys
content = content.replace('nifty_bot_active', 'ah_bot_active_v1')
content = content.replace('nifty_bot_config_v2', 'ah_bot_config_v1')
content = content.replace('nifty_bot_session_v3', 'ah_bot_session_v1')
content = content.replace('nifty_active_holdings_v3', 'ah_active_holdings_v1')
content = content.replace('nifty_positions_entry_lock_v1', 'ah_positions_entry_lock_v1')

# Add After-Hours Lab Notice Banner right under main-cockpit
banner_html = '''
        <!-- AFTER-HOURS STRATEGY LAB ISOLATION BANNER -->
        <div class="after-hours-banner" style="background: linear-gradient(135deg, rgba(255, 209, 102, 0.12) 0%, rgba(167, 139, 250, 0.12) 100%); border: 1px solid rgba(255, 209, 102, 0.35); border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1.5rem; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 4px 15px rgba(0,0,0,0.3);">
            <div>
                <h4 style="margin: 0; color: #ffd166; font-size: 1.05rem; display: flex; align-items: center; gap: 8px;">
                    <i class="fa-solid fa-moon"></i> AFTER-HOURS STRATEGY LAB &amp; 24/7 ENGINE
                </h4>
                <p style="margin: 0.35rem 0 0; color: #cbd5e1; font-size: 0.85rem;">
                    This is your dedicated After-Hours workspace. Rules, trade sizes, strategies, and session logs set on this page are <strong>100% isolated</strong> from the Live Market Bot.
                </p>
            </div>
            <span class="badge" style="background: rgba(255, 209, 102, 0.2); color: #ffd166; border: 1px solid rgba(255, 209, 102, 0.45); font-weight: 800; padding: 6px 14px; border-radius: 6px; letter-spacing: 0.5px;">
                <i class="fa-solid fa-shield-halved"></i> ISOLATED ENVIRONMENT
            </span>
        </div>
'''

content = content.replace('<div class="bot-cockpit">', '<div class="bot-cockpit">' + banner_html)

with open('app/templates/bot_after_hours.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('bot_after_hours.html created successfully!')

