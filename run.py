import os
from dotenv import load_dotenv
from app import create_app

load_dotenv()

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Trading System running on http://127.0.0.1:{port}")
    app.run(host='127.0.0.1', port=port, debug=True)

