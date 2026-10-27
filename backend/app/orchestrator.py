"""Orchestrator — runs the pipeline and owns persistence/idempotency/recovery.

Pipeline:
  Discover -> Verify -> Deduplicate -> Score -> Select Resume -> Tailor Materials
  -> Prepare Application -> Approval Check -> (Submit) -> Verify Submission
  -> Update Google Sheets -> Follow Up

Guarantees:
  * Idempotent: jobs keyed by stable job_id; re-runs update, never duplicate.
  * Checkpointed: each job carries a pipeline_stage; a crash mid-run resumes
    from the last completed stage without re-doing or double-submitting.
  * Bounded: respects daily application caps and max concurrency.
  * Audited: AgentRun + Event rows for every step.
  * SAFE by default: never submits unless policy + approval allow.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .agents.base import (canonicalize_url, log_event, make_job_id, stronger_status)
from .agents.browser_apply import BrowserApplyAgent
from .agents.discovery import DiscoveryAgent
from .agents.followup import FollowupAgent
from .agents.materials import MaterialsAgent
from .agents.matching import MatchingAgent
from .agents.notifier import NotifierAgent
from .agents.resume_intel import ResumeIntelAgent
from .agents.sheets_tracker import SheetsAgent
from .agents.verification import VerificationAgent
from .config import ApprovalMode, get_settings
from .database import SessionLocal
from .logging_conf import get_logger
from .models import (AgentRun, ApplicationAttempt, ApplicationStatus,
                     EligibilityStatus, Event, Job, KeyValue, PipelineStage,
                     ResumeVersion)
from .profile_loader import load_profile
from .schemas import RawJob

log = get_logger("orchestrator")

# Minimum score to bother preparing an application (prioritization).
SHORTLIST_THRESHOLD = 45.0


class Orchestrator:
    def __init__(self, sheets: Optional[SheetsAgent] = None,
                 enabled_agents: Optional[set] = None):
        self.settings = get_settings()
        self._run_lock = threading.Lock()
        self.set_profile(load_profile())
        self.sheets = sheets or SheetsAgent()
        self.notifier = NotifierAgent()
        # Applications that stopped at ACTION_REQUIRED this run (emailed as one digest).
        self._needs_you: List[dict] = []
        # Any agent can be disabled (brief requirement).
        self.enabled = enabled_agents or {
            "discovery", "verification", "matching", "resume_intel",
            "materials", "browser_apply", "sheets", "followup", "notifier"}

    def set_profile(self, profile) -> None:
        """(Re)build the profile-dependent agents so an edited profile takes
        effect everywhere, not just on the orchestrator."""
        self.profile = profile
        self._profile_mtime = self._read_profile_mtime()
        self.discovery = DiscoveryAgent(profile)
        self.verify = VerificationAgent(profile)
        self.match = MatchingAgent(profile)
        self.resume = ResumeIntelAgent(profile)
        self.materials = MaterialsAgent(profile)
        self.browser = BrowserApplyAgent(profile)
        self.followup = FollowupAgent(profile)

    def _read_profile_mtime(self) -> float:
        try:
            return Path(self.settings.profile_path).stat().st_mtime
        except OSError:
            return 0.0

    def _refresh_profile(self) -> None:
        # Edits to candidate_profile.yaml (phone, answers, sources) apply on the
        # next run without a restart or pressing Reload.
        if self._read_profile_mtime() != self._profile_mtime:
            self.set_profile(load_profile(force=True))

    # ---- public entrypoints ---------------------------------------------- #
    def is_running(self) -> bool:
        return self._run_lock.locked()

    def run_full_cycle(self, discover_raw: Optional[List[RawJob]] = None) -> dict:
        # One pipeline run at a time (scheduler, button, startup recovery) so
        # two runs can never prepare/submit the same job concurrently.
        if not self._run_lock.acquire(blocking=False):
            return {"busy": True, "message": "A pipeline run is already in progress."}
        try:
            return self._run_full_cycle(discover_raw)
        finally:
            self._run_lock.release()

    def _run_full_cycle(self, discover_raw: Optional[List[RawJob]] = None) -> dict:
        """One end-to-end cycle. `discover_raw` lets tests inject jobs without
        live HTTP; in production it's None and the discovery agent fetches."""
        run_id = uuid.uuid4().hex[:12]
        db = SessionLocal()
        stats = {"run_id": run_id, "discovered": 0, "new": 0, "verified": 0,
                 "shortlisted": 0, "prepared": 0, "submitted": 0, "errors": 0}
        self._needs_you = []
        self._refresh_profile()
        try:
            if self._paused(db):
                log.info("Automation paused; skipping cycle.")
                return {**stats, "paused": True}

            stats["reverified"] = self._reverify_open(db)
            raw = self._discover(db, run_id, discover_raw)
            stats["discovered"] = len(raw)
            new_or_updated = self._ingest_dedup(db, raw)
            stats["new"] = sum(1 for x in new_or_updated if x[1])
            # process each job through the remaining stages (resumable)
            done_ids = set()
            for job, _is_new in new_or_updated:
                self._process_job(db, job, stats)
                done_ids.add(job.job_id)
            # plus anything left mid-pipeline earlier (e.g. queued by the daily cap)
            for job in self._pending_jobs(db):
                if job.job_id not in done_ids:
                    self._process_job(db, job, stats)
            # sheets dashboard + followup
            self._sync_sheets(db)
            if "followup" in self.enabled:
                self.followup.run(db)
            self._send_digest(db)
            self._set_kv(db, "last_cycle", {"run_id": run_id, "at": _now_iso(), "stats": stats})
            return stats
        except Exception as e:
            stats["errors"] += 1
            log.exception(f"cycle failed: {e}")
            return stats
        finally:
            db.close()

    def process_pending(self) -> dict:
        """Resume any jobs left mid-pipeline (crash recovery)."""
        if not self._run_lock.acquire(blocking=False):
            return {"busy": True, "message": "A pipeline run is already in progress."}
        try:
            return self._process_pending()
        finally:
            self._run_lock.release()

    def _process_pending(self) -> dict:
        db = SessionLocal()
        stats = {"resumed": 0}
        self._needs_you = []
        self._refresh_profile()
        try:
            if self._paused(db):
                return {**stats, "paused": True}
            stats["reverified"] = self._reverify_open(db)
            for job in self._pending_jobs(db):
                self._process_job(db, job, stats)
                stats["resumed"] += 1
            self._sync_sheets(db)
            self._send_digest(db)
            return stats
        finally:
            db.close()

    def _reverify_open(self, db) -> int:
        """Re-run eligibility on every job not applied to yet, so rule changes
        (and profile edits) apply to old listings too — not only new ones.
        Returns how many changed status."""
        if "verification" not in self.enabled:
            return 0
        open_statuses = [ApplicationStatus.NONE.value, ApplicationStatus.DRAFTED.value,
                         ApplicationStatus.READY_FOR_REVIEW.value,
                         ApplicationStatus.ACTION_REQUIRED.value]
        max_years = self.profile.raw().get("experience_level", {}).get("max_years", 2)
        changed = 0
        for job in db.scalars(select(Job).where(Job.application_status.in_(open_statuses))):
            vr = self.verify.verify(RawJob(**_job_to_rawdict(job)), max_years=max_years)
            if vr.status == job.eligibility_status and vr.reasons == job.eligibility_reasons:
                continue
            was_rejected = job.eligibility_status == EligibilityStatus.REJECTED.value
            job.eligibility_status = vr.status
            job.eligibility_reasons = vr.reasons
            job.last_checked = datetime.now(timezone.utc)
            if vr.status == EligibilityStatus.REJECTED.value:
                job.pipeline_stage = PipelineStage.DONE.value
                job.next_action = "Rejected at verification."
            elif was_rejected:
                # rules loosened: send it back through scoring
                job.pipeline_stage = PipelineStage.VERIFIED.value
                job.next_action = ""
            changed += 1
        db.commit()
        if changed:
            log_event(db, level="info", agent="verification",
                      message=f"Re-verified open jobs: {changed} changed status")
        return changed

    def _pending_jobs(self, db) -> List[Job]:
        # Best matches first, so the daily cap is spent on them.
        return db.scalars(select(Job).where(
            Job.pipeline_stage != PipelineStage.DONE.value,
            Job.eligibility_status != EligibilityStatus.REJECTED.value)
            .order_by(Job.match_score.desc().nullslast())).all()

    def _send_digest(self, db):
        if "notifier" in self.enabled and self._needs_you:
            self.notifier.cycle_digest(db, self._needs_you)
        self._needs_you = []

    # ---- stages ----------------------------------------------------------- #
    def _discover(self, db, run_id, injected) -> List[RawJob]:
        if "discovery" not in self.enabled:
            return []
        ar = self._start_run(db, "discovery", run_id)
        try:
            raw = injected if injected is not None else self.discovery.discover()
            self._finish_run(db, ar, items_out=len(raw))
            log_event(db, level="info", agent="discovery",
                      message=f"Discovered {len(raw)} raw listings")
            return raw
        except Exception as e:
            self._finish_run(db, ar, error=str(e))
            return []

    def _ingest_dedup(self, db, raw: List[RawJob]):
        """Insert/update jobs idempotently. Dedup by job_id (source+id|url) and
        by (company,title) + canonical_url."""
        results = []
        seen_keys = set()
        for rj in raw:
            canon = canonicalize_url(rj.canonical_url or rj.job_url)
            jid = make_job_id(rj.source, rj.source_id, canon)
            # within-batch dedup
            dedup_key = (rj.company.lower(), rj.title.lower(), canon)
            if jid in seen_keys or dedup_key in seen_keys:
                continue
            seen_keys.add(jid); seen_keys.add(dedup_key)

            try:
                job = db.scalar(select(Job).where(Job.job_id == jid))
                is_new = job is None
                if is_new:
                    job = Job(job_id=jid, source=rj.source, source_id=rj.source_id,
                              canonical_url=canon)
                    db.add(job)
                # update descriptive fields (safe to refresh)
                job.company = rj.company or job.company
                job.title = rj.title or job.title
                job.description = rj.description or job.description
                job.location = rj.location or job.location
                job.work_mode = rj.work_mode or job.work_mode
                job.employment_type = rj.employment_type or job.employment_type
                job.experience_req = rj.experience_req or job.experience_req
                job.salary_range = rj.salary_range or job.salary_range
                job.posted_at = rj.posted_at or job.posted_at
                job.job_url = rj.job_url or job.job_url
                job.application_url = rj.application_url or job.application_url
                db.commit()
            except IntegrityError:
                # Inserted concurrently (another process/run) — skip, don't kill the cycle.
                db.rollback()
                continue
            results.append((job, is_new))
        return results

    def _process_job(self, db: Session, job: Job, stats: dict) -> None:
        """Advance one job through the pipeline from its current checkpoint."""
        try:
            stage = PipelineStage(job.pipeline_stage)
            # Finished jobs (submitted, rejected, below threshold, ...) are never
            # re-run — otherwise a rediscovered listing could be submitted twice.
            if stage == PipelineStage.DONE:
                return

            # VERIFY
            if _stage_lt(stage, PipelineStage.VERIFIED) and "verification" in self.enabled:
                vr = self.verify.verify(RawJob(**_job_to_rawdict(job)),
                                        max_years=self.profile.raw()
                                        .get("experience_level", {}).get("max_years", 2))
                job.eligibility_status = vr.status
                job.eligibility_reasons = vr.reasons
                job.last_checked = datetime.now(timezone.utc)
                job.pipeline_stage = PipelineStage.VERIFIED.value
                db.commit()
                if vr.status in (EligibilityStatus.VERIFIED.value,):
                    stats["verified"] = stats.get("verified", 0) + 1
                if vr.status == EligibilityStatus.REJECTED.value:
                    job.pipeline_stage = PipelineStage.DONE.value
                    job.next_action = "Rejected at verification."
                    db.commit()
                    return

            # SCORE
            if _stage_lt(PipelineStage(job.pipeline_stage), PipelineStage.SCORED) \
                    and "matching" in self.enabled:
                sr = self.match.score(RawJob(**_job_to_rawdict(job)), job.eligibility_status)
                job.match_score = sr.total
                job.score_breakdown = sr.breakdown
                job.matching_skills = sr.matching_skills
                job.missing_skills = sr.missing_skills
                job.recommended_resume_version = sr.recommended_resume_version
                job.score_explanation = sr.explanation
                job.pipeline_stage = PipelineStage.SCORED.value
                db.commit()

            # SHORTLIST GATE
            if (job.match_score or 0) < SHORTLIST_THRESHOLD or \
                    job.eligibility_status == EligibilityStatus.REJECTED.value:
                job.pipeline_stage = PipelineStage.DONE.value
                job.next_action = job.next_action or "Below shortlist threshold."
                db.commit()
                return
            stats["shortlisted"] = stats.get("shortlisted", 0) + 1

            # DAILY CAP
            if self._prepared_today(db) >= self.settings.daily_application_limit:
                job.next_action = "Daily application cap reached — queued for next run."
                db.commit()
                return

            # RESUME + MATERIALS
            from .schemas import ScoreResult
            sr = ScoreResult(total=job.match_score or 0, breakdown=job.score_breakdown or {},
                             matching_skills=job.matching_skills or [],
                             missing_skills=job.missing_skills or [],
                             recommended_resume_version=job.recommended_resume_version,
                             explanation=job.score_explanation)
            if _stage_lt(PipelineStage(job.pipeline_stage), PipelineStage.RESUME_SELECTED) \
                    and "resume_intel" in self.enabled:
                r = self.resume.tailor_for_job(db, job, sr)
                job.tailored_resume_path = r["pdf_path"]
                job.recommended_resume_version = r["label"]
                job.pipeline_stage = PipelineStage.RESUME_SELECTED.value
                db.commit()

            mats = None
            if _stage_lt(PipelineStage(job.pipeline_stage), PipelineStage.MATERIALS_READY) \
                    and "materials" in self.enabled:
                mats = self.materials.build(db, job, tailored_summary=job.score_explanation)
                if mats.form_answers.get("_cover_letter_path"):
                    job.cover_letter_path = mats.form_answers["_cover_letter_path"]
                if mats.unknown_fields:
                    job.notes = (job.notes or "") + \
                        f" | Ask human: {', '.join(mats.unknown_fields[:4])}"
                job.pipeline_stage = PipelineStage.MATERIALS_READY.value
                db.commit()
            stats["prepared"] = stats.get("prepared", 0) + 1

            # APPROVAL CHECK + (maybe) SUBMIT
            self._apply_stage(db, job, mats, stats)

            if job.pipeline_stage != PipelineStage.DONE.value:
                job.pipeline_stage = PipelineStage.APPLICATION_PREPARED.value
                db.commit()
        except Exception as e:
            stats["errors"] = stats.get("errors", 0) + 1
            log_event(db, level="error", agent="orchestrator",
                      message=f"process_job failed: {e}", job_id=job.job_id)
            db.rollback()

    def _apply_stage(self, db, job, mats, stats):
        """Decide between draft (Mode A) and auto-submit (Mode B). Never submits
        in SAFE mode."""
        if "browser_apply" not in self.enabled:
            job.application_status = stronger_status(job.application_status,
                                                     ApplicationStatus.DRAFTED.value)
            job.next_action = "Materials ready (browser agent disabled)."
            db.commit()
            return

        if self.settings.approval_mode == ApprovalMode.SAFE:
            # Draft only; stop for human review. No browser launch by default in
            # a cycle (that happens on explicit user action from the dashboard).
            job.application_status = stronger_status(job.application_status,
                                                     ApplicationStatus.READY_FOR_REVIEW.value)
            job.next_action = "Review & approve to submit (SAFE mode)."
            db.commit()
            return

        # Mode B: check policy, then attempt via browser agent.
        allow, why = self.browser.may_autosubmit(job, mats, self._autosubmit_today(db))
        if not allow:
            job.application_status = stronger_status(job.application_status,
                                                     ApplicationStatus.READY_FOR_REVIEW.value)
            job.next_action = f"Auto-submit blocked: {why}"
            db.commit()
            return
        if self._waiting_for_answers(db, job):
            return
        outcome = self.submit_job(db, job, mats)
        if outcome.status in (ApplicationStatus.SUBMITTED_CONFIRMED.value,
                              ApplicationStatus.SUBMISSION_UNVERIFIED.value):
            stats["submitted"] = stats.get("submitted", 0) + 1
        if outcome.status == ApplicationStatus.ACTION_REQUIRED.value and \
                outcome.detail.get("missing"):
            # Stopped only for an answer you haven't given yet: stays queued and
            # retries by itself once candidate_profile.yaml changes.
            return
        # Otherwise one automatic attempt per job (submitted, CAPTCHA, failure).
        # Anything that needs you is emailed; retry it with Approve.
        job.pipeline_stage = PipelineStage.DONE.value
        db.commit()

    def _waiting_for_answers(self, db, job) -> bool:
        """True when the last attempt stopped on unanswered questions and the
        profile hasn't been edited since — re-trying would hit the same wall."""
        last = db.scalar(select(ApplicationAttempt)
                         .where(ApplicationAttempt.job_id == job.job_id)
                         .order_by(ApplicationAttempt.started_at.desc()))
        if not last or last.result != ApplicationStatus.ACTION_REQUIRED.value \
                or not (last.detail or {}).get("missing"):
            return False
        try:
            edited = datetime.fromtimestamp(Path(self.settings.profile_path).stat().st_mtime,
                                            timezone.utc).replace(tzinfo=None)
        except OSError:
            return True
        return edited <= (last.finished_at or last.started_at)

    def _upload_resume_path(self, db, job) -> str:
        """Upload YOUR original resume (the variant picked for this job) rather
        than the plain generated one; fall back to the tailored file."""
        rv = db.scalar(select(ResumeVersion).where(ResumeVersion.job_id == job.job_id)
                       .order_by(ResumeVersion.created_at.desc()))
        if rv and rv.base_variant.endswith(")") and "(" in rv.base_variant:
            name = rv.base_variant.rsplit("(", 1)[1][:-1]
            p = Path(self.settings.resumes_dir) / name
            if p.exists():
                return str(p)
        return job.tailored_resume_path

    def submit_job(self, db, job, mats=None, approved: bool = False):
        """Explicit submission (called by dashboard approval OR Mode B)."""
        attempt_no = db.scalar(select(func.count(ApplicationAttempt.id))
                               .where(ApplicationAttempt.job_id == job.job_id)) + 1
        att = ApplicationAttempt(job_id=job.job_id, attempt_no=attempt_no,
                                 result="running")
        db.add(att); db.commit()
        if mats is None and "materials" in self.enabled:
            # resumed job: rebuild form answers without rewriting the cover letter
            mats = self.materials.build(db, job, need_cover=False)
        outcome = self.browser.apply(db, job, self._upload_resume_path(db, job),
                                     job.cover_letter_path, mats,
                                     self._autosubmit_today(db), approved=approved)
        att.result = outcome.status
        att.error = outcome.error
        att.detail = outcome.detail
        att.finished_at = datetime.now(timezone.utc)
        job.application_status = stronger_status(job.application_status, outcome.status)
        if outcome.status == ApplicationStatus.SUBMITTED_CONFIRMED.value:
            job.submission_date = _now_iso()
            job.confirmation_reference = outcome.detail.get("reference", "")
            job.pipeline_stage = PipelineStage.DONE.value
            self._bump_autosubmit(db)
        elif outcome.status == ApplicationStatus.SUBMISSION_UNVERIFIED.value:
            job.submission_date = _now_iso()
            job.next_action = "Submitted — check your inbox for the company's confirmation."
            self._bump_autosubmit(db)
        elif outcome.status in (ApplicationStatus.ACTION_REQUIRED.value,
                                ApplicationStatus.FAILED.value):
            job.next_action = (outcome.error or "")[:290]
            self._needs_you.append({"title": job.title, "company": job.company,
                                    "why": outcome.error or outcome.status,
                                    "missing": outcome.detail.get("missing") or [],
                                    "url": outcome.detail.get("url") or job.job_url})
        db.commit()
        if "notifier" in self.enabled and outcome.status in (
                ApplicationStatus.SUBMITTED_CONFIRMED.value,
                ApplicationStatus.SUBMISSION_UNVERIFIED.value):
            self.notifier.application_submitted(db, job, outcome)
        self.sheets_safe(lambda: self.sheets.record_attempt(
            job, attempt_no, outcome.status, outcome.error, outcome.detail))
        self.sheets_safe(lambda: self.sheets.upsert_job(job))
        return outcome

    def _sync_sheets(self, db):
        if "sheets" not in self.enabled:
            return
        if not self.sheets.enabled:
            return
        self.sheets_safe(self.sheets.ensure_spreadsheet)
        # Only jobs worth tracking (shortlisted or with an application); syncing
        # thousands of rejected listings would blow the Sheets API quota.
        # Tracked: eligible jobs, shortlisted ones (so a row turns REJECTED
        # instead of going stale), and anything with an application.
        jobs = db.scalars(select(Job).where(
            Job.eligibility_status.in_([EligibilityStatus.VERIFIED.value,
                                        EligibilityStatus.NEEDS_REVIEW.value]) |
            (Job.match_score >= SHORTLIST_THRESHOLD) |
            (Job.application_status != ApplicationStatus.NONE.value))
            .order_by(Job.match_score.desc().nullslast())).all()
        res = self.sheets_safe(lambda: self.sheets.sync_jobs(jobs))
        self.sheets_safe(lambda: self.sheets.update_dashboard(compute_stats(db)))
        if res:
            log.info("Sheets synced", extra=res)

    def sheets_safe(self, fn):
        try:
            return fn()
        except Exception as e:
            log.warning(f"sheets op failed (non-fatal): {e}")

    # ---- small state helpers --------------------------------------------- #
    def _start_run(self, db, agent, run_id) -> AgentRun:
        ar = AgentRun(agent=agent, run_id=run_id, status="running")
        db.add(ar); db.commit(); return ar

    def _finish_run(self, db, ar, items_out=0, error=""):
        ar.status = "error" if error else "ok"
        ar.items_out = items_out
        ar.error = error
        ar.finished_at = datetime.now(timezone.utc)
        db.commit()

    def _paused(self, db) -> bool:
        kv = db.get(KeyValue, "paused")
        return bool(kv and kv.value.get("paused"))

    def set_paused(self, paused: bool):
        db = SessionLocal()
        try:
            self._set_kv(db, "paused", {"paused": paused})
        finally:
            db.close()

    def _set_kv(self, db, key, value):
        kv = db.get(KeyValue, key)
        if kv:
            kv.value = value
        else:
            db.add(KeyValue(key=key, value=value))
        db.commit()

    def _prepared_today(self, db) -> int:
        # Jobs that got a tailored resume today. (Counting DONE jobs updated
        # today also counted every job REJECTED today, so the cap was "reached"
        # before a single application had been prepared.)
        today = datetime.now(timezone.utc).date()
        return db.scalar(select(func.count(func.distinct(ResumeVersion.job_id))).where(
            func.date(ResumeVersion.created_at) == today.isoformat())) or 0

    def _autosubmit_today(self, db) -> int:
        kv = db.get(KeyValue, f"autosubmit_{datetime.now(timezone.utc).date().isoformat()}")
        return (kv.value.get("count", 0) if kv else 0)

    def _bump_autosubmit(self, db):
        key = f"autosubmit_{datetime.now(timezone.utc).date().isoformat()}"
        kv = db.get(KeyValue, key)
        n = (kv.value.get("count", 0) if kv else 0) + 1
        self._set_kv(db, key, {"count": n})


