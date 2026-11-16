with open('app/templates/bot.html', encoding='utf-8') as f:
    text = f.read()

ids = [
    'broker-ticket-modal', 'ticket-copy-text', 'btn-ticket-copy', 
    'ticket-kite-link', 'btn-ticket-zerodha', 'ticket-tv-link',
    'chip-btn-symbol', 'chip-val-symbol',
    'chip-btn-qty', 'chip-val-qty',
    'chip-btn-price', 'chip-val-price',
    'chip-btn-sl', 'chip-val-sl',
    'chip-btn-tp', 'chip-val-tp'
]

for i in ids:
    if f'id="{i}"' not in text:
        print('MISSING in bot.html:', i)

print('Done checking bot.html')

with open('app/templates/bot_after_hours.html', encoding='utf-8') as f:
    text_ah = f.read()

for i in ids:
    if f'id="{i}"' not in text_ah:
        print('MISSING in bot_after_hours.html:', i)

print('Done checking bot_after_hours.html')

