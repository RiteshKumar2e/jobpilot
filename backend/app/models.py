"""ORM models + the canonical status enums used across all agents.

Design notes:
- `Job.job_id` is a STABLE hash of (source, source_id|canonical_url). This is the
  idempotency key that prevents duplicate rows in both the DB and Google Sheets.
- Status enums are strings (portable to Postgres, readable in the Sheet).
- `AgentRun` + `Event` give a full audit trail (safety requirement).
- Per-job checkpoints live in `Job.pipeline_stage` so the orchestrator can
  resume after a crash without re-doing or duplicating completed work.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Enums (kept as plain string constants for Sheet/JSON friendliness)
# --------------------------------------------------------------------------- #
class EligibilityStatus(str, enum.Enum):
    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    UNCHECKED = "UNCHECKED"


class ApplicationStatus(str, enum.Enum):
    NONE = "NONE"
    DRAFTED = "DRAFTED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"               # human said go (Mode A) / policy allows (Mode B)
    SUBMITTED_CONFIRMED = "SUBMITTED_CONFIRMED"
    SUBMISSION_UNVERIFIED = "SUBMISSION_UNVERIFIED"
    FAILED = "FAILED"
    ACTION_REQUIRED = "ACTION_REQUIRED"  # CAPTCHA/OTP/login — needs the human
    REJECTED_BY_USER = "REJECTED_BY_USER"


class Outcome(str, enum.Enum):
    """After the application: recruiter reply → interview → offer (or a no)."""
    RECRUITER_REPLY = "RECRUITER_REPLY"
    ASSESSMENT = "ASSESSMENT"
    INTERVIEW_INVITE = "INTERVIEW_INVITE"
    INTERVIEW_BOOKED = "INTERVIEW_BOOKED"
    INTERVIEW_DONE = "INTERVIEW_DONE"
    OFFER = "OFFER"
    REJECTED_BY_COMPANY = "REJECTED_BY_COMPANY"


# Later outcomes win; an automatic update never moves a job backwards.
OUTCOME_RANK = {"": 0, "RECRUITER_REPLY": 1, "ASSESSMENT": 2, "INTERVIEW_INVITE": 3,
                "INTERVIEW_BOOKED": 4, "INTERVIEW_DONE": 5, "REJECTED_BY_COMPANY": 6, "OFFER": 7}


# Strength ranking so we NEVER overwrite a strong status with a weaker/older one.
APPLICATION_STATUS_RANK = {
    ApplicationStatus.NONE: 0,
    ApplicationStatus.DRAFTED: 1,
    ApplicationStatus.READY_FOR_REVIEW: 2,
    ApplicationStatus.ACTION_REQUIRED: 3,
    ApplicationStatus.APPROVED: 4,
    ApplicationStatus.FAILED: 5,
    ApplicationStatus.SUBMISSION_UNVERIFIED: 6,
    ApplicationStatus.REJECTED_BY_USER: 7,
    ApplicationStatus.SUBMITTED_CONFIRMED: 8,   # strongest: never downgraded by a sync
}


class PipelineStage(str, enum.Enum):
    DISCOVERED = "DISCOVERED"
    VERIFIED = "VERIFIED"
    SCORED = "SCORED"
    RESUME_SELECTED = "RESUME_SELECTED"
    MATERIALS_READY = "MATERIALS_READY"
    APPLICATION_PREPARED = "APPLICATION_PREPARED"
    DONE = "DONE"


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)  # stable hash

    # discovery
    source: Mapped[str] = mapped_column(String(40), index=True)   # greenhouse|lever|...
    source_id: Mapped[str] = mapped_column(String(120), default="")
    company: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    work_mode: Mapped[str] = mapped_column(String(40), default="")   # remote|hybrid|onsite
    employment_type: Mapped[str] = mapped_column(String(40), default="")
    experience_req: Mapped[str] = mapped_column(String(120), default="")
    salary_range: Mapped[str] = mapped_column(String(120), default="")
    posted_at: Mapped[str] = mapped_column(String(40), default="")
    job_url: Mapped[str] = mapped_column(String(600), default="")
    application_url: Mapped[str] = mapped_column(String(600), default="")
    canonical_url: Mapped[str] = mapped_column(String(600), default="", index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # multi-source discovery (added in v0.2; see database._migrate)
    board: Mapped[str] = mapped_column(String(200), default="", index=True)   # ATS board / feed target
    dedup_key: Mapped[str] = mapped_column(String(300), default="", index=True)  # company|title|place
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # what happened after applying (inbox agent / you): see Outcome
    outcome: Mapped[str] = mapped_column(String(30), default="", index=True)
    recruiter: Mapped[str] = mapped_column(String(200), default="")
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # verification
    eligibility_status: Mapped[str] = mapped_column(
        String(20), default=EligibilityStatus.UNCHECKED.value, index=True
    )
    eligibility_reasons: Mapped[list] = mapped_column(JSON, default=list)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # scoring
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    matching_skills: Mapped[list] = mapped_column(JSON, default=list)
    missing_skills: Mapped[list] = mapped_column(JSON, default=list)
    score_explanation: Mapped[str] = mapped_column(Text, default="")

    # resume / materials
    recommended_resume_version: Mapped[str] = mapped_column(String(80), default="")
    tailored_resume_path: Mapped[str] = mapped_column(String(600), default="")
    cover_letter_path: Mapped[str] = mapped_column(String(600), default="")

    # application
    application_status: Mapped[str] = mapped_column(
        String(30), default=ApplicationStatus.NONE.value, index=True
    )
    submission_date: Mapped[str] = mapped_column(String(40), default="")
    confirmation_reference: Mapped[str] = mapped_column(String(200), default="")
    follow_up_date: Mapped[str] = mapped_column(String(40), default="")
    next_action: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    # orchestration / checkpointing
    pipeline_stage: Mapped[str] = mapped_column(
        String(30), default=PipelineStage.DISCOVERED.value, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    applications: Mapped[list["ApplicationAttempt"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class ApplicationAttempt(Base):
    """One row per submission attempt (Sheet tab B: Application History)."""
    __tablename__ = "application_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), ForeignKey("jobs.job_id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[str] = mapped_column(String(30), default="")      # ApplicationStatus value
    error: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

    job: Mapped["Job"] = relationship(back_populates="applications")


class Interview(Base):
    """Sheet tab C: Interview Tracker."""
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), ForeignKey("jobs.job_id"), index=True)
    recruiter_contact: Mapped[str] = mapped_column(String(200), default="")
    stage: Mapped[str] = mapped_column(String(80), default="")
    scheduled_at: Mapped[str] = mapped_column(String(40), default="")
    feedback: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ResumeVersion(Base):
    """Audit trail of every resume variant produced (truthfulness + provenance)."""
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True, default="")
    base_variant: Mapped[str] = mapped_column(String(80), default="")  # AI/ML, Backend, ...
    label: Mapped[str] = mapped_column(String(120), default="")
    pdf_path: Mapped[str] = mapped_column(String(600), default="")
    source_path: Mapped[str] = mapped_column(String(600), default="")
    content_hash: Mapped[str] = mapped_column(String(40), default="")
    flags: Mapped[list] = mapped_column(JSON, default=list)  # unverified-claim warnings
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AgentRun(Base):
    """Audit trail: every agent invocation, its status and timing."""
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent: Mapped[str] = mapped_column(String(60), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True, default="")
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|error
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    items_in: Mapped[int] = mapped_column(Integer, default=0)
    items_out: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class Event(Base):
    """Fine-grained action log surfaced in the dashboard 'Agent activity'."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")
    agent: Mapped[str] = mapped_column(String(60), default="", index=True)
    job_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class JobSource(Base):
    """Every place one job was found (Greenhouse board, Remotive, LinkedIn import,
    ...). Cross-source duplicates become extra rows here, not extra jobs, so a
    job can never be applied to twice because two portals listed it."""
    __tablename__ = "job_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), ForeignKey("jobs.job_id"), index=True)
    source_job_id: Mapped[str] = mapped_column(String(40), index=True)  # make_job_id(source, ...)
    source: Mapped[str] = mapped_column(String(40), default="")
    url: Mapped[str] = mapped_column(String(600), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("source_job_id", name="uq_job_sources_source_job"),)


