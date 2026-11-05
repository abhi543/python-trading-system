import os
import sys
import traceback
from werkzeug.exceptions import HTTPException

# Configure cache directories to /tmp for serverless environments
if any(k in os.environ for k in ('VERCEL', 'VERCEL_ENV', 'AWS_LAMBDA_FUNCTION_NAME', 'LAMBDA_TASK_ROOT')):
    os.environ.setdefault('TMPDIR', '/tmp')
    os.environ.setdefault('YFINANCE_CACHE_DIR', '/tmp/py-yfinance')

# Load local .env if available, but never crash in production/serverless if absent
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from app import create_app
    app = create_app()

    @app.errorhandler(404)
    def handle_404(e):
        from flask import request
        return {
            "error": "Not Found",
            "request_path": request.path,
            "raw_uri": request.environ.get('RAW_URI'),
            "x_forwarded_uri": request.environ.get('HTTP_X_FORWARDED_URI'),
            "x_matched_path": request.environ.get('HTTP_X_MATCHED_PATH')
        }, 404

    @app.errorhandler(Exception)
    def handle_error(e):
        if isinstance(e, HTTPException):
            return e
        tb = traceback.format_exc()
        from flask import Response
        return Response(f"Internal Server Error:\n\n{tb}", status=500, mimetype="text/plain")

except Exception as err:
    startup_error = traceback.format_exc()
    print("FATAL STARTUP ERROR:\n", startup_error, file=sys.stderr)
    from flask import Flask, Response
    app = Flask(__name__)

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def catch_all(path):
        return Response(f"CRITICAL FLASK STARTUP ERROR:\n\n{startup_error}", status=500, mimetype="text/plain")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Trading System running on http://127.0.0.1:{port}")
    app.run(host='127.0.0.1', port=port, debug=True)
