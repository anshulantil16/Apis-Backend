"""
Windows production server using Waitress (gunicorn does not run on Windows).
Run with: python serve_windows.py
"""
import os
from dotenv import load_dotenv

load_dotenv()

from waitress import serve
from config.wsgi import application

HOST = os.getenv('SERVER_HOST', '0.0.0.0')
PORT = int(os.getenv('SERVER_PORT', '8000'))
THREADS = int(os.getenv('SERVER_THREADS', '8'))

if __name__ == '__main__':
    print(f"Starting APIS India backend on http://{HOST}:{PORT} ({THREADS} threads)")
    serve(application, host=HOST, port=PORT, threads=THREADS)
