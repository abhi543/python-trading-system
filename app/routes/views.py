import os
from flask import Blueprint, render_template, jsonify

views_bp = Blueprint('views', __name__)

@views_bp.route('/')
@views_bp.route('/api/index')
def dashboard():
    return render_template('dashboard.html')

@views_bp.route('/bot')
def bot_page():
    return render_template('bot.html')

@views_bp.route('/crypto')
def crypto_page():
    return render_template('crypto.html')

@views_bp.route('/debug-env')
def debug_env():
    from flask import request
    return jsonify({k: str(v) for k, v in request.environ.items() if 'VERCEL' in k or 'HTTP_' in k or 'PATH' in k or 'REQUEST_URI' in k})

@views_bp.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "environment": "serverless" if any(k in os.environ for k in ('VERCEL', 'VERCEL_ENV', 'AWS_LAMBDA_FUNCTION_NAME')) else "local"
    })


