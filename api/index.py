import sys
import os

# Ensure project root is in python search path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from run import app

# Export both 'app' and 'handler' to satisfy any Vercel serverless loader
handler = app
