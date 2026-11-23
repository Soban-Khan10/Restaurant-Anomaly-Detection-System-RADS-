"""SQLAlchemy engine + session for rads.db.

Every connection runs: PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
The schema lives in shared/schema.sql (single source of truth); tables are reflected from the DB.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, MetaData, Table, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from shared.config import SCHEMA_FILE, get_settings


def _apply_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def make_engine(db_path: str | Path | None = None) -> Engine:
    """New engine for db_path (default: RADS_DB_PATH). Tests pass a temporary path."""
    path = Path(db_path) if db_path else get_settings().db_path
    engine = create_engine(
        URL.create("sqlite", database=str(path)),
        connect_args={"check_same_thread": False},  # worker and app use threads
    )
    event.listen(engine, "connect", _apply_pragmas)
    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine()


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def SessionLocal() -> Session:
    return _session_factory()()


@contextmanager
def session_scope() -> Iterator[Session]:
    """`with session_scope() as s:` commits on success, rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@lru_cache(maxsize=1)
def get_metadata() -> MetaData:
    """All RADS tables reflected from the database (run scripts/init_db.py first)."""
    md = MetaData()
    md.reflect(bind=get_engine())
    return md


def table(name: str) -> Table:
    return get_metadata().tables[name]


def create_schema(db_path: str | Path) -> None:
    """Execute shared/schema.sql on a new database file and switch it to WAL."""
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(sql)
        con.execute("PRAGMA journal_mode=WAL")
        con.commit()
    finally:
        con.close()
