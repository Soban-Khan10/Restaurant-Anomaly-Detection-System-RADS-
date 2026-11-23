"""RADS app server.  Run:  uvicorn app.main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import auth, evidence, incidents, internal, users
from shared.config import get_settings

log = logging.getLogger("rads.app")

# Vite dev server
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    if not s.jwt_secret:
        raise RuntimeError("RADS_JWT_SECRET is not set in .env")
    if s.jwt_secret.startswith("change-me"):
        log.warning("RADS_JWT_SECRET still has the .env.example value; set a long random string")
    if not s.db_path.exists():
        raise RuntimeError(f"No database at {s.db_path}; run scripts/init_db.py and scripts/seed.py")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="RADS app server", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(incidents.router)
    app.include_router(evidence.router)
    app.include_router(internal.router)
    return app


app = create_app()
