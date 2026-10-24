"""Agent 1 — Job Discovery.

Uses the OFFICIAL public job-board APIs (no scraping, no ToS violation):
  - Greenhouse Job Board API: https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
  - Lever Postings API:       https://api.lever.co/v0/postings/{account}?mode=json

You list the company board tokens / lever accounts you care about in
candidate_profile.yaml -> sources. The agent fetches, normalizes, and
deduplicates. It NEVER asserts a listing is open without the fetch succeeding
for that listing right now.

The parsing is unit-tested here against captured API shapes; the live HTTP call
runs on your machine (this sandbox blocks outbound web hosts).
"""
from __future__ import annotations

import re
from typing import List

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..config import get_settings
from ..logging_conf import get_logger
from ..schemas import RawJob
from .base import canonicalize_url

log = get_logger("discovery")

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{account}?mode=json"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&#39;", "'").replace("&quot;", '"').replace("&nbsp;", " "))
    return _WS_RE.sub(" ", text).strip()


_STRONG_REMOTE = ("fully remote", "100% remote", "remote-first", "remote first",
                  "work from anywhere", "work from home", "this is a remote",
                  "this role is remote", "this position is remote")


def _infer_work_mode(text: str, location: str) -> str:
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


# ---- Pure parsers (unit-tested) ------------------------------------------- #
def parse_greenhouse(board_token: str, payload: dict) -> List[RawJob]:
    out: List[RawJob] = []
    for j in payload.get("jobs", []):
        content = _strip_html(j.get("content", ""))
        location = (j.get("location") or {}).get("name", "")
        url = canonicalize_url(j.get("absolute_url", ""))
        out.append(RawJob(
            source="greenhouse",
            source_id=str(j.get("id", "")),
            company=board_token,  # board token == company slug on Greenhouse
            title=j.get("title", "").strip(),
            description=content,
            location=location,
            work_mode=_infer_work_mode(content, location),
            employment_type="",
            experience_req="",
            salary_range="",
            posted_at=j.get("updated_at", "") or j.get("first_published", ""),
            job_url=url,
            application_url=url,  # greenhouse posting == application page
            canonical_url=url,
        ))
    return out


def parse_lever(account: str, payload: list) -> List[RawJob]:
    out: List[RawJob] = []
    for j in payload:
        cats = j.get("categories", {}) or {}
        desc = _strip_html(j.get("descriptionPlain") or j.get("description", ""))
        all_locs = [l for l in (cats.get("allLocations") or []) if l]
        location = "; ".join(all_locs) if len(all_locs) > 1 else cats.get("location", "")
        # Lever's own workplaceType (remote|hybrid|onsite|unspecified) beats inference.
        wpt = (j.get("workplaceType") or "").lower()
        work_mode = wpt if wpt in ("remote", "hybrid", "onsite") else _infer_work_mode(desc, location)
        url = canonicalize_url(j.get("hostedUrl", "") or j.get("applyUrl", ""))
        out.append(RawJob(
            source="lever",
            source_id=str(j.get("id", "")),
            company=account,
            title=(j.get("text") or "").strip(),
            description=desc,
            location=location,
            work_mode=work_mode,
            employment_type=cats.get("commitment", ""),
            experience_req="",
            salary_range=(j.get("salaryRange") or {}).get("currency", "") and
                         _lever_salary(j) or "",
            posted_at=str(j.get("createdAt", "")),
            job_url=url,
            application_url=canonicalize_url(j.get("applyUrl", "") or url),
            canonical_url=url,
        ))
    return out


def _lever_salary(j: dict) -> str:
    sr = j.get("salaryRange") or {}
    if not sr:
        return ""
    mn, mx, cur = sr.get("min"), sr.get("max"), sr.get("currency", "")
    if mn and mx:
        return f"{cur} {mn:,}–{mx:,}".strip()
    return ""


# ---- Live fetchers (run on user's machine; sandbox blocks these hosts) ---- #
class _Retryable(Exception):
    pass


@retry(reraise=True, stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=1, min=1, max=8),
       retry=retry_if_exception_type(_Retryable))
def _get_json(client: httpx.Client, url: str):
    try:
        r = client.get(url)
    except httpx.TransportError as e:
        raise _Retryable(str(e))
    if r.status_code in (429, 500, 502, 503, 504):
        raise _Retryable(f"HTTP {r.status_code}")
    r.raise_for_status()
    return r.json()


class DiscoveryAgent:
    name = "discovery"

    def __init__(self, profile):
        self.profile = profile
        self.settings = get_settings()

    def discover(self) -> List[RawJob]:
        jobs: List[RawJob] = []
        src = self.profile.sources
        timeout = self.settings.http_timeout
        headers = {"User-Agent": "JobPilot/0.1 (personal job search; contact via config)"}
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            for token in src["greenhouse_boards"]:
                try:
                    data = _get_json(client, GREENHOUSE_URL.format(token=token))
                    parsed = parse_greenhouse(token, data)
                    jobs.extend(parsed)
                    log.info("greenhouse fetched", extra={"board": token, "count": len(parsed)})
                except Exception as e:
                    log.warning(f"greenhouse fetch failed for {token}: {e}")
            for account in src["lever_accounts"]:
                try:
                    data = _get_json(client, LEVER_URL.format(account=account))
                    parsed = parse_lever(account, data)
                    jobs.extend(parsed)
                    log.info("lever fetched", extra={"account": account, "count": len(parsed)})
                except Exception as e:
                    log.warning(f"lever fetch failed for {account}: {e}")
        return jobs
