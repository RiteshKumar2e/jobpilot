"""All REST endpoints. Grouped by concern but mounted under one router for
simplicity. Every mutating route depends on require_api_key.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_session
from ..models import (ApplicationAttempt, ApplicationStatus, Event, Interview,
                      Job, PipelineStage, ResumeVersion)
from ..orchestrator import compute_stats
from ..profile_loader import load_profile
from ..schemas import JobOut, StatsOut
from .deps import (get_orchestrator, get_scheduler, get_sheets, require_api_key)

router = APIRouter()


# ---------- system / health ------------------------------------------------ #
@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.get("/api/system")
def system(db: Session = Depends(get_session)):
    s = get_settings()
    sched = get_scheduler()
    sheets = get_sheets()
    orch = get_orchestrator()
    from ..models import KeyValue
    paused = db.get(KeyValue, "paused")
    return {
        "approval_mode": s.approval_mode.value,
        "llm_provider": s.llm_provider.value,
        "daily_application_limit": s.daily_application_limit,
        "daily_autosubmit_limit": s.daily_autosubmit_limit,
        "discovery_cron": s.discovery_cron,
        "next_run": sched.next_run() if sched else None,
        "sheets_enabled": bool(sheets and sheets.enabled),
        "sheet_id": (sheets.sheet_id if sheets else ""),
        "sheet_url": (sheets.sheet_url if sheets else ""),
        "paused": bool(paused and paused.value.get("paused")),
        "enabled_agents": sorted(orch.enabled) if orch else [],
        "resume_variants": orch.resume.variants() if orch else [],
    }


@router.get("/api/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_session)):
    return StatsOut(**compute_stats(db))


# ---------- jobs ----------------------------------------------------------- #
@router.get("/api/jobs", response_model=List[JobOut])
def list_jobs(
    db: Session = Depends(get_session),
    status: Optional[str] = None,
    eligibility: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
    min_score: Optional[float] = None,
    sort: str = Query("score", pattern="^(score|date|company)$"),
    limit: int = 200,
):
    query = select(Job)
    if status:
        query = query.where(Job.application_status == status)
    if eligibility:
        query = query.where(Job.eligibility_status == eligibility)
    if source:
        query = query.where(Job.source == source)
    if min_score is not None:
        query = query.where(Job.match_score >= min_score)
    if q:
        like = f"%{q.lower()}%"
        from sqlalchemy import func, or_
        query = query.where(or_(func.lower(Job.title).like(like),
                                func.lower(Job.company).like(like)))
    if sort == "score":
        query = query.order_by(desc(Job.match_score))
    elif sort == "date":
        query = query.order_by(desc(Job.discovered_at))
    else:
        query = query.order_by(Job.company)
    return db.scalars(query.limit(limit)).all()


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_session)):
    job = db.scalar(select(Job).where(Job.job_id == job_id))
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/api/jobs/{job_id}/materials")
def job_materials(job_id: str, db: Session = Depends(get_session)):
    job = db.scalar(select(Job).where(Job.job_id == job_id))
    if not job:
        raise HTTPException(404, "Job not found")
    cover = ""
    if job.cover_letter_path and Path(job.cover_letter_path).exists():
        cover = Path(job.cover_letter_path).read_text(encoding="utf-8", errors="ignore")
    versions = db.scalars(select(ResumeVersion).where(
        ResumeVersion.job_id == job_id).order_by(desc(ResumeVersion.created_at))).all()
    return {
        "job_id": job_id,
        "cover_letter": cover,
        "tailored_resume_path": job.tailored_resume_path,
        "resume_versions": [{"label": v.label, "pdf_path": v.pdf_path,
                             "flags": v.flags, "created_at": str(v.created_at)}
                            for v in versions],
    }


@router.get("/api/jobs/{job_id}/resume")
def download_resume(job_id: str, db: Session = Depends(get_session)):
    job = db.scalar(select(Job).where(Job.job_id == job_id))
    if not job or not job.tailored_resume_path:
        raise HTTPException(404, "No tailored resume")
    p = Path(job.tailored_resume_path)
    if not p.exists():
        raise HTTPException(404, "Resume file missing on disk")
    media = "application/pdf" if p.suffix == ".pdf" else "text/plain"
    return FileResponse(str(p), media_type=media, filename=p.name)


# ---------- approval / submission ------------------------------------------ #
@router.post("/api/jobs/{job_id}/approve", dependencies=[Depends(require_api_key)])
def approve_job(job_id: str, db: Session = Depends(get_session)):
    """Mode A approval: user explicitly approves, then the browser agent runs a
    submission attempt (on the user's machine with a real browser)."""
    job = db.scalar(select(Job).where(Job.job_id == job_id))
    if not job:
        raise HTTPException(404, "Job not found")
    if job.eligibility_status == ApplicationStatus.SUBMITTED_CONFIRMED.value:
        return {"status": "already_confirmed"}
    orch = get_orchestrator()
    job.application_status = ApplicationStatus.APPROVED.value
    db.commit()
    outcome = orch.submit_job(db, job, approved=True)
    return {"status": outcome.status, "error": outcome.error,
            "screenshot": outcome.screenshot, "detail": outcome.detail}


@router.post("/api/jobs/{job_id}/reject", dependencies=[Depends(require_api_key)])
def reject_job(job_id: str, db: Session = Depends(get_session)):
    job = db.scalar(select(Job).where(Job.job_id == job_id))
    if not job:
        raise HTTPException(404, "Job not found")
    job.application_status = ApplicationStatus.REJECTED_BY_USER.value
    job.pipeline_stage = PipelineStage.DONE.value
    job.next_action = "Rejected by user."
    db.commit()
    return {"status": "rejected"}


# ---------- agents / cycle control ----------------------------------------- #
@router.post("/api/cycle/run", dependencies=[Depends(require_api_key)])
def run_cycle():
    """Starts a cycle in the background and returns at once. A full cycle (fetch
    + verify + browser applications) takes minutes; holding the request open
    that long is what made the dev proxy answer 500."""
    orch = get_orchestrator()
    if not orch:
        raise HTTPException(503, "Orchestrator not ready")
    return _start_background(orch.run_full_cycle, "cycle")


@router.post("/api/cycle/resume", dependencies=[Depends(require_api_key)])
def resume_pending():
    return _start_background(get_orchestrator().process_pending, "resume")


def _start_background(fn, name: str) -> dict:
    if get_orchestrator().is_running():
        return {"started": False, "busy": True,
                "message": "A pipeline run is already in progress."}
    threading.Thread(target=fn, name=f"jobpilot-{name}", daemon=True).start()
    return {"started": True, "message": f"{name.capitalize()} started in the background."}


@router.post("/api/automation/pause", dependencies=[Depends(require_api_key)])
def pause():
    get_orchestrator().set_paused(True)
    return {"paused": True}


@router.get("/api/auth/check", dependencies=[Depends(require_api_key)])
def auth_check():
    """Lets the dashboard confirm a saved API key before you need it."""
    return {"ok": True}


@router.post("/api/automation/resume", dependencies=[Depends(require_api_key)])
def resume_automation():
    get_orchestrator().set_paused(False)
    return {"paused": False}


@router.post("/api/agents/{agent}/toggle", dependencies=[Depends(require_api_key)])
def toggle_agent(agent: str, enable: bool = True):
    orch = get_orchestrator()
    if enable:
        orch.enabled.add(agent)
    else:
        orch.enabled.discard(agent)
    return {"enabled_agents": sorted(orch.enabled)}


# ---------- events / logs -------------------------------------------------- #
@router.get("/api/events")
def events(db: Session = Depends(get_session), limit: int = 100,
           agent: Optional[str] = None, level: Optional[str] = None):
    q = select(Event).order_by(desc(Event.ts))
    if agent:
        q = q.where(Event.agent == agent)
    if level:
        q = q.where(Event.level == level)
    rows = db.scalars(q.limit(limit)).all()
    return [{"ts": str(e.ts), "level": e.level, "agent": e.agent,
             "job_id": e.job_id, "message": e.message, "data": e.data} for e in rows]


@router.get("/api/history")
def history(db: Session = Depends(get_session), limit: int = 100):
    rows = db.scalars(select(ApplicationAttempt)
                      .order_by(desc(ApplicationAttempt.started_at)).limit(limit)).all()
    return [{"job_id": a.job_id, "attempt": a.attempt_no, "result": a.result,
             "error": a.error, "started_at": str(a.started_at),
             "finished_at": str(a.finished_at) if a.finished_at else None}
            for a in rows]


# ---------- config --------------------------------------------------------- #
@router.get("/api/config")
def get_config():
    s = get_settings()
    p = load_profile()
    return {
        "approval_mode": s.approval_mode.value,
        "daily_application_limit": s.daily_application_limit,
        "target_roles": p.target_roles,
        "locations": p.locations,
        "compensation": p.compensation,
        "sources": p.sources,
        "verified_items": p.verified_items(),
    }


@router.post("/api/config/reload", dependencies=[Depends(require_api_key)])
def reload_profile():
    profile = load_profile(force=True)
    orch = get_orchestrator()
    if orch:
        orch.set_profile(profile)
    return {"status": "reloaded"}


@router.post("/api/schedule", dependencies=[Depends(require_api_key)])
def set_schedule(cron: str):
    sched = get_scheduler()
    if not sched:
        raise HTTPException(503, "Scheduler not running")
    sched.reschedule(cron)
    return {"cron": cron, "next_run": sched.next_run()}


# ---------- resume upload -------------------------------------------------- #
@router.post("/api/resumes/upload", dependencies=[Depends(require_api_key)])
async def upload_resume(file: UploadFile = File(...)):
    s = get_settings()
    allowed = {".pdf", ".docx", ".txt", ".md", ".tex"}
    name = Path(file.filename).name
    ext = Path(name).suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported type {ext}. Allowed: {sorted(allowed)}")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "File too large (>5MB)")
    dest = Path(s.resumes_dir) / name
    dest.write_bytes(content)
    orch = get_orchestrator()
    if orch:
        orch.resume.index(force=True)
    return {"saved": name, "variants": orch.resume.variants() if orch else []}
