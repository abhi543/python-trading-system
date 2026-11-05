import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def get_database_uri():
    if os.environ.get('DATABASE_URL'):
        return os.environ.get('DATABASE_URL')
    
    # Check if running in Vercel, AWS Lambda, or serverless environment
    is_serverless = any(
        k in os.environ for k in ('VERCEL', 'VERCEL_ENV', 'VERCEL_REGION', 'AWS_LAMBDA_FUNCTION_NAME', 'LAMBDA_TASK_ROOT')
    )
    if is_serverless:
        return "sqlite:////tmp/trading.db"
    
    # Fallback check: test if BASE_DIR is writable
    try:
        test_path = os.path.join(BASE_DIR, '.perm_test')
        with open(test_path, 'w') as f:
            f.write('1')
        os.remove(test_path)
        return f"sqlite:///{os.path.join(BASE_DIR, 'trading.db')}"
    except Exception:
        return "sqlite:////tmp/trading.db"

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'trading-system-secret-key-change-me')
    SQLALCHEMY_DATABASE_URI = get_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    INITIAL_CASH = float(os.environ.get('INITIAL_CASH', 100000.0))