# ---- module-level helpers ------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage_lt(a: PipelineStage, b: PipelineStage) -> bool:
    order = list(PipelineStage)
    return order.index(a) < order.index(b)


def _job_to_rawdict(job: Job) -> dict:
    return {
        "source": job.source, "source_id": job.source_id, "company": job.company,
        "title": job.title, "description": job.description, "location": job.location,
        "work_mode": job.work_mode, "employment_type": job.employment_type,
        "experience_req": job.experience_req, "salary_range": job.salary_range,
        "posted_at": job.posted_at, "job_url": job.job_url,
        "application_url": job.application_url, "canonical_url": job.canonical_url,
    }


def compute_stats(db: Session) -> dict:
    from .models import Interview
    def count(**kw):
        q = select(func.count(Job.id))
        for k, v in kw.items():
            q = q.where(getattr(Job, k) == v)
        return db.scalar(q) or 0

    by_source = dict(db.execute(
        select(Job.source, func.count(Job.id)).group_by(Job.source)).all())
    by_status = dict(db.execute(
        select(Job.application_status, func.count(Job.id))
        .group_by(Job.application_status)).all())

    return {
        "discovered": db.scalar(select(func.count(Job.id))) or 0,
        "verified_eligible": count(eligibility_status=EligibilityStatus.VERIFIED.value),
        "shortlisted": db.scalar(select(func.count(Job.id)).where(
            Job.match_score >= SHORTLIST_THRESHOLD)) or 0,
        "prepared": db.scalar(select(func.count(Job.id)).where(
            Job.application_status.in_([ApplicationStatus.READY_FOR_REVIEW.value,
                                        ApplicationStatus.DRAFTED.value,
                                        ApplicationStatus.APPROVED.value]))) or 0,
        "submitted_confirmed": count(application_status=ApplicationStatus.SUBMITTED_CONFIRMED.value),
        "submission_failed_or_unverified": db.scalar(select(func.count(Job.id)).where(
            Job.application_status.in_([ApplicationStatus.FAILED.value,
                                        ApplicationStatus.SUBMISSION_UNVERIFIED.value]))) or 0,
        "interviews": db.scalar(select(func.count(Interview.id))) or 0,
        "rejected": count(eligibility_status=EligibilityStatus.REJECTED.value),
        "pending_followups": db.scalar(select(func.count(Job.id)).where(
            Job.follow_up_date != "")) or 0,
        "by_source": by_source,
        "by_status": by_status,
    }
