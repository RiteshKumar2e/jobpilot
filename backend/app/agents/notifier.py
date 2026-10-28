"""Agent 9 — Notifier.

Emails you when an application is submitted, and sends one digest per cycle of
the applications that need you (CAPTCHA, an unanswered required question, ...).
Uses plain SMTP (Gmail App Password). Without SMTP credentials it logs and
returns — it never blocks the pipeline.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import List

from ..config import get_settings
from ..logging_conf import get_logger
from .base import log_event

log = get_logger("notifier")


class NotifierAgent:
    name = "notifier"

    def __init__(self):
        self.settings = get_settings()

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(s.notify_email and s.smtp_user and s.smtp_password)

    def send(self, subject: str, body: str) -> bool:
        s = self.settings
        if not self.enabled:
            log.info(f"Email not configured; skipped: {subject}")
            return False
        msg = EmailMessage()
        msg["From"] = s.smtp_user
        msg["To"] = s.notify_email
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            if s.smtp_port == 465:
                with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20,
                                      context=ssl.create_default_context()) as smtp:
                    smtp.login(s.smtp_user, s.smtp_password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.login(s.smtp_user, s.smtp_password)
                    smtp.send_message(msg)
            return True
        except Exception as e:
            log.warning(f"email send failed: {e}")
            return False

    def application_submitted(self, db, job, outcome) -> None:
        confirmed = outcome.status == "SUBMITTED_CONFIRMED"
        subject = (f"✅ Applied: {job.title} @ {job.company}" if confirmed
                   else f"⚠️ Submitted (unconfirmed): {job.title} @ {job.company}")
        body = "\n".join([
            f"Role:      {job.title}",
            f"Company:   {job.company}",
            f"Location:  {job.location or '-'} ({job.work_mode or '-'})",
            f"Score:     {job.match_score}",
            f"Resume:    {job.recommended_resume_version}",
            f"Status:    {outcome.status}",
            f"Reference: {outcome.detail.get('reference') or '-'}",
            f"Link:      {job.job_url}",
            "",
            *([] if confirmed else [
                "No confirmation page was seen. If you got an email with a security",
                "code from the company / Greenhouse, open the link above and enter it",
                "— the application is not complete until you do.", ""]),
            "Sent by JobPilot AI.",
        ])
        sent = self.send(subject, body)
        log_event(db, level="info", agent=self.name, job_id=job.job_id,
                  message=f"Submission email {'sent' if sent else 'skipped'}: {job.title} @ {job.company}")

    def cycle_digest(self, db, needs_you: List[dict]) -> None:
        if not needs_you:
            return
        lines = [f"{len(needs_you)} application(s) need you — finish the form, or apply "
                 f"yourself where the source has no automated submission:", ""]
        for i, j in enumerate(needs_you, 1):
            lines += [f"{i}. {j['title']} @ {j['company']}",
                      f"   Why:  {j['why']}"]
            if j.get("missing"):
                lines.append("   Fill these:")
                lines += [f"     - {m}" for m in j["missing"]]
            lines += [f"   Open: {j['url']}", ""]
        lines.append("Sent by JobPilot AI.")
        sent = self.send(f"JobPilot: {len(needs_you)} application(s) need you", "\n".join(lines))
        log_event(db, level="info", agent=self.name,
                  message=f"Action-required digest {'sent' if sent else 'skipped'} ({len(needs_you)})")
