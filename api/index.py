import os
import sys

# Ensure project root is in Python sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Set VERCEL environment marker
os.environ['VERCEL'] = '1'

# Import Flask WSGI application
from app import app
