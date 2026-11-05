import sys
import os

# Ensure project root is in python search path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from run import app

handler = app
