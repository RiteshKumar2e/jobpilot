"""Job-board feeds and aggregators with public APIs.

  remotive         remotive.com/api/remote-jobs?search=   (public; attribution — we keep the source link)
  remoteok         remoteok.com/api                       (public; link back to Remote OK)
  arbeitnow        arbeitnow.com/api/job-board-api        (public; mostly Europe)
  himalayas        himalayas.app/jobs/api/search          (public; India-eligible remote filter)
  weworkremotely   weworkremotely.com/categories/*.rss    (public RSS)
  adzuna           api.adzuna.com/v1/api/jobs/in/search   (free key: ADZUNA_APP_ID / ADZUNA_APP_KEY)

Feeds return search results, not a company's full list, so they never mark a
listing closed. They're filtered by title relevance before anything is stored.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import List
from urllib.parse import quote_plus

from ..agents.base import canonicalize_url
from ..schemas import RawJob
from .base import (APPLY_HANDOFF, KIND_FEED, FetchContext, FetchResult, Provider, Target,
                   infer_work_mode, iso_from_epoch, parse_date, strip_html)


def _remote_location(restriction: str) -> str:
    r = (restriction or "").strip()
    if not r or r.lower() in ("worldwide", "anywhere", "anywhere in the world", "global"):
        return "Remote"
    return f"Remote - {r}"


def _keep(job: RawJob, ctx: FetchContext, target: Target) -> bool:
    return bool(job.title) and (not target.filter_titles or ctx.title_ok(job.title))


# ---- Remotive ------------------------------------------------------------- #
def parse_remotive(payload: dict) -> List[RawJob]:
    out = []
    for j in payload.get("jobs", []):
        url = canonicalize_url(j.get("url", ""))
        out.append(RawJob(
            source="remotive", source_id=str(j.get("id", "")), company=j.get("company_name", ""),
            title=(j.get("title") or "").strip(), description=strip_html(j.get("description", "")),
            location=_remote_location(j.get("candidate_required_location", "")), work_mode="remote",
            employment_type=(j.get("job_type") or "").replace("_", " "),
            salary_range=j.get("salary") or "", posted_at=j.get("publication_date", ""),
            job_url=url, application_url=url, canonical_url=url,
        ))
    return out


class RemotiveProvider(Provider):
    name, kind, apply_mode = "remotive", KIND_FEED, APPLY_HANDOFF
    homepage = "https://remotive.com/api-documentation"
    notes = "Public API, remote jobs. Keep the Remotive link (their terms ask for attribution)."
    URL = "https://remotive.com/api/remote-jobs?search={q}&limit=100"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs = parse_remotive(ctx.http.get_json(self.URL.format(q=quote_plus(target.target))))
        return FetchResult(jobs=[j for j in jobs if _keep(j, ctx, target)], pages=1)


# ---- Remote OK ------------------------------------------------------------ #
def parse_remoteok(payload: list) -> List[RawJob]:
    out = []
    for j in payload or []:
        if not isinstance(j, dict) or "position" not in j:
            continue  # first element is the legal notice
        url = canonicalize_url(j.get("url", ""))
        mn, mx = j.get("salary_min") or 0, j.get("salary_max") or 0
        out.append(RawJob(
            source="remoteok", source_id=str(j.get("id", "")), company=j.get("company", ""),
            title=(j.get("position") or "").strip(), description=strip_html(j.get("description", "")),
            location=_remote_location(j.get("location", "")), work_mode="remote",
            salary_range=f"USD {mn:,}–{mx:,}" if mn and mx else "",
            posted_at=j.get("date", ""), job_url=url,
            application_url=canonicalize_url(j.get("apply_url") or url), canonical_url=url,
        ))
    return out


class RemoteOKProvider(Provider):
    name, kind, apply_mode = "remoteok", KIND_FEED, APPLY_HANDOFF
    homepage = "https://remoteok.com/api"
    notes = "Public API (latest ~100 jobs). Terms: link back to Remote OK — we keep its URL."
    URL = "https://remoteok.com/api"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs = parse_remoteok(ctx.http.get_json(self.URL))
        return FetchResult(jobs=[j for j in jobs if _keep(j, ctx, target)], pages=1)


# ---- Arbeitnow ------------------------------------------------------------ #
def parse_arbeitnow(payload: dict) -> List[RawJob]:
    out = []
    for j in payload.get("data", []):
        remote = bool(j.get("remote"))
        loc = j.get("location", "")
        url = canonicalize_url(j.get("url", ""))
        out.append(RawJob(
            source="arbeitnow", source_id=str(j.get("slug", "")), company=j.get("company_name", ""),
            title=(j.get("title") or "").strip(), description=strip_html(j.get("description", "")),
            location=(f"Remote; {loc}" if remote and loc else "Remote" if remote else loc),
            work_mode="remote" if remote else infer_work_mode("", loc),
            employment_type=", ".join(j.get("job_types") or []),
            posted_at=iso_from_epoch(j.get("created_at")), job_url=url, application_url=url,
            canonical_url=url,
        ))
    return out


class ArbeitnowProvider(Provider):
    name, kind, apply_mode = "arbeitnow", KIND_FEED, APPLY_HANDOFF
    homepage = "https://www.arbeitnow.com/blog/job-board-api"
    notes = "Free public API (mostly Europe; remote roles kept). Paged, newest first."
    URL = "https://www.arbeitnow.com/api/job-board-api?page={page}"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs, pages = [], 0
        for page in range(1, ctx.max_pages + 1):
            batch = parse_arbeitnow(ctx.http.get_json(self.URL.format(page=page)))
            pages += 1
            jobs.extend(batch)
            # incremental: stop once a page is entirely older than the last good run
            if not batch or (target.since and all(
                    (parse_date(j.posted_at) or target.since) < target.since for j in batch)):
                break
        return FetchResult(jobs=[j for j in jobs if _keep(j, ctx, target)], pages=pages)


# ---- Himalayas ------------------------------------------------------------ #
def parse_himalayas(payload: dict) -> List[RawJob]:
    out = []
    for j in payload.get("jobs", []):
        restr = j.get("locationRestrictions") or []
        url = canonicalize_url(j.get("guid") or j.get("applicationLink") or "")
        mn, mx, cur = j.get("minSalary"), j.get("maxSalary"), j.get("currency") or ""
        out.append(RawJob(
            source="himalayas", source_id=url, company=j.get("companyName", ""),
            title=(j.get("title") or "").strip(),
            description=strip_html(j.get("description") or j.get("excerpt") or ""),
            location=_remote_location(", ".join(restr)), work_mode="remote",
            employment_type=j.get("employmentType", ""),
            experience_req=", ".join(j.get("seniority") or []),
            salary_range=f"{cur} {mn:,}–{mx:,}".strip() if mn and mx else "",
            posted_at=iso_from_epoch(j.get("pubDate")), job_url=url,
            application_url=canonicalize_url(j.get("applicationLink") or url), canonical_url=url,
        ))
    return out


class HimalayasProvider(Provider):
    name, kind, apply_mode = "himalayas", KIND_FEED, APPLY_HANDOFF
    homepage = "https://himalayas.app/api"
    notes = "Public API; searched with country=India so results accept India-based applicants."
    URL = "https://himalayas.app/jobs/api/search?q={q}&country=India&page={page}"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs, pages = [], 0
        for page in range(1, ctx.max_pages + 1):
            data = ctx.http.get_json(self.URL.format(q=quote_plus(target.target), page=page))
            pages += 1
            batch = parse_himalayas(data)
            jobs.extend(batch)
            if len(batch) < 20 or len(jobs) >= (data.get("totalCount") or 0):
                break
        return FetchResult(jobs=[j for j in jobs if _keep(j, ctx, target)], pages=pages)


# ---- We Work Remotely ------------------------------------------------------ #
def parse_wwr(xml_text: str) -> List[RawJob]:
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    out = []
    for it in root.iter("item"):
        g = lambda tag: (it.findtext(tag) or "").strip()
        company, _, title = g("title").partition(": ")
        if not title:
            company, title = "", g("title")
        try:
            posted = parsedate_to_datetime(g("pubDate")).isoformat()
        except (TypeError, ValueError):
            posted = ""
        url = canonicalize_url(g("link") or g("guid"))
        out.append(RawJob(
            source="weworkremotely", source_id=url, company=company, title=title.strip(),
            description=strip_html(g("description")), location=_remote_location(g("region")),
            work_mode="remote", employment_type=g("type"), posted_at=posted,
            job_url=url, application_url=url, canonical_url=url,
        ))
    return out


class WeWorkRemotelyProvider(Provider):
    name, kind, apply_mode = "weworkremotely", KIND_FEED, APPLY_HANDOFF
    homepage = "https://weworkremotely.com/remote-job-rss-feed"
    notes = "Public RSS per category. Target = category slug."
    URL = "https://weworkremotely.com/categories/{cat}.rss"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs = parse_wwr(ctx.http.get_text(self.URL.format(cat=target.target)))
        return FetchResult(jobs=[j for j in jobs if _keep(j, ctx, target)], pages=1)


# ---- Adzuna (needs a free key) -------------------------------------------- #
def parse_adzuna(payload: dict) -> List[RawJob]:
    out = []
    for j in payload.get("results", []):
        loc = (j.get("location") or {}).get("display_name", "")
        mn, mx = j.get("salary_min"), j.get("salary_max")
        url = canonicalize_url(j.get("redirect_url", ""))
        out.append(RawJob(
            source="adzuna", source_id=str(j.get("id", "")),
            company=(j.get("company") or {}).get("display_name", ""),
            title=strip_html(j.get("title") or ""), description=strip_html(j.get("description", "")),
            location=loc, work_mode=infer_work_mode(j.get("description", ""), loc),
            employment_type=(j.get("contract_time") or "").replace("_", " "),
            salary_range=f"INR {int(mn):,}–{int(mx):,}" if mn and mx else "",
            posted_at=j.get("created", ""), job_url=url, application_url=url, canonical_url=url,
        ))
    return out


class AdzunaProvider(Provider):
    name, kind, apply_mode = "adzuna", KIND_FEED, APPLY_HANDOFF
    needs_key = True
    homepage = "https://developer.adzuna.com"
    notes = "Official API, India index. Free key: set ADZUNA_APP_ID and ADZUNA_APP_KEY in backend/.env."
    URL = ("https://api.adzuna.com/v1/api/jobs/in/search/{page}?app_id={id}&app_key={key}"
           "&what={q}&results_per_page=50&max_days_old=30&content-type=application/json")

    def configured(self) -> bool:
        return bool(os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs, pages = [], 0
        for page in range(1, min(ctx.max_pages, 3) + 1):
            data = ctx.http.get_json(self.URL.format(
                page=page, id=os.environ.get("ADZUNA_APP_ID", ""),
                key=os.environ.get("ADZUNA_APP_KEY", ""), q=quote_plus(target.target)))
            pages += 1
            batch = parse_adzuna(data)
            jobs.extend(batch)
            if len(batch) < 50:
                break
        return FetchResult(jobs=[j for j in jobs if _keep(j, ctx, target)], pages=pages)
