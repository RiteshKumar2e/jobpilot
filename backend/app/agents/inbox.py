"""Agent 10 — Inbox (recruiter replies → tracker, drafts, calendar).

Reads your Gmail over IMAP, READ-ONLY (the mailbox is opened with EXAMINE and
messages fetched with BODY.PEEK, so nothing is marked read, moved or deleted).
Uses the same App Password as notification emails.

For each new job-related email it:
  * classifies it: interview invite, assessment, rejection, offer, application
    received, security/verification code, or a recruiter reply;
  * matches it to the job you applied to (company name in sender/subject/body);
  * updates the job's outcome (never backwards) — and an "application received"
    email upgrades SUBMISSION_UNVERIFIED to SUBMITTED_CONFIRMED (real evidence);
  * drafts a reply (offer slots / thank-you) and an "Add to Google Calendar"
    link. It NEVER sends a reply — you read, edit and send it yourself.

Unrelated email is skipped and never stored; stored messages keep a short snippet.
"""
from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import List, Optional, Tuple
from urllib.parse import quote

from sqlalchemy import select

from ..config import get_settings
from ..logging_conf import get_logger
from ..models import (ApplicationStatus, InboxMessage, Job, OUTCOME_RANK, Outcome,
                      PipelineStage)
from .base import log_event, normalize_company

log = get_logger("inbox")

IST = timezone(timedelta(hours=5, minutes=30))

# category -> phrases (checked in this order; first hit wins)
_RULES: List[Tuple[str, re.Pattern]] = [
    ("verification_code", re.compile(
        r"security code|verification code|confirm your email|enter (?:the|this) code", re.I)),
    ("offer", re.compile(r"offer letter|pleased to (?:extend|offer)|we(?:'d| would) like to offer you", re.I)),
    # Only definite no's: confirmation emails often say "IF you are not selected …".
    ("rejection", re.compile(
        r"unfortunately|not (?:be )?moving forward|decided to (?:move forward|proceed) with other"
        r"|regret to inform|not (?:be )?progressing|will not be proceeding|position has been filled"
        r"|(?:you|we) (?:were|have) not (?:been )?selected|(?:have )?decided not to (?:move|proceed|progress)", re.I)),
    ("assessment", re.compile(
        r"assessment|coding (?:challenge|test|round)|take[- ]home|online test|hackerrank\.com"
        r"|codesignal|hackerearth|mettl|codility|assignment", re.I)),
    ("interview", re.compile(
        r"interview|phone screen|schedule (?:a|some) time|your availability|book a (?:time|slot)"
        r"|calendly\.com|let(?:'s| us) (?:connect|chat)|next round|speak with you", re.I)),
    ("application_received", re.compile(
        r"thank(?:s| you) for (?:applying|your application|your interest)|application (?:received|submitted)"
        r"|we(?:'ve| have) received your application|received your application", re.I)),
]
_JOBBY = re.compile(r"applica|candida|recruit|hiring|interview|position|role|opportunit|talent", re.I)
_ATS_SENDERS = re.compile(r"greenhouse|lever\.co|ashbyhq|smartrecruiters|myworkday|workable"
                          r"|recruitee|personio|breezy|hire\.|talent|careers|jobs|recruit", re.I)
_ALERT_SENDERS = re.compile(r"jobalert|job-alert|jobs-noreply@linkedin|alert@|alerts@|newsletter"
                            r"|digest|marketing|promo|noreply@medium|substack"
                            # job boards' own mail is alerts/tips, not replies from employers
                            r"|glassdoor|flexjobs|internshala|unstop|foundit|monster|timesjobs", re.I)
# Senders that email on behalf of many companies: their name says nothing about
# which company an email is from (a HackerRank test is rarely for a job AT HackerRank).
_PLATFORMS = {"hackerrank", "hackerrankmail", "greenhouse", "lever", "workday", "myworkday",
              "smartrecruiters", "ashby", "ashbyhq", "workable", "codesignal", "hackerearth",
              "mettl", "codility", "testgorilla", "linkedin", "indeed", "naukri"}
