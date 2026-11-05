import os
from flask import Blueprint, render_template, jsonify

views_bp = Blueprint('views', __name__)

@views_bp.route('/')
@views_bp.route('/api/index')
def dashboard():
    return render_template('dashboard.html')

@views_bp.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "environment": "serverless" if any(k in os.environ for k in ('VERCEL', 'VERCEL_ENV', 'AWS_LAMBDA_FUNCTION_NAME')) else "local"
    })


