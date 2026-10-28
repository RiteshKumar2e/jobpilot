"""SQLAlchemy engine/session. SQLite now, clean path to Postgres (just change DB_URL)."""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

# check_same_thread only matters for SQLite + threaded server/scheduler.
_is_sqlite = _settings.db_url.startswith("sqlite")
_connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}

engine = create_engine(
    _settings.db_url,
    echo=False,
    future=True,
    connect_args=_connect_args,
    pool_pre_ping=True,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # WAL: dashboard reads never wait on (or get "database is locked" from)
        # the pipeline's writes; busy_timeout covers writer-vs-writer.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_session():
    """FastAPI dependency: yields a session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables if absent. Import models first so they register on Base."""
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate()


# Columns added after the first release: (table, column, DDL type + default).
# create_all() only creates missing TABLES, so existing databases get these via
# ALTER TABLE ADD COLUMN — additive and safe to run on every start.
_ADDED_COLUMNS = [
    ("jobs", "board", "VARCHAR(200) DEFAULT ''"),
    ("jobs", "dedup_key", "VARCHAR(300) DEFAULT ''"),
    ("jobs", "last_seen_at", "DATETIME"),
    ("jobs", "outcome", "VARCHAR(30) DEFAULT ''"),
    ("jobs", "recruiter", "VARCHAR(200) DEFAULT ''"),
    ("jobs", "outcome_at", "DATETIME"),
]


def add_missing_columns(eng) -> list:
    """ALTER TABLE ADD COLUMN for every _ADDED_COLUMNS entry the table lacks.
    Returns what was added."""
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    added = []
    with eng.begin() as conn:
        for table, col, ddl in _ADDED_COLUMNS:
            if not insp.has_table(table):
                continue
            if col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                if col in ("board", "dedup_key"):
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{col} ON {table} ({col})"))
                added.append(f"{table}.{col}")
    return added


def _migrate() -> None:
    add_missing_columns(engine)
    _backfill()


def _backfill() -> None:
    """Fill the v0.2 columns for jobs stored before them (one-time; cheap after)."""
    from sqlalchemy import or_, select
    from .agents.base import dedup_key
    from .models import Job, JobSource
    db = SessionLocal()
    try:
        rows = db.scalars(select(Job).where(or_(Job.dedup_key == "", Job.dedup_key.is_(None)))).all()
        for job in rows:
            job.dedup_key = dedup_key(job.company, job.title, job.location)
            if not job.board and job.source in ("greenhouse", "lever"):
                job.board = job.company  # their company field is the board token
        if rows:
            db.commit()
        # every job gets its own source row (the alias lookup for cross-source dedup)
        have = set(db.scalars(select(JobSource.source_job_id)).all())
        missing = [j for j in db.scalars(select(Job)).all() if j.job_id not in have]
        for job in missing:
            db.add(JobSource(job_id=job.job_id, source_job_id=job.job_id, source=job.source,
                             url=job.job_url, first_seen=job.discovered_at,
                             last_seen=job.discovered_at))
        if missing:
            db.commit()
    finally:
        db.close()
