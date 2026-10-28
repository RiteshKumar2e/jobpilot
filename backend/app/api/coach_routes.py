"""Answer Bank, Inbox, outcomes, interview coach, LinkedIn kit, follow-ups,
weekly review, and the application spot-check."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .. import coach
from ..answer_bank import add_answer, coverage, pending
from ..config import get_settings
from ..database import get_session
from ..models import (ApplicationAttempt, InboxMessage, Interview, Job, OUTCOME_RANK, Outcome)
from ..profile_loader import load_profile
from .deps import get_orchestrator, require_api_key

router = APIRouter()


def _job(db, job_id) -> Job:
    job = db.scalar(select(Job).where(Job.job_id == job_id))
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# ---------- Answer Bank ---------------------------------------------------- #
@router.get("/api/answers")
def answers(db: Session = Depends(get_session)):
    profile = load_profile(force=True)
    return {"coverage": coverage(profile), "pending": pending(db, profile),
            "answers": profile.raw().get("application_answers") or []}


class AnswerIn(BaseModel):
    question: str
    answer: str
    pattern: str = ""


@router.post("/api/answers", dependencies=[Depends(require_api_key)])
def save_answer(body: AnswerIn):
    """Adds the answer to candidate_profile.yaml. Jobs that stopped on this
    question retry on the next run (or right away with Resume pending)."""
    try:
        rule = add_answer(body.question, body.answer, body.pattern or None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    orch = get_orchestrator()
    if orch:
        orch.set_profile(load_profile(force=True))
    return {"saved": rule}


# ---------- outcomes ------------------------------------------------------- #
class OutcomeIn(BaseModel):
    outcome: str = ""            # Outcome value, or "" to clear
    recruiter: str = ""
    interview_at: str = ""       # ISO date-time, optional
    note: str = ""


@router.post("/api/jobs/{job_id}/outcome", dependencies=[Depends(require_api_key)])
def set_outcome(job_id: str, body: OutcomeIn, db: Session = Depends(get_session)):
    job = _job(db, job_id)
    if body.outcome and body.outcome not in {o.value for o in Outcome}:
        raise HTTPException(422, f"outcome must be one of {[o.value for o in Outcome]}")
    job.outcome = body.outcome
    job.outcome_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if body.recruiter:
        job.recruiter = body.recruiter[:200]
    if body.interview_at or body.outcome == Outcome.INTERVIEW_BOOKED.value:
        db.add(Interview(job_id=job_id, recruiter_contact=job.recruiter, stage=body.outcome,
                         scheduled_at=body.interview_at, next_action=body.note[:300]))
    db.commit()
    return {"job_id": job_id, "outcome": job.outcome}


# ---------- Inbox ---------------------------------------------------------- #
@router.get("/api/inbox")
def inbox(db: Session = Depends(get_session), category: Optional[str] = None,
          include_handled: bool = False, limit: int = 100):
    q = select(InboxMessage).order_by(desc(InboxMessage.received_at))
    if category:
        q = q.where(InboxMessage.category == category)
    if not include_handled:
        q = q.where(InboxMessage.handled.is_(False))
    rows = db.scalars(q.limit(limit)).all()
    jobs = {j.job_id: j for j in db.scalars(select(Job).where(
        Job.job_id.in_([r.job_id for r in rows if r.job_id])))}
    orch = get_orchestrator()
    return {
        "enabled": bool(orch and orch.inbox.enabled),
        "messages": [{
            "id": m.id, "received_at": str(m.received_at or ""), "sender": m.sender,
            "subject": m.subject, "snippet": m.snippet, "category": m.category,
            "job_id": m.job_id, "job": ({"title": jobs[m.job_id].title, "company": jobs[m.job_id].company,
                                         "outcome": jobs[m.job_id].outcome}
                                        if m.job_id in jobs else None),
            "links": m.links, "draft_reply": m.draft_reply, "calendar_url": m.calendar_url,
            "handled": m.handled,
            "gmail_url": "https://mail.google.com/mail/u/0/#search/" +
                         quote(f'subject:"{m.subject[:80]}"'),
        } for m in rows]}


@router.post("/api/inbox/check", dependencies=[Depends(require_api_key)])
def inbox_check():
    orch = get_orchestrator()
    if not orch:
        raise HTTPException(503, "Orchestrator not ready")
    if not orch.inbox.enabled:
        return {"started": False, "message": "Inbox needs JOBPILOT_SMTP_USER + an App Password "
                                             "in JOBPILOT_SMTP_PASSWORD."}
    threading.Thread(target=orch.check_inbox, name="jobpilot-inbox", daemon=True).start()
    return {"started": True, "message": "Checking your inbox (read-only)…"}


@router.post("/api/inbox/{mid}/handled", dependencies=[Depends(require_api_key)])
def inbox_handled(mid: int, handled: bool = True, db: Session = Depends(get_session)):
    m = db.get(InboxMessage, mid)
    if not m:
        raise HTTPException(404, "Message not found")
    m.handled = handled
    db.commit()
    return {"id": mid, "handled": handled}


# ---------- interview coach / dream-job kit ------------------------------- #
@router.get("/api/jobs/{job_id}/kit")
def job_kit(job_id: str, db: Session = Depends(get_session)):
    return coach.dream_kit(_job(db, job_id), load_profile())


class PrepIn(BaseModel):
    kind: str = "recruiter screen"


@router.post("/api/jobs/{job_id}/prep")
def job_prep(job_id: str, body: PrepIn, db: Session = Depends(get_session)):
    job = _job(db, job_id)
    return {"brief": coach.prep_brief(job, load_profile(), body.kind),
            "questions": coach.mock_questions(job, body.kind)}


class MockIn(BaseModel):
    question: str
    answer: str


@router.post("/api/jobs/{job_id}/mock")
def job_mock(job_id: str, body: MockIn, db: Session = Depends(get_session)):
    if not body.answer.strip():
        raise HTTPException(422, "Type (or dictate) your answer first.")
    return coach.score_answer(_job(db, job_id), load_profile(), body.question, body.answer)


class DebriefIn(BaseModel):
    notes: str
    interviewer: str = ""


@router.post("/api/jobs/{job_id}/debrief", dependencies=[Depends(require_api_key)])
def job_debrief(job_id: str, body: DebriefIn, db: Session = Depends(get_session)):
    job = _job(db, job_id)
    if OUTCOME_RANK.get(job.outcome or "", 0) < OUTCOME_RANK[Outcome.INTERVIEW_DONE.value]:
        job.outcome = Outcome.INTERVIEW_DONE.value
        job.outcome_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if body.interviewer and not job.recruiter:
        job.recruiter = body.interviewer[:200]
    db.commit()
    return coach.debrief(job, load_profile(), body.notes, body.interviewer)


# ---------- spot-check what was sent -------------------------------------- #
@router.get("/api/jobs/{job_id}/attempts")
def job_attempts(job_id: str, db: Session = Depends(get_session)):
    rows = db.scalars(select(ApplicationAttempt).where(ApplicationAttempt.job_id == job_id)
                      .order_by(desc(ApplicationAttempt.started_at))).all()
    return [{"id": a.id, "attempt": a.attempt_no, "result": a.result, "error": a.error,
             "started_at": str(a.started_at), "filled": (a.detail or {}).get("filled", []),
             "missing": (a.detail or {}).get("missing", []),
             "url": (a.detail or {}).get("url", ""),
             "has_screenshot": bool((a.detail or {}).get("screenshot"))} for a in rows]


@router.get("/api/attempts/{aid}/screenshot")
def attempt_screenshot(aid: int, db: Session = Depends(get_session)):
    a = db.get(ApplicationAttempt, aid)
    path = (a.detail or {}).get("screenshot") if a else ""
    if not path:
        raise HTTPException(404, "No screenshot for this attempt")
    p = Path(path).resolve()
    shots = (Path(get_settings().log_dir) / "screenshots").resolve()
    if shots not in p.parents or not p.exists():      # only serve our own screenshots
        raise HTTPException(404, "Screenshot not available")
    return FileResponse(str(p), media_type="image/png")


# ---------- LinkedIn kit / follow-ups / weekly review --------------------- #
@router.get("/api/coach/linkedin")
def linkedin(db: Session = Depends(get_session)):
    return coach.linkedin_kit(db, load_profile())


@router.get("/api/coach/followups")
def followup_list(db: Session = Depends(get_session), days: int = 7, top: int = 5):
    return coach.followups(db, load_profile(), days, top)


@router.get("/api/coach/weekly")
def weekly(db: Session = Depends(get_session), days: int = 7):
    r = coach.weekly_review(db, days)
    return {**r, "text": coach.weekly_review_text(r)}


@router.post("/api/coach/weekly/email", dependencies=[Depends(require_api_key)])
def weekly_email():
    orch = get_orchestrator()
    if not orch:
        raise HTTPException(503, "Orchestrator not ready")
    orch.send_weekly_review()
    return {"sent": orch.notifier.enabled}
