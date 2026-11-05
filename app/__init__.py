import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from config import Config

db = SQLAlchemy()

def create_app(config_class=Config):
    # Use explicit absolute paths for templates and static files to ensure reliability across all environments
    base_app_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = os.path.join(base_app_dir, 'templates')
    static_dir = os.path.join(base_app_dir, 'static')
    
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.config.from_object(config_class)

    db.init_app(app)

    with app.app_context():
        try:
            from app.models import trading
            db.create_all()

            # Safe SQLite migration helper for new position tracking columns
            from sqlalchemy import text
            try:
                with db.engine.connect() as conn:
                    for col_def in [
                        "peak_price FLOAT DEFAULT 0.0",
                        "stop_loss FLOAT DEFAULT 0.0",
                        "take_profit FLOAT DEFAULT 0.0",
                        "trailing_active BOOLEAN DEFAULT 0"
                    ]:
                        try:
                            conn.execute(text(f"ALTER TABLE positions ADD COLUMN {col_def}"))
                            conn.commit()
                        except Exception:
                            pass
            except Exception:
                pass

            trading.init_default_data(app.config.get('INITIAL_CASH', 1000000.0))
        except Exception as e:
            app.logger.warning(f"Database init warning (non-fatal): {e}")

    from app.routes.views import views_bp
    from app.routes.api import api_bp

    app.register_blueprint(views_bp)
    app.register_blueprint(api_bp, url_prefix='/api')

    return app
