"""Answer Bank — the standing answers that let applications go through by themselves.

  * `coverage()`   the questions job forms ask again and again (from the setup
                   guide's list) and whether your profile already answers each.
  * `pending()`    questions that stopped real applications, grouped, with the
                   jobs waiting on them. Answer once and every one of those
                   jobs retries automatically (the profile file changed).
  * `add_answer()` appends `{match, answer}` to candidate_profile.yaml ->
                   application_answers, keeping the file's comments intact.

Answers are only ever what you typed — nothing here guesses.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

import yaml
from sqlalchemy import select

from .agents.browser_apply import BrowserApplyAgent
from .config import get_settings
from .models import ApplicationAttempt, ApplicationStatus, Job

# (topic, how forms usually ask it) — the guide's Answer Bank list, fresher-friendly
COMMON_QUESTIONS = [
    ("Total experience", "How many years of experience do you have?"),
    ("Current CTC", "What is your current CTC?"),
    ("Expected CTC", "What is your expected CTC?"),
    ("Notice period", "What is your notice period?"),
    ("Earliest joining date", "When can you start?"),
    ("Current city", "Current location / city"),
    ("Relocation", "Are you willing to relocate?"),
    ("Work from office", "Are you comfortable working from the office 5 days a week?"),
    ("Work authorisation", "Are you legally authorized to work in this country?"),
    ("Sponsorship", "Will you now or in the future require visa sponsorship?"),
    ("Highest education", "Degree"),
    ("Graduation year", "Year of graduation"),
    ("How you heard", "How did you hear about this job?"),
    ("Worked here before", "Have you previously worked for this company?"),
    ("Why this role", "Why are you interested in this role?"),
    ("Diversity: gender", "Gender"),
    ("Diversity: disability", "Do you have a disability?"),
    ("LinkedIn / GitHub / portfolio", "LinkedIn Profile"),
]

_IGNORE = {"unnamed field", "", "security code"}


def _rules(profile):
    return BrowserApplyAgent(profile).answer_rules(None)


def answer_for(rules, question: str) -> str:
    return next((v for rx, v in rules if rx.search(question)), "")


def coverage(profile) -> List[dict]:
    rules = _rules(profile)
    return [{"topic": t, "question": q, "answer": answer_for(rules, q)} for t, q in COMMON_QUESTIONS]


def pending(db, profile) -> List[dict]:
    """Unanswered required questions from each job's latest attempt."""
    rules = _rules(profile)
    latest: dict = {}
    for a in db.scalars(select(ApplicationAttempt).order_by(ApplicationAttempt.started_at)):
        latest[a.job_id] = a
    groups: dict = {}
    for jid, a in latest.items():
        if a.result != ApplicationStatus.ACTION_REQUIRED.value:
            continue
        for q in (a.detail or {}).get("missing") or []:
            q = (q or "").strip()
            if q.lower() in _IGNORE or len(q) < 3 or answer_for(rules, q):
                continue
            g = groups.setdefault(q.lower(), {"question": q, "count": 0, "jobs": [],
                                             "last_seen": ""})
            job = db.scalar(select(Job).where(Job.job_id == jid))
            g["count"] += 1
            g["last_seen"] = max(g["last_seen"], str(a.finished_at or a.started_at))
            if job:
                g["jobs"].append({"job_id": jid, "title": job.title, "company": job.company,
                                  "url": job.application_url or job.job_url})
    return sorted(groups.values(), key=lambda g: (-g["count"], g["question"]))


def pattern_for(question: str) -> str:
    """A regex that matches this question (and the same wording elsewhere)."""
    q = re.sub(r"\s+", " ", (question or "").strip().lower()).rstrip("?*:. ")
    q = q[:80]
    if not q:
        raise ValueError("Empty question")
    return re.escape(q).replace(r"\ ", r"\s+")


def add_answer(question: str, answer: str, pattern: Optional[str] = None,
               path: Optional[Path] = None) -> dict:
    answer = (answer or "").strip()
    if not answer:
        raise ValueError("Answer is empty.")
    pat = (pattern or "").strip() or pattern_for(question)
    try:
        re.compile(pat, re.I)
    except re.error as e:
        raise ValueError(f"Not a valid pattern: {e}")
    path = Path(path or get_settings().profile_path)
    original = path.read_text(encoding="utf-8")
    line = f"  - {{match: {json.dumps(pat)}, answer: {json.dumps(answer)}}}"
    lines = original.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("application_answers:"))
    except StopIteration:
        new_text = original.rstrip("\n") + f"\n\napplication_answers:\n{line}\n"
    else:
        # insert after the block's last list item (comments inside it stay put)
        last = start
        for i in range(start + 1, len(lines)):
            l = lines[i]
            if l and not l.startswith((" ", "#")):
                break
            if l.lstrip().startswith("- "):
                last = i
        lines.insert(last + 1, line)
        new_text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
    # never leave a broken profile behind
    try:
        data = yaml.safe_load(new_text) or {}
        ok = any(a.get("match") == pat and str(a.get("answer")) == answer
                 for a in data.get("application_answers") or [])
    except yaml.YAMLError:
        ok = False
    if not ok:
        raise ValueError("Couldn't add that answer safely; the profile was left unchanged.")
    path.write_text(new_text, encoding="utf-8")
    return {"match": pat, "answer": answer}
