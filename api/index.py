import sys
import os

# Ensure project root is in python search path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from run import app

class VercelPathFix:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        # Vercel rewrites forward the real path in HTTP headers
        real_uri = environ.get('HTTP_X_FORWARDED_URI') or environ.get('HTTP_X_MATCHED_PATH')
        if real_uri:
            environ['PATH_INFO'] = real_uri.split('?')[0]
        return self.wsgi_app(environ, start_response)

# Apply middleware to restore original request paths from Vercel rewrites
app.wsgi_app = VercelPathFix(app.wsgi_app)
handler = app
