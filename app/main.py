"""RADS app server.  Run:  uvicorn app.main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import sessionmaker

from app import alerts, ws
from app.routes import auth, evidence, incidents, internal, users
from shared.config import get_settings
from shared.db import get_engine

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
    if not logging.getLogger().handlers:  # show rads.* INFO logs next to uvicorn's
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")

    channels = [x.channel.value for x in alerts.configured_senders()]
    log.info("alert channels: dashboard%s", "".join(f", {c}" for c in channels))

    # Escalation job every minute (APScheduler thread) + offline-camera watcher (asyncio task).
    scheduler = BackgroundScheduler(timezone="Asia/Karachi")
    scheduler.add_job(alerts.run_escalation, "interval", minutes=1, args=[sessionmaker(bind=get_engine())],
                      id="escalation", max_instances=1, coalesce=True)
    scheduler.start()
    watcher = asyncio.create_task(ws.offline_watch())
    try:
        yield
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        scheduler.shutdown(wait=False)


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
    app.include_router(ws.router)
    return app


app = create_app()