_LINK_RE = re.compile(r"https?://[^\s<>\"')]+")
_BOOKING = re.compile(r"calendly\.com|cal\.com|goodtime|greenhouse\.io/.*schedul|zoom\.us|meet\.google"
                      r"|hackerrank|codesignal|hackerearth|mettl|codility|testgorilla", re.I)

OUTCOME_FOR = {"interview": Outcome.INTERVIEW_INVITE.value, "assessment": Outcome.ASSESSMENT.value,
               "rejection": Outcome.REJECTED_BY_COMPANY.value, "offer": Outcome.OFFER.value,
               "recruiter_reply": Outcome.RECRUITER_REPLY.value}


def _dec(v) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def _body_text(msg) -> str:
    parts = []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html") or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            raw = part.get_payload(decode=True) or b""
            txt = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/html":
            txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", txt, flags=re.S | re.I)
            txt = re.sub(r"<[^>]+>", " ", txt)
        parts.append(txt)
        if ctype == "text/plain":
            break  # prefer plain text when present
    import html
    return re.sub(r"\s+", " ", html.unescape(" ".join(parts))).strip()


def classify(subject: str, body: str, sender: str) -> str:
    text = f"{subject}\n{body[:4000]}"
    for cat, rx in _RULES:
        if rx.search(text):
            return cat
    if _JOBBY.search(subject) or _ATS_SENDERS.search(sender):
        return "recruiter_reply"
    return ""


def match_job(jobs: List[Job], subject: str, body: str, sender: str) -> Optional[Job]:
    """The applied job this email is about: company name in sender/subject/body;
    the job title breaks ties; most recent application last."""
    hay = normalize_company(f"{sender} {subject} {body[:3000]}")
    subj = subject.lower()
    sender_n = normalize_company(sender)
    text = f"{subj} {body[:3000].lower()}"
    best, best_score = None, 0
    for j in jobs:
        c = normalize_company(j.company)
        if len(c) < 3 or c not in hay:
            continue
        titled = j.title.lower()[:30] in text
        if c in _PLATFORMS and c in sender_n and not titled:
            continue  # e.g. a HackerRank test for another company ≠ the HackerRank job
        score = 2 + (3 if titled else 0)
        if score > best_score or (score == best_score and best and
                                  (j.submission_date or "") > (best.submission_date or "")):
            best, best_score = j, score
    return best


def next_slots(now: Optional[datetime] = None, n: int = 3) -> List[datetime]:
    """n weekday slots (11:00 / 15:00 IST) over the next working days — a
    starting point for your reply; edit to your real availability."""
    now = (now or datetime.now(IST)).astimezone(IST)
    out, day = [], now.date() + timedelta(days=1)
    hours = [11, 15]
    while len(out) < n:
        if day.weekday() < 5:
            for h in hours:
                if len(out) < n:
                    out.append(datetime(day.year, day.month, day.day, h, 0, tzinfo=IST))
        day += timedelta(days=1)
    return out


def calendar_link(title: str, start: datetime, minutes: int, details: str = "") -> str:
    """Google Calendar 'add event' link (no Calendar permission needed)."""
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end = start + timedelta(minutes=minutes)
    return ("https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={quote(title)}&dates={fmt(start)}/{fmt(end)}&details={quote(details[:900])}")


def draft_for(category: str, name: str, sender_name: str, job: Optional[Job]) -> str:
    who = sender_name.split()[0] if sender_name else ""
    hi = f"Hi {who}," if who else "Hello,"
    role = f"the {job.title} role" if job else "the role"
    if category == "interview":
        slots = "\n".join(f"  - {d.strftime('%a %d %b, %I:%M %p')} IST" for d in next_slots())
        return (f"{hi}\n\nThank you for reaching out about {role}. I'd be glad to speak. "
                f"I'm available at any of these times:\n{slots}\n\nIf none of these work, "
                f"I'm happy to fit around your schedule.\n\nBest regards,\n{name}")
    if category == "rejection":
        return (f"{hi}\n\nThank you for letting me know, and for considering me for {role}. "
                f"I'd be glad to be considered for future openings.\n\nBest regards,\n{name}")
    if category == "assessment":
        return (f"{hi}\n\nThank you — I've received the assessment for {role} and will "
                f"complete it before the deadline.\n\nBest regards,\n{name}")
    if category == "offer":
        return (f"{hi}\n\nThank you very much for the offer for {role}. I'm reviewing the "
                f"details and will get back to you shortly.\n\nBest regards,\n{name}")
    return ""


