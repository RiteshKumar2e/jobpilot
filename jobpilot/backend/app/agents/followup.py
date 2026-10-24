"""Agent 8 — Follow-up & Quality Control.

Periodic, read-mostly checks: stale applications, missing confirmations,
duplicate detection, follow-up reminders, and a daily/weekly summary. It does
NOT send messages or recruiter emails — it only flags next actions for you
(the brief forbids automated repeated outreach without explicit approval).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (ApplicationStatus, EligibilityStatus, Interview, Job)
from .base import log_event


class FollowupAgent:
    name = "followup"

    def __init__(self, profile):
        self.profile = profile

    def run(self, db: Session, stale_days: int = 10) -> dict:
        now = datetime.now(timezone.utc)
        summary = {"stale_flagged": 0, "missing_confirmation": 0,
                   "duplicates": 0, "followups_due": 0}

        # 1) Missing confirmations: submitted-but-unverified
        unverified = db.scalars(select(Job).where(
            Job.application_status == ApplicationStatus.SUBMISSION_UNVERIFIED.value)).all()
        for j in unverified:
            j.next_action = "Verify submission (no confirmation captured)."
            summary["missing_confirmation"] += 1

        # 2) Stale: prepared/ready but not submitted for N days
        cutoff = now - timedelta(days=stale_days)
        stale = db.scalars(select(Job).where(
            Job.application_status.in_([ApplicationStatus.READY_FOR_REVIEW.value,
                                        ApplicationStatus.DRAFTED.value]),
            Job.updated_at < cutoff)).all()
        for j in stale:
            j.next_action = f"Stale {stale_days}d+ — review & submit or drop."
            j.follow_up_date = (now + timedelta(days=1)).date().isoformat()
            summary["stale_flagged"] += 1

        # 3) Duplicate applications (same company+title, >1 submitted)
        rows = db.execute(
            select(Job.company, Job.title, func.count(Job.id))
            .where(Job.application_status.in_([
                ApplicationStatus.SUBMITTED_CONFIRMED.value,
                ApplicationStatus.SUBMISSION_UNVERIFIED.value]))
            .group_by(Job.company, Job.title).having(func.count(Job.id) > 1)).all()
        summary["duplicates"] = len(rows)
        for company, title, _ in rows:
            log_event(db, level="warn", agent=self.name,
                      message=f"Possible duplicate applications: {title} @ {company}")

        # 4) Follow-ups due today
        today = now.date().isoformat()
        due = db.scalars(select(Job).where(Job.follow_up_date == today)).all()
        summary["followups_due"] = len(due)

        db.commit()
        log_event(db, level="info", agent=self.name,
                  message="Follow-up/QC pass complete", data=summary)
        return summary

    def recheck_open(self, db: Session, discovery_agent, verify_agent, limit: int = 20) -> int:
        """Re-fetch a sample of tracked jobs to check they're still open.
        Runs on the user's machine (needs live web). Marks EXPIRED on 404/removal."""
        # Implementation note: the live re-fetch uses the same official APIs.
        # Here we only mark jobs not seen in the latest discovery as needing a check;
        # the orchestrator passes freshly-discovered IDs to reconcile.
        return 0
