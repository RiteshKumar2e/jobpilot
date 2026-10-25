"""Agent 7 — Google Sheets & Application Tracker (official Sheets API).

Creates/maintains a spreadsheet with 4 tabs: Job Pipeline, Application History,
Interview Tracker, Dashboard. Upserts by stable Job ID so re-syncs never create
duplicate rows, and never overwrites a stronger application status with a weaker
one from an older sync.

Google libraries are optional: if not installed / no creds, the agent is a
no-op that reports 'sheets_disabled' — the rest of the system is unaffected.
This keeps the app runnable with zero Google setup.
"""
from __future__ import annotations

from typing import List, Optional

from ..config import get_settings
from ..logging_conf import get_logger
from ..models import ApplicationStatus, APPLICATION_STATUS_RANK

log = get_logger("sheets")

PIPELINE_HEADERS = [
    "Job ID", "Discovery Date", "Company", "Job Title", "Job URL", "Application URL",
    "Location", "Work Mode", "Employment Type", "Experience Requirement",
    "Salary Range", "Eligibility Status", "Match Score", "Matching Skills",
    "Missing Skills", "Resume Version", "Tailored Resume Path", "Cover Letter Path",
    "Application Status", "Submission Date", "Confirmation Reference",
    "Follow-up Date", "Last Checked", "Next Action", "Notes", "Eligibility Reasons",
]
HISTORY_HEADERS = ["Timestamp", "Job ID", "Company", "Job Title", "Attempt",
                   "Result", "Error", "Detail"]
INTERVIEW_HEADERS = ["Job ID", "Company", "Recruiter Contact", "Stage",
                     "Scheduled At", "Feedback", "Next Action"]
