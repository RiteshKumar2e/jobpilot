"""FastAPI application entrypoint. Wires config, DB, agents, orchestrator,
scheduler, routes, and CORS.

Run:  uvicorn app.main:app --reload   (from the backend/ directory)
"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import init_db
from .logging_conf import configure_logging, get_logger
from .orchestrator import Orchestrator
from .scheduler import JobScheduler
from .agents.sheets_tracker import SheetsAgent
from .api.deps import set_runtime
from .api.routes import router

settings = get_settings()
configure_logging(settings.log_dir)
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    init_db()
    _close_interrupted_attempts()
    sheets = SheetsAgent()

    # First-time Google sign-in waits for you in the browser; do it in the
    # background so the API (and dashboard) are up meanwhile.
    def _connect_sheets():
        try:
            if sheets.connect():  # no-op if creds/libs absent
                sheets.ensure_spreadsheet()  # create it now so its link shows at once
                log.info(f"Google Sheet: {sheets.sheet_url}")
        except Exception as e:
            log.warning(f"Sheets connect skipped: {e}")
    threading.Thread(target=_connect_sheets, name="sheets-connect", daemon=True).start()
    orch = Orchestrator(sheets=sheets)
    scheduler = JobScheduler(orch)
    scheduler.start()
    set_runtime(orch, scheduler, sheets)
    log.info("JobPilot backend started",
             extra={"approval_mode": settings.approval_mode.value,
                    "llm": settings.llm_provider.value,
                    "sheets": sheets.enabled})
    # Resume anything left mid-pipeline after a previous crash. In a background
    # thread so the API is reachable immediately (thousands of jobs can be pending).
    def _resume():
        try:
            log.info("Startup recovery done", extra={"stats": orch.process_pending()})
        except Exception as e:
            log.warning(f"resume-pending on startup failed: {e}")
    threading.Thread(target=_resume, name="startup-recovery", daemon=True).start()
    yield
    # shutdown
    scheduler.shutdown()
    log.info("JobPilot backend stopped")


def _close_interrupted_attempts() -> None:
    """Attempts still 'running' at startup were cut off by a restart (nothing
    was submitted — submission is the last step and records its result)."""
    from .database import SessionLocal
    from .models import ApplicationAttempt
    db = SessionLocal()
    try:
        for a in db.query(ApplicationAttempt).filter(ApplicationAttempt.result == "running"):
            a.result = "interrupted"
            a.error = "Backend restarted mid-attempt."
        db.commit()
    finally:
        db.close()


app = FastAPI(title="JobPilot AI", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root():
    return {"name": "JobPilot AI", "docs": "/docs", "health": "/api/health"}