class Company(Base):
    """Company registry: employers whose boards/careers pages are polled. Rows
    come from your profile, from "Add company" in the dashboard, or from company
    discovery (ATS links found in other listings)."""
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    provider: Mapped[str] = mapped_column(String(40), index=True)   # greenhouse|ashby|careers|...
    board: Mapped[str] = mapped_column(String(300), index=True)     # board id / careers URL
    careers_url: Mapped[str] = mapped_column(String(600), default="")
    domain: Mapped[str] = mapped_column(String(200), default="")
    categories: Mapped[list] = mapped_column(JSON, default=list)
    watch: Mapped[bool] = mapped_column(Boolean, default=True)      # polled each cycle
    discovered_by: Mapped[str] = mapped_column(String(80), default="")  # profile|user|feed:remotive|...
    open_jobs: Mapped[int] = mapped_column(Integer, default=0)   # relevant openings, last fetch
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("provider", "board", name="uq_company_provider_board"),)


class ProviderHealth(Base):
    """Per (provider, target) run history + circuit breaker."""
    __tablename__ = "provider_health"

    key: Mapped[str] = mapped_column(String(400), primary_key=True)  # "ashby:sarvam"
    provider: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str] = mapped_column(String(300), default="")
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ok: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    total_failures: Mapped[int] = mapped_column(Integer, default=0)
    jobs_last: Mapped[int] = mapped_column(Integer, default=0)
    jobs_total: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    disabled_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InboxMessage(Base):
    """A job-related email the inbox agent read (read-only IMAP). Only messages
    that look job-related are stored, and only a short snippet."""
    __tablename__ = "inbox_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[str] = mapped_column(String(400), unique=True, index=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sender: Mapped[str] = mapped_column(String(300), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(30), default="", index=True)
    job_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    links: Mapped[list] = mapped_column(JSON, default=list)       # booking / assessment links
    draft_reply: Mapped[str] = mapped_column(Text, default="")   # never sent automatically
    calendar_url: Mapped[str] = mapped_column(String(1200), default="")
    handled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class KeyValue(Base):
    """Small persistent key/value store: scheduler state, daily counters, sheet id."""
    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (UniqueConstraint("key"),)
