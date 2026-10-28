"""Shared agent utilities: stable IDs, event logging, status-strength guard."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..logging_conf import get_logger
from ..models import (APPLICATION_STATUS_RANK, ApplicationStatus, Event)


_INDIA_PLACES = (
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "navi mumbai",
    "chennai", "gurugram", "gurgaon", "noida", "new delhi", "delhi", "ncr",
    "kolkata", "ahmedabad", "jaipur", "kochi", "cochin", "thiruvananthapuram",
    "trivandrum", "coimbatore", "chandigarh", "indore", "bhubaneswar", "jamshedpur",
    "ranchi", "lucknow", "nagpur", "mysore", "mysuru", "vadodara", "surat",
    "visakhapatnam", "goa", "mohali",
)
_INDIA_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in _INDIA_PLACES) + r")\b", re.I)


def is_india_location(location: str) -> bool:
    """True if a job's LOCATION field (not its description) names India or an
    Indian city."""
    return bool(location and _INDIA_RE.search(location))


def make_job_id(source: str, source_id: str, canonical_url: str) -> str:
    """Deterministic, collision-resistant ID. Same job -> same id across runs.

    Priority key: source_id if present (requisition id), else canonical_url.
    This is the idempotency anchor for the DB and the Google Sheet.
    """
    key = f"{source.lower()}::{(source_id or canonical_url or '').strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def canonicalize_url(url: str) -> str:
    """Strip tracking params / fragments so the same posting dedups to one URL."""
    if not url:
        return ""
    url = url.strip()
    url = re.sub(r"#.*$", "", url)                 # drop fragment
    url = re.sub(r"[?&](utm_[^=]+|gh_src|src|ref)=[^&]*", "", url)  # drop trackers
    url = re.sub(r"[?&]+$", "", url)
    return url.rstrip("/")


# ---- cross-source dedup keys ---------------------------------------------- #
_COMPANY_SUFFIXES = ("privatelimited", "pvtltd", "limited", "ltd", "inc", "llc", "corp",
                     "corporation", "gmbh", "technologies", "technology", "software",
                     "labs", "india", "hq", "co")
_CITY_ALIASES = {"bangalore": "bengaluru", "gurgaon": "gurugram", "bombay": "mumbai",
                 "new delhi": "delhi", "madras": "chennai", "calcutta": "kolkata",
                 "cochin": "kochi", "trivandrum": "thiruvananthapuram", "mysore": "mysuru"}


def normalize_company(name: str) -> str:
    """'Razorpay Software Private Limited' / 'razorpaysoftwareprivatelimited' /
    'Razorpay' -> 'razorpay'."""
    s = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    changed = True
    while changed and s:
        changed = False
        for suf in _COMPANY_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf) + 2:
                s, changed = s[: -len(suf)], True
    return s


def normalize_title(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)                 # "(Remote)", "[India]"
    t = re.sub(r"\b(sr|jr)\b\.?", lambda m: {"sr": "senior", "jr": "junior"}[m.group(1)], t)
    t = re.sub(r"[^a-z0-9+#]+", " ", t)
    return " ".join(t.split())


def normalize_place(location: str) -> str:
    """First concrete place in a location string, aliases merged; 'remote' if
    the role is remote without a city."""
    loc = (location or "").lower()
    m = _INDIA_RE.search(loc)
    if m:
        city = m.group(1).lower()
        return _CITY_ALIASES.get(city, city)
    first = re.split(r"[;|,/(]", loc)[0].strip()
    first = re.sub(r"[^a-z ]", "", first).strip()
    return _CITY_ALIASES.get(first, first) or ("remote" if "remote" in loc else "")


def dedup_key(company: str, title: str, location: str) -> str:
    """Same company + same normalized title + same place = the same opening,
    whichever portal listed it."""
    c, t = normalize_company(company), normalize_title(title)
    if not c or not t:
        return ""
    return f"{c}|{t}|{normalize_place(location)}"


def log_event(db: Session, *, level: str, agent: str, message: str,
              job_id: str = "", data: dict | None = None) -> None:
    db.add(Event(level=level, agent=agent, message=message,
                 job_id=job_id, data=data or {}))
    db.commit()
    get_logger(agent).info(message, extra={"job_id": job_id, **(data or {})})


def stronger_status(current: str, incoming: str) -> str:
    """Return whichever application status is 'stronger' so we never downgrade a
    confirmed submission with a weaker/older value (Sheet-sync safety rule)."""
    try:
        cur = ApplicationStatus(current)
    except ValueError:
        cur = ApplicationStatus.NONE
    try:
        inc = ApplicationStatus(incoming)
    except ValueError:
        inc = ApplicationStatus.NONE
    return (inc if APPLICATION_STATUS_RANK[inc] >= APPLICATION_STATUS_RANK[cur]
            else cur).value


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
