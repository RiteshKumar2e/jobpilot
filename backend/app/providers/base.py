"""Common provider interface for job discovery.

A *provider* knows one job source (an ATS such as Ashby, or a feed such as
Remotive). A *target* is one thing to fetch from it: a company board for an
ATS, a search query for a feed. The discovery agent runs every (provider,
target) pair concurrently and isolates failures, so one broken source never
stops the others.

Adding a source = subclass `Provider`, implement `fetch(target, ctx)`, register
it in `providers/__init__.py`, and add a parser test with a captured payload.
"""
from __future__ import annotations

import html
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..schemas import RawJob

# ---- capability vocabulary (shown in the dashboard's Sources page) --------- #
KIND_ATS = "ats"            # a company's own applicant-tracking board (complete list)
KIND_FEED = "feed"          # aggregator / job-board feed (search results)
KIND_CAREERS = "careers"    # a company careers page we parse (JSON-LD)
KIND_PORTAL = "portal"      # no permitted API: search links + manual handoff only

APPLY_AUTO = "auto"         # browser agent may submit (when on the allowlist)
APPLY_ASSIST = "assist"     # browser agent fills on your Approve, best-effort
APPLY_HANDOFF = "handoff"   # you apply; we prepare resume + cover letter + link


@dataclass
class Target:
    provider: str
    target: str                       # board token / query / URL
    label: str = ""                   # company display name if known
    since: Optional[datetime] = None  # last successful run (incremental fetch)
    filter_titles: bool = True        # drop clearly off-target titles before detail calls

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.target}"


@dataclass
class FetchResult:
    jobs: List[RawJob] = field(default_factory=list)
    complete: bool = False            # True = this is the board's FULL open list
    pages: int = 0
    discovered_boards: List[tuple] = field(default_factory=list)  # (provider, id, name)


@dataclass
class FetchContext:
    """What a provider gets besides its target: an HTTP client, limits, and
    a relevance filter built from your profile."""
    http: "Http"
    max_pages: int = 5
    max_details: int = 40
    title_ok: Callable[[str], bool] = lambda t: True
    queries: List[str] = field(default_factory=list)
    known: Callable[[str, str], bool] = lambda source, source_id: False  # already stored


class Provider:
    name: str = ""
    kind: str = KIND_ATS
    apply_mode: str = APPLY_HANDOFF
    needs_key: bool = False
    homepage: str = ""
    notes: str = ""

    def configured(self) -> bool:
        """False when required credentials are missing."""
        return True

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:  # pragma: no cover
        raise NotImplementedError

    def describe(self) -> dict:
        return {"name": self.name, "kind": self.kind, "apply_mode": self.apply_mode,
                "needs_key": self.needs_key, "configured": self.configured(),
                "homepage": self.homepage, "notes": self.notes}


# ---- HTTP with retries + per-host politeness ------------------------------- #
class Retryable(Exception):
    pass


class Http:
    """Thread-safe wrapper around one httpx.Client: retries 429/5xx with
    exponential backoff and keeps a minimum gap between calls to the same host."""

    def __init__(self, timeout: float = 20, min_interval: float = 0.4,
                 client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": "JobPilot/0.2 (personal job search assistant)"})
        self.min_interval = min_interval
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    def close(self):
        self.client.close()

    def _wait_turn(self, url: str):
        host = urlparse(url).netloc
        with self._lock:
            now = time.monotonic()
            nxt = max(now, self._last.get(host, 0) + self.min_interval)
            self._last[host] = nxt
        if nxt > now:
            time.sleep(nxt - now)

    @retry(reraise=True, stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=1, max=8),
           retry=retry_if_exception_type(Retryable))
    def request(self, method: str, url: str, **kw) -> httpx.Response:
        self._wait_turn(url)
        try:
            r = self.client.request(method, url, **kw)
        except httpx.TransportError as e:
            raise Retryable(str(e))
        if r.status_code in (429, 500, 502, 503, 504):
            raise Retryable(f"HTTP {r.status_code} from {urlparse(url).netloc}")
        r.raise_for_status()
        return r

    def get_json(self, url: str, **kw):
        return self.request("GET", url, **kw).json()

    def get_text(self, url: str, **kw) -> str:
        return self.request("GET", url, **kw).text

    def post_json(self, url: str, payload: dict, **kw):
        return self.request("POST", url, json=payload, **kw).json()


# ---- shared normalization helpers ----------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)          # feeds sometimes double-escape (&lt;p&gt;)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


_STRONG_REMOTE = ("fully remote", "100% remote", "remote-first", "remote first",
                  "work from anywhere", "work from home", "this is a remote",
                  "this role is remote", "this position is remote")


def infer_work_mode(text: str, location: str) -> str:
    """The location field is authoritative; the description only counts for
    unambiguous phrases (a passing 'remote-friendly team' mention doesn't)."""
    loc = (location or "").lower()
    if "remote" in loc or "anywhere" in loc:
        return "remote"
    if "hybrid" in loc:
        return "hybrid"
    desc = (text or "").lower()
    if any(p in desc for p in _STRONG_REMOTE):
        return "remote"
    if "hybrid" in desc:
        return "hybrid"
    if location:
        return "onsite"
    return ""


def iso_from_epoch(v) -> str:
    """Epoch seconds or milliseconds -> ISO string ('' if unusable)."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v or "")
    if n > 1e12:
        n /= 1000
    try:
        return datetime.fromtimestamp(n, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def parse_date(v: str) -> Optional[datetime]:
    """Best-effort ISO / epoch parse to an aware UTC datetime."""
    if not v:
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{10,13}(\.\d+)?", s):
        s = iso_from_epoch(s)
    s = s.replace("Z", "+00:00").replace(" UTC", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+00:00", s):
        s = s.replace(" ", "T", 1)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def join_locations(parts: Iterable[str]) -> str:
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip().strip(",").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return "; ".join(out)