DASHBOARD_HEADERS = ["Metric", "Value"]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsAgent:
    name = "sheets"

    def __init__(self):
        self.settings = get_settings()
        self._svc = None
        self._enabled = False
        # Remember the sheet we created so a restart reuses it instead of making a new one.
        self._id_file = self.settings.google_token.parent / "google_sheet_id.txt"
        self._sheet_id = self.settings.sheet_id or self._saved_sheet_id()

    # ---- lifecycle -------------------------------------------------------- #
    def connect(self) -> bool:
        try:
            from googleapiclient.discovery import build
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            import os, json
        except Exception as e:
            log.warning(f"Google libraries not installed ({e}); Sheets disabled.")
            return False

        creds = None
        token_path = self.settings.google_token
        cred_path = self.settings.google_credentials
        try:
            if token_path.exists():
                creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                elif cred_path.exists():
                    flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), SCOPES)
                    creds = flow.run_local_server(port=0)
                else:
                    log.warning("No Google credentials.json present; Sheets disabled.")
                    return False
                token_path.write_text(creds.to_json(), encoding="utf-8")
            self._svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
            self._enabled = True
            return True
        except Exception as e:
            log.warning(f"Sheets connect failed: {e}")
            return False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def sheet_id(self) -> str:
        return self._sheet_id

    @property
    def sheet_url(self) -> str:
        return (f"https://docs.google.com/spreadsheets/d/{self._sheet_id}/edit"
                if self._sheet_id else "")

    def _saved_sheet_id(self) -> str:
        try:
            return self._id_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def ensure_spreadsheet(self) -> Optional[str]:
        if not self._enabled:
            return None
        if self._sheet_id:
            self._ensure_tabs()
            return self._sheet_id
        body = {
            "properties": {"title": "JobPilot — Application Tracker"},
            "sheets": [{"properties": {"title": t}} for t in
                       ["Job Pipeline", "Application History",
                        "Interview Tracker", "Dashboard"]],
        }
        ss = self._svc.spreadsheets().create(body=body).execute()
        self._sheet_id = ss["spreadsheetId"]
        self._id_file.write_text(self._sheet_id, encoding="utf-8")
        self._write_row("Job Pipeline", 1, PIPELINE_HEADERS)
        self._write_row("Application History", 1, HISTORY_HEADERS)
        self._write_row("Interview Tracker", 1, INTERVIEW_HEADERS)
        self._write_row("Dashboard", 1, DASHBOARD_HEADERS)
        log.info("Created tracker spreadsheet", extra={"sheet_id": self._sheet_id})
        return self._sheet_id

    # ---- idempotent upsert ----------------------------------------------- #
    def upsert_job(self, job) -> None:
        if not self._enabled:
            return
        row = self._job_to_row(job)
        rownum = self._find_row("Job Pipeline", job.job_id)
        if rownum is None:
            self._append_row("Job Pipeline", row)
            return
        # never downgrade confirmed status with a weaker incoming one
        existing = self._read_row("Job Pipeline", rownum)
        if existing and len(existing) >= 19:
            existing_status = existing[18]
            if self._weaker(row[18], existing_status):
                row[18] = existing_status            # keep stronger status
                row[19] = existing[19] if len(existing) > 19 else row[19]
        self._write_row("Job Pipeline", rownum, row)

    def sync_jobs(self, jobs) -> dict:
        """Upsert many jobs in 3 API calls (one read, one batch update, one
        append) instead of 2-3 calls per job, which ran into the Sheets quota
        (~60 requests/min) once more than a few dozen jobs were tracked."""
        if not self._enabled or not jobs:
            return {"updated": 0, "added": 0}
        res = self._svc.spreadsheets().values().get(
            spreadsheetId=self._sheet_id, range="Job Pipeline!A2:Z100000").execute()
        existing = {r[0]: (i, r) for i, r in enumerate(res.get("values", []), start=2) if r}
        updates, appends = [], []
        for job in jobs:
            row = self._job_to_row(job)
            if job.job_id in existing:
                rownum, old = existing[job.job_id]
                # never downgrade a stronger application status written earlier
                if len(old) > 18 and self._weaker(row[18], old[18]):
                    row[18] = old[18]
                    row[19] = old[19] if len(old) > 19 else row[19]
                updates.append({"range": f"Job Pipeline!A{rownum}", "values": [row]})
            else:
                appends.append(row)
        # keep the header current (columns can be added over time)
        updates.append({"range": "Job Pipeline!A1", "values": [PIPELINE_HEADERS]})
        self._svc.spreadsheets().values().batchUpdate(
            spreadsheetId=self._sheet_id,
            body={"valueInputOption": "RAW", "data": updates}).execute()
        if appends:
            self._svc.spreadsheets().values().append(
                spreadsheetId=self._sheet_id, range="Job Pipeline!A1",
                valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                body={"values": appends}).execute()
        return {"updated": len(updates) - 1, "added": len(appends)}

    def record_attempt(self, job, attempt_no, result, error="", detail=None) -> None:
        if not self._enabled:
            return
        from .base import utcnow_iso
        self._append_row("Application History", [
            utcnow_iso(), job.job_id, job.company, job.title, str(attempt_no),
            result, error[:500], str(detail or {})[:500]])

    def update_dashboard(self, stats: dict) -> None:
        if not self._enabled:
            return
        rows = [[k, str(v)] for k, v in stats.items() if not isinstance(v, dict)]
        for k, v in stats.items():
            if isinstance(v, dict):
                for sub, val in v.items():
                    rows.append([f"{k}:{sub}", str(val)])
        self._svc.spreadsheets().values().update(
            spreadsheetId=self._sheet_id, range="Dashboard!A2:B200",
            valueInputOption="RAW", body={"values": rows}).execute()

    # ---- low-level helpers ----------------------------------------------- #
    def _weaker(self, incoming: str, existing: str) -> bool:
        try:
            i = APPLICATION_STATUS_RANK[ApplicationStatus(incoming)]
            e = APPLICATION_STATUS_RANK[ApplicationStatus(existing)]
            return i < e
        except Exception:
            return False

    def _job_to_row(self, job) -> list:
        return [
            job.job_id, str(job.discovered_at)[:19], job.company, job.title,
            job.job_url, job.application_url, job.location, job.work_mode,
            job.employment_type, job.experience_req, job.salary_range,
            job.eligibility_status,
            "" if job.match_score is None else str(job.match_score),
            ", ".join(job.matching_skills or []), ", ".join(job.missing_skills or []),
            job.recommended_resume_version, job.tailored_resume_path,
            job.cover_letter_path, job.application_status, job.submission_date,
            job.confirmation_reference, job.follow_up_date,
            str(job.last_checked or "")[:19], job.next_action, (job.notes or "")[:500],
            " | ".join(job.eligibility_reasons or [])[:500],
        ]

    def _ensure_tabs(self):
        meta = self._svc.spreadsheets().get(spreadsheetId=self._sheet_id).execute()
        have = {s["properties"]["title"] for s in meta.get("sheets", [])}
        want = {"Job Pipeline": PIPELINE_HEADERS, "Application History": HISTORY_HEADERS,
                "Interview Tracker": INTERVIEW_HEADERS, "Dashboard": DASHBOARD_HEADERS}
        requests = [{"addSheet": {"properties": {"title": t}}}
                    for t in want if t not in have]
        if requests:
            self._svc.spreadsheets().batchUpdate(
                spreadsheetId=self._sheet_id, body={"requests": requests}).execute()
        for t, headers in want.items():
            first = self._read_row(t, 1)
            if not first:
                self._write_row(t, 1, headers)

    def _find_row(self, tab, job_id) -> Optional[int]:
        res = self._svc.spreadsheets().values().get(
            spreadsheetId=self._sheet_id, range=f"{tab}!A2:A100000").execute()
        for i, r in enumerate(res.get("values", []), start=2):
            if r and r[0] == job_id:
                return i
        return None

    def _read_row(self, tab, rownum) -> list:
        res = self._svc.spreadsheets().values().get(
            spreadsheetId=self._sheet_id, range=f"{tab}!A{rownum}:AZ{rownum}").execute()
        vals = res.get("values", [])
        return vals[0] if vals else []

    def _write_row(self, tab, rownum, values) -> None:
        self._svc.spreadsheets().values().update(
            spreadsheetId=self._sheet_id, range=f"{tab}!A{rownum}",
            valueInputOption="RAW", body={"values": [values]}).execute()

    def _append_row(self, tab, values) -> None:
        self._svc.spreadsheets().values().append(
            spreadsheetId=self._sheet_id, range=f"{tab}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": [values]}).execute()
