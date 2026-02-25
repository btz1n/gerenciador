
from __future__ import annotations

import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from .db import engine
from .migrations import ensure_schema
from .routes import router

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
TEMPLATES_DIR = os.path.join(PROJECT_DIR, "templates")
STATIC_DIR = os.path.join(PROJECT_DIR, "static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

def create_app() -> FastAPI:
    app = FastAPI(title="Império SaaS")

    # Static
    if not os.path.isdir(STATIC_DIR):
        os.makedirs(STATIC_DIR, exist_ok=True)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Ensure schema on startup
    ensure_schema(engine)

    # Routes
    app.include_router(router)

    # Exception handler for auth/subscription
    @app.exception_handler(HTTPException)
    def http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == 401:
            return RedirectResponse(url="/login", status_code=302)
        if exc.status_code == 402:
            # payment required
            return RedirectResponse(url="/billing", status_code=302)
        return templates.TemplateResponse("base.html", {"request": request, "user": None, "error": exc.detail, "title": "Erro"}, status_code=exc.status_code)

    return app

app = create_app()
