import os
from dotenv import load_dotenv

load_dotenv()

# Configure cache directories to /tmp for serverless environments (e.g. Vercel)
if any(k in os.environ for k in ('VERCEL', 'VERCEL_ENV', 'AWS_LAMBDA_FUNCTION_NAME', 'LAMBDA_TASK_ROOT')):
    os.environ.setdefault('TMPDIR', '/tmp')
    os.environ.setdefault('YFINANCE_CACHE_DIR', '/tmp/py-yfinance')

from app import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Trading System running on http://127.0.0.1:{port}")
    app.run(host='127.0.0.1', port=port, debug=True)
