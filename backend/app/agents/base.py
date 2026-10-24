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
