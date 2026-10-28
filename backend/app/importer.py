"""Import one job by URL (dashboard → Sources → Import a job URL).

  * Company ATS link (Greenhouse, Lever, Ashby, Workable, SmartRecruiters,
    Recruitee, Personio, Breezy, Workday): fetched through that adapter, and
    the company is added to the registry so its board is polled from now on.
  * Any other page with schema.org JobPosting data: parsed from the page.
  * Portal links (LinkedIn, Naukri, Indeed, ...): not fetched — those sites
    don't permit automated access. You give the title/company (and ideally
    paste the description); the job then goes through verification, scoring
    and resume/cover-letter preparation, and you apply yourself (handoff).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import select

from .agents.base import canonicalize_url
from .config import get_settings
from .models import Company
from .providers import PROVIDERS, detect_ats, portal_for_url
from .providers.ats import parse_workday_detail, workday_base
from .providers.base import FetchContext, Http, Target
from .providers.careers import parse_jsonld_jobs
from .schemas import RawJob


class JobImportError(Exception):
    pass


def _same(a: str, b: str) -> bool:
    a, b = canonicalize_url(a).lower(), canonicalize_url(b).lower()
    return bool(a and b) and (a == b or a.startswith(b + "/") or b.startswith(a + "/"))


def find_in_board(jobs, url: str) -> Optional[RawJob]:
    for j in jobs:
        if _same(j.job_url, url) or _same(j.application_url, url):
            return j
    tail = urlparse(url).path.rstrip("/").split("/")
    for j in jobs:  # id somewhere in the path (Greenhouse /jobs/123, Lever /acct/uuid/apply)
        if j.source_id and any(seg == j.source_id or seg.startswith(j.source_id) for seg in tail):
            return j
    return None


def fetch_listing(url: str, http: Optional[Http] = None) -> tuple[Optional[RawJob], Optional[tuple]]:
    """(job or None, (provider, board) or None) for a URL we're allowed to fetch."""
    s = get_settings()
    own = http is None
    http = http or Http(timeout=s.provider_timeout)
    try:
        hit = detect_ats(url)
        if hit:
            provider, board = hit
            if provider == "workday":
                api, _, _ = workday_base(board)
                path = "/job/" + url.split("/job/", 1)[1] if "/job/" in url else ""
                if not path:
                    return None, hit
                return parse_workday_detail(board, path, http.get_json(f"{api}{path}")), hit
            ctx = FetchContext(http=http, max_pages=s.max_pages_per_provider, max_details=200)
            res = PROVIDERS[provider].fetch(Target(provider, board, filter_titles=False), ctx)
            return find_in_board(res.jobs, url), hit
        if portal_for_url(url):
            return None, None
        jobs = parse_jsonld_jobs(http.get_text(url), url)
        match = find_in_board(jobs, url) or (jobs[0] if len(jobs) == 1 else None)
        return match, None
    finally:
        if own:
            http.close()


def build_import(db, url: str, title: str = "", company: str = "", location: str = "",
                 description: str = "", http: Optional[Http] = None) -> tuple[RawJob, str]:
    """Returns (RawJob to ingest, how) where how is 'ats' | 'careers' | 'manual'.
    Raises JobImportError with a user-facing message when it can't."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise JobImportError("Paste a full job URL (https://...).")
    try:
        job, hit = fetch_listing(url, http)
    except Exception as e:
        job, hit = None, detect_ats(url)
        if not (title and company):
            raise JobImportError(f"Couldn't fetch that page ({type(e).__name__}). "
                               f"Add the title and company to import it manually.")
    if hit:
        provider, board = hit
        if not db.scalar(select(Company).where(Company.provider == provider, Company.board == board)):
            db.add(Company(provider=provider, board=board, name=company or board, watch=True,
                           discovered_by="import", careers_url=url))
            db.commit()
    if job:
        return job, "ats" if hit else "careers"
    if hit and not (title and company):
        raise JobImportError("That posting isn't on the company's board any more (closed?). "
                           "The company was added to your registry.")
    if not (title and company):
        portal = portal_for_url(url)
        raise JobImportError(
            (f"{portal.capitalize()} doesn't allow automated access, so " if portal else
             "No job data found on that page, so ") +
            "add the title and company (and paste the description for a better match).")
    portal = portal_for_url(url) or "manual"
    canon = canonicalize_url(url)
    return RawJob(source=portal, source_id=canon, company=company.strip(), title=title.strip(),
                  location=location.strip(), description=description.strip(),
                  job_url=canon, application_url=canon, canonical_url=canon), "manual"
