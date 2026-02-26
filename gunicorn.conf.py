# Gunicorn config for Render (FastAPI + UvicornWorker)
# preload_app=False avoids pre-fork DB connections/migrations that can cause hangs.
import os

bind = f"0.0.0.0:{os.getenv('PORT','10000')}"
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
threads = 1
preload_app = False
keepalive = 5
timeout = 90
loglevel = os.getenv("LOG_LEVEL", "info")
accesslog = "-"
errorlog = "-"
