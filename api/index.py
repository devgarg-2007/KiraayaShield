import os
import sys

# Ensure repository root is in sys.path for Vercel Serverless environment
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend_python.main import app

# Vercel looks for the ASGI/WSGI app variable
__all__ = ["app"]
