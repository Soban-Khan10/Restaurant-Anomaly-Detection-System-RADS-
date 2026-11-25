"""RADS app server.  Run:  uvicorn app.main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import sessionmaker

from app import alerts, log_redaction, retention, ws
from app.routes import (admin, auth, cameras, evidence, incidents, internal, lookups, reports, rules, users,
                        zones)
from shared.config import Settings, get_settings
from shared.contract import TZ
from shared.db import get_engine

log = logging.getLogger("rads.app")
log_redaction.install()  # uvicorn has set up its loggers before it imports the app: no tokens in any log line

# Vite dev server (dashboard/, `npm run dev` on port 5173)
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
# Response headers the dashboard's JavaScript may read (browsers hide non-standard headers otherwise).
EXPOSED_HEADERS = ["X-RADS-Worker-Reload", "Content-Disposition"]


def add_jobs(scheduler: BackgroundScheduler, Session_: sessionmaker, s: Settings) -> None:
    """Every minute, and once right at startup: catch up missed first alerts, then escalation.
    Daily at 03:00 Pakistan time: evidence retention + database backup."""
    scheduler.add_job(alerts.run_minute_jobs, "interval", minutes=1, args=[Session_],
                      id="escalation", max_instances=1, coalesce=True, next_run_time=datetime.now(TZ))
    scheduler.add_job(retention.run_daily_maintenance, "cron", hour=3, minute=0,
                      args=[Session_, s.evidence_dir, s.db_path, s.data_dir / "backups"],
                      id="maintenance", max_instances=1, coalesce=True,
                      misfire_grace_time=3600)  # still runs if the PC was busy or asleep at 03:00, up to 04:00


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
    log_redaction.install()  # again, for the handler basicConfig just added
    # APScheduler logs two INFO lines per job run (every minute); keep only its warnings and errors.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    channels = [x.channel.value for x in alerts.configured_senders()]
    log.info("alert channels: dashboard%s", "".join(f", {c}" for c in channels))

    # Scheduled jobs (APScheduler thread) + offline-camera watcher (asyncio task).
    scheduler = BackgroundScheduler(timezone="Asia/Karachi")
    add_jobs(scheduler, sessionmaker(bind=get_engine()), s)
    scheduler.start()
    log.info("daily maintenance (evidence > %d days, DB backup) next runs at %s", retention.RETENTION_DAYS,
             scheduler.get_job("maintenance").next_run_time)
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
        expose_headers=EXPOSED_HEADERS,
    )
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(incidents.router)
    app.include_router(lookups.router)
    app.include_router(evidence.router)
    app.include_router(cameras.router)
    app.include_router(zones.router)
    app.include_router(rules.router)
    app.include_router(reports.router)
    app.include_router(admin.router)
    app.include_router(internal.router)
    app.include_router(ws.router)
    return app


app = create_app()