class InboxAgent:
    name = "inbox"

    def __init__(self, profile):
        self.profile = profile
        self.settings = get_settings()

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(s.inbox_enabled and s.smtp_user and s.smtp_password)

    def fetch(self, since_days: int, keep=None) -> List[dict]:
        """Read-only fetch of recent messages: [{message_id, date, sender, subject, body}].

        Headers for all recent mail come in ONE request; only messages that
        `keep(sender, subject, message_id)` accepts are downloaded, and only
        their first 80 KB (no attachments)."""
        s = self.settings
        out = []
        M = imaplib.IMAP4_SSL(s.imap_host, timeout=30)
        try:
            M.login(s.smtp_user, s.smtp_password)
            M.select("INBOX", readonly=True)            # EXAMINE: flags never change
            since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
            typ, data = M.search(None, "SINCE", since)
            ids = (data[0] or b"").split()[-s.inbox_max_messages:]
            if not ids:
                return []
            typ, heads = M.fetch(b",".join(ids),
                                 "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
            wanted = []
            for part in heads:
                if not isinstance(part, tuple):
                    continue
                num = part[0].split()[0]
                h = email.message_from_bytes(part[1])
                sender, subject = _dec(h.get("From")), _dec(h.get("Subject"))
                mid = (h.get("Message-ID") or f"imap-{num.decode()}").strip()
                if keep is None or keep(sender, subject, mid):
                    wanted.append((num, mid, sender, subject, h.get("Date")))
            for num, mid, sender, subject, date_h in wanted:
                typ, msg_data = M.fetch(num, "(BODY.PEEK[]<0.81920>)")
                raw = next((p[1] for p in msg_data if isinstance(p, tuple)), None)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                try:
                    date = parsedate_to_datetime(date_h).astimezone(timezone.utc)
                except Exception:
                    date = None
                out.append({"message_id": mid, "date": date, "sender": sender,
                            "subject": subject, "body": _body_text(msg)})
        finally:
            try:
                M.logout()
            except Exception:
                pass
        return out

    def process(self, db, messages: List[dict]) -> dict:
        """Classify + match + record. Pure apart from the DB, so it's unit-tested."""
        stats = {"read": len(messages), "job_related": 0, "matched": 0, "confirmed": 0,
                 "new": []}
        seen = set(db.scalars(select(InboxMessage.message_id)).all())
        applied = db.scalars(select(Job).where(Job.application_status.in_([
            ApplicationStatus.SUBMITTED_CONFIRMED.value, ApplicationStatus.SUBMISSION_UNVERIFIED.value,
            ApplicationStatus.ACTION_REQUIRED.value, ApplicationStatus.APPROVED.value,
            ApplicationStatus.READY_FOR_REVIEW.value, ApplicationStatus.DRAFTED.value,
            ApplicationStatus.FAILED.value]))).all()
        name = self.profile.name
        me = (self.settings.smtp_user or "").lower()
        for m in messages:
            if m["message_id"] in seen:
                continue
            sender_name, sender_addr = parseaddr(m["sender"])
            if sender_addr.lower() == me:
                continue  # our own notifications
            if _ALERT_SENDERS.search(m["sender"]):
                continue  # job alerts / newsletters aren't replies
            cat = classify(m["subject"], m["body"], m["sender"])
            job = match_job(applied, m["subject"], m["body"], m["sender"])
            if not cat or not (job or (cat != "recruiter_reply" and _ATS_SENDERS.search(m["sender"]))):
                continue  # not about a job you applied to — never stored
            stats["job_related"] += 1
            links = [l for l in _LINK_RE.findall(m["body"]) if _BOOKING.search(l)][:5]
            cal = ""
            if job:
                stats["matched"] += 1
                cal = self._apply_to_job(db, job, cat, sender_name or sender_addr, stats)
            if not cal and cat == "assessment":
                # deadline is in the email — this adds a reminder you can move to it
                cal = calendar_link(f"Assessment: {job.company if job else m['subject'][:60]}",
                                    next_slots(n=1)[0], 60, m["subject"])
            rec = InboxMessage(message_id=m["message_id"][:400], received_at=(m["date"] or datetime.now(timezone.utc)).replace(tzinfo=None),
                               sender=m["sender"][:300], subject=m["subject"][:500],
                               snippet=m["body"][:400], category=cat,
                               job_id=job.job_id if job else "", links=links,
                               draft_reply=draft_for(cat, name, sender_name, job),
                               calendar_url=cal)
            db.add(rec)
            seen.add(m["message_id"])
            stats["new"].append({"category": cat, "subject": m["subject"][:120],
                                 "company": job.company if job else "", "title": job.title if job else ""})
        db.commit()
        return stats

    def _apply_to_job(self, db, job: Job, cat: str, sender: str, stats: dict) -> str:
        cal = ""
        if cat == "application_received" and \
                job.application_status != ApplicationStatus.SUBMITTED_CONFIRMED.value:
            # The company's own confirmation is real evidence an application exists —
            # whether we submitted it or you did after a handoff. Marking it confirmed
            # and DONE is what stops JobPilot from ever applying to it a second time.
            was = job.application_status
            job.application_status = ApplicationStatus.SUBMITTED_CONFIRMED.value
            job.confirmation_reference = job.confirmation_reference or "Confirmation email received"
            job.submission_date = job.submission_date or datetime.now(timezone.utc).isoformat()
            job.pipeline_stage = PipelineStage.DONE.value
            if was != ApplicationStatus.SUBMISSION_UNVERIFIED.value:
                job.next_action = "Applied (confirmation email received)."
            stats["confirmed"] += 1
        new = OUTCOME_FOR.get(cat)
        if new and OUTCOME_RANK.get(new, 0) > OUTCOME_RANK.get(job.outcome or "", 0):
            job.outcome = new
            job.outcome_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if cat in ("interview", "assessment", "offer", "recruiter_reply") and sender and not job.recruiter:
            job.recruiter = sender[:200]
        if cat == "interview":
            job.next_action = "Interview invite — reply with your slots (draft in Inbox)."
            cal = calendar_link(f"Prep: {job.company} — {job.title}", next_slots(n=1)[0], 30,
                                job.job_url)
        elif cat == "assessment":
            job.next_action = "Assessment received — note the deadline (Inbox)."
        elif cat == "verification_code":
            job.next_action = "Enter the emailed security code to finish this application."
        elif cat == "rejection":
            job.next_action = "Rejected by the company."
        log_event(db, level="info", agent=self.name, job_id=job.job_id,
                  message=f"Inbox: {cat.replace('_', ' ')} — {job.title} @ {job.company}")
        return cal

    def prefilter(self, db):
        """Cheap header check deciding which emails are worth downloading: not
        seen yet, not an alert/newsletter, and either from a recruiting/ATS
        sender, job-sounding, or naming a company you applied to."""
        seen = set(db.scalars(select(InboxMessage.message_id)).all())
        companies = {normalize_company(c) for (c,) in db.execute(select(Job.company).where(
            Job.application_status != ApplicationStatus.NONE.value))}
        companies = {c for c in companies if len(c) >= 3}
        me = (self.settings.smtp_user or "").lower()

        def keep(sender: str, subject: str, mid: str) -> bool:
            if mid in seen or me and me in sender.lower() or _ALERT_SENDERS.search(sender):
                return False
            if _ATS_SENDERS.search(sender) or _JOBBY.search(subject) or \
                    any(rx.search(subject) for _, rx in _RULES):
                return True
            hay = normalize_company(f"{sender} {subject}")
            return any(c in hay for c in companies)
        return keep

    def run(self, db) -> dict:
        if not self.enabled:
            return {"skipped": "inbox disabled or no Gmail App Password"}
        try:
            msgs = self.fetch(self.settings.inbox_lookback_days, self.prefilter(db))
        except Exception as e:
            log.warning(f"inbox read failed: {e}")
            return {"error": str(e)[:200]}
        stats = self.process(db, msgs)
        log_event(db, level="info", agent=self.name,
                  message=f"Inbox checked: {stats['job_related']} job email(s), "
                          f"{stats['matched']} matched, {stats['confirmed']} confirmations",
                  data={k: v for k, v in stats.items() if k != "new"})
        return stats
