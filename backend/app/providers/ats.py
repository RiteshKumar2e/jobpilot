"""Applicant-tracking-system providers: each company's own public job board.

Every adapter uses the platform's public, unauthenticated job-board endpoint —
the same one the company's careers page calls:

  greenhouse       boards-api.greenhouse.io/v1/boards/{token}/jobs
  lever            api.lever.co/v0/postings/{account}
  ashby            api.ashbyhq.com/posting-api/job-board/{org}
  workable         apply.workable.com/api/v1/widget/accounts/{account} (+ v2 job detail)
  smartrecruiters  api.smartrecruiters.com/v1/companies/{id}/postings (+ detail)
  recruitee        {company}.recruitee.com/api/offers/
  personio         {company}.jobs.personio.de/xml
  breezy           {company}.breezy.hr/json
  workday          {tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

Parsers are pure functions (unit-tested against captured payloads); `fetch`
does the HTTP. Job details are only fetched for titles that pass your profile's
relevance filter and aren't stored yet, to keep request counts low.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List
from urllib.parse import quote

from ..agents.base import canonicalize_url, is_india_location
from ..schemas import RawJob
from .base import (APPLY_ASSIST, APPLY_AUTO, KIND_ATS, FetchContext, FetchResult,
                   Provider, Target, infer_work_mode, join_locations, strip_html)


def _india_or_remote(text: str) -> bool:
    t = (text or "").lower()
    return is_india_location(t) or "remote" in t or "anywhere" in t


def _board(jobs: List[RawJob], target: Target, ctx: FetchContext) -> FetchResult:
    """A full board listing: remember every id (for closed-listing detection),
    then keep the titles that pass your profile's relevance filter."""
    listed = [j.source_id for j in jobs]
    if target.filter_titles:
        jobs = [j for j in jobs if ctx.title_ok(j.title)]
    return FetchResult(jobs=jobs, complete=True, pages=1, listed_ids=listed)


# --------------------------------------------------------------------------- #
# Greenhouse
# --------------------------------------------------------------------------- #
def parse_greenhouse(board_token: str, payload: dict) -> List[RawJob]:
    out: List[RawJob] = []
    for j in payload.get("jobs", []):
        content = strip_html(j.get("content", ""))
        location = (j.get("location") or {}).get("name", "")
        url = canonicalize_url(j.get("absolute_url", ""))
        out.append(RawJob(
            source="greenhouse", source_id=str(j.get("id", "")),
            company=board_token,  # board token == company slug (the apply form needs it)
            board=board_token,
            title=(j.get("title") or "").strip(), description=content,
            location=location, work_mode=infer_work_mode(content, location),
            posted_at=j.get("updated_at", "") or j.get("first_published", ""),
            job_url=url, application_url=url, canonical_url=url,
        ))
    return out


class GreenhouseProvider(Provider):
    name, kind, apply_mode = "greenhouse", KIND_ATS, APPLY_AUTO
    homepage = "https://developers.greenhouse.io/job-board.html"
    notes = "Official Job Board API. Auto-submit supported (hosted form)."
    URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs = parse_greenhouse(target.target, ctx.http.get_json(self.URL.format(token=target.target)))
        return _board(jobs, target, ctx)


# --------------------------------------------------------------------------- #
# Lever
# --------------------------------------------------------------------------- #
def _lever_salary(j: dict) -> str:
    sr = j.get("salaryRange") or {}
    mn, mx, cur = sr.get("min"), sr.get("max"), sr.get("currency", "")
    if mn and mx:
        return f"{cur} {mn:,}–{mx:,}".strip()
    return ""


def parse_lever(account: str, payload: list) -> List[RawJob]:
    out: List[RawJob] = []
    for j in payload:
        cats = j.get("categories", {}) or {}
        desc = strip_html(j.get("descriptionPlain") or j.get("description", ""))
        all_locs = [l for l in (cats.get("allLocations") or []) if l]
        location = "; ".join(all_locs) if len(all_locs) > 1 else cats.get("location", "")
        # Lever's own workplaceType (remote|hybrid|onsite|unspecified) beats inference.
        wpt = (j.get("workplaceType") or "").lower()
        work_mode = wpt if wpt in ("remote", "hybrid", "onsite") else infer_work_mode(desc, location)
        url = canonicalize_url(j.get("hostedUrl", "") or j.get("applyUrl", ""))
        out.append(RawJob(
            source="lever", source_id=str(j.get("id", "")), company=account, board=account,
            title=(j.get("text") or "").strip(), description=desc,
            location=location, work_mode=work_mode,
            employment_type=cats.get("commitment", ""),
            salary_range=_lever_salary(j),
            posted_at=str(j.get("createdAt", "")),
            job_url=url, application_url=canonicalize_url(j.get("applyUrl", "") or url),
            canonical_url=url,
        ))
    return out


class LeverProvider(Provider):
    name, kind, apply_mode = "lever", KIND_ATS, APPLY_AUTO
    homepage = "https://github.com/lever/postings-api"
    notes = "Official Postings API. Auto-submit supported."
    URL = "https://api.lever.co/v0/postings/{account}?mode=json"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs = parse_lever(target.target, ctx.http.get_json(self.URL.format(account=target.target)))
        return _board(jobs, target, ctx)


# --------------------------------------------------------------------------- #
# Ashby
# --------------------------------------------------------------------------- #
_ASHBY_EMPLOYMENT = {"FullTime": "Full-time", "PartTime": "Part-time", "Intern": "Internship",
                     "Contract": "Contract", "Temporary": "Temporary"}
_ASHBY_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}


def parse_ashby(org: str, payload: dict, company: str = "") -> List[RawJob]:
    out: List[RawJob] = []
    for j in payload.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in (j.get("secondaryLocations") or [])]
        location = join_locations(locs)
        desc = (j.get("descriptionPlain") or "").strip() or strip_html(j.get("descriptionHtml", ""))
        wp = _ASHBY_WORKPLACE.get((j.get("workplaceType") or "").lower(), "")
        if not wp:
            wp = "remote" if j.get("isRemote") else infer_work_mode(desc, location)
        comp = j.get("compensation") or {}
        url = canonicalize_url(j.get("jobUrl", ""))
        out.append(RawJob(
            source="ashby", source_id=str(j.get("id", "")), company=company or org, board=org,
            title=(j.get("title") or "").strip(), description=desc, location=location,
            work_mode=wp,
            employment_type=_ASHBY_EMPLOYMENT.get(j.get("employmentType", ""), j.get("employmentType", "") or ""),
            salary_range=(comp.get("scrapeableCompensationSalarySummary")
                          or comp.get("compensationTierSummary") or ""),
            posted_at=j.get("publishedAt", ""),
            job_url=url, application_url=canonicalize_url(j.get("applyUrl", "") or url),
            canonical_url=url,
        ))
    return out


class AshbyProvider(Provider):
    name, kind, apply_mode = "ashby", KIND_ATS, APPLY_ASSIST
    homepage = "https://developers.ashbyhq.com/docs/public-job-posting-api"
    notes = "Official public Job Posting API (no key)."
    URL = "https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        data = ctx.http.get_json(self.URL.format(org=quote(target.target)))
        jobs = parse_ashby(target.target, data, target.label)
        return _board(jobs, target, ctx)


# --------------------------------------------------------------------------- #
# Workable
# --------------------------------------------------------------------------- #
def _workable_location(locs: list, fallback: dict) -> str:
    parts = []
    for l in (locs or [fallback]):
        if l and not l.get("hidden"):
            parts.append(", ".join(x for x in (l.get("city"), l.get("region") or l.get("state"),
                                               l.get("country")) if x))
    return join_locations(parts)


def parse_workable_list(account: str, payload: dict) -> List[RawJob]:
    company = payload.get("name") or account
    out = []
    for j in payload.get("jobs", []):
        location = _workable_location(j.get("locations"), j)
        remote = bool(j.get("telecommuting"))
        url = canonicalize_url(j.get("url") or j.get("shortlink") or "")
        out.append(RawJob(
            source="workable", source_id=str(j.get("shortcode", "")), company=company, board=account,
            title=(j.get("title") or "").strip(),
            location=("Remote - " + location) if remote and "remote" not in location.lower() else location,
            work_mode="remote" if remote else infer_work_mode("", location),
            employment_type=j.get("employment_type", ""),
            experience_req=j.get("experience", ""),
            posted_at=j.get("published_on") or j.get("created_at", ""),
            job_url=url, application_url=canonicalize_url(j.get("application_url") or url),
            canonical_url=url,
        ))
    return out


def apply_workable_detail(job: RawJob, d: dict) -> RawJob:
    desc = strip_html(" ".join(x for x in (d.get("description"), d.get("requirements"),
                                           d.get("benefits")) if x))
    wp = (d.get("workplace") or "").lower()
    return job.model_copy(update={
        "description": desc or job.description,
        "work_mode": {"remote": "remote", "hybrid": "hybrid", "on_site": "onsite",
                      "onsite": "onsite"}.get(wp, job.work_mode),
    })


class WorkableProvider(Provider):
    name, kind, apply_mode = "workable", KIND_ATS, APPLY_ASSIST
    homepage = "https://apply.workable.com"
    notes = "Public careers-widget JSON; descriptions from the public job endpoint."
    LIST = "https://apply.workable.com/api/v1/widget/accounts/{account}"
    DETAIL = "https://apply.workable.com/api/v2/accounts/{account}/jobs/{code}"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs = parse_workable_list(target.target,
                                   ctx.http.get_json(self.LIST.format(account=quote(target.target))))
        listed = [j.source_id for j in jobs]
        if target.filter_titles:
            jobs = [j for j in jobs if ctx.title_ok(j.title)]
        budget = ctx.max_details
        for i, j in enumerate(jobs):
            if budget <= 0 or ctx.known(j.source, j.source_id):
                continue
            budget -= 1
            try:
                d = ctx.http.get_json(self.DETAIL.format(account=quote(target.target), code=j.source_id))
                jobs[i] = apply_workable_detail(j, d)
            except Exception:
                pass  # keep the listing without a description
        return FetchResult(jobs=jobs, complete=True, pages=1, listed_ids=listed)


# --------------------------------------------------------------------------- #
# SmartRecruiters
# --------------------------------------------------------------------------- #
def parse_smartrecruiters_item(company_id: str, j: dict) -> RawJob:
    loc = j.get("location") or {}
    location = (loc.get("fullLocation") or ", ".join(
        x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x))
    location = re.sub(r"(,\s*)+", ", ", location).strip(", ")
    mode = "remote" if loc.get("remote") else "hybrid" if loc.get("hybrid") else (
        "onsite" if location else "")
    if mode == "remote" and "remote" not in location.lower():
        location = f"Remote - {location}" if location else "Remote"
    url = j.get("postingUrl") or f"https://jobs.smartrecruiters.com/{company_id}/{j.get('id', '')}"
    sections = ((j.get("jobAd") or {}).get("sections") or {})
    desc = strip_html(" ".join((sections.get(k) or {}).get("text", "") for k in
                               ("jobDescription", "qualifications", "additionalInformation")))
    level = (j.get("experienceLevel") or {}).get("label", "")
    return RawJob(
        source="smartrecruiters", source_id=str(j.get("id", "")),
        company=(j.get("company") or {}).get("name") or company_id, board=company_id,
        title=(j.get("name") or "").strip(), description=desc, location=location,
        work_mode=mode,
        employment_type=(j.get("typeOfEmployment") or {}).get("label", ""),
        experience_req="" if level in ("", "Not Applicable") else level,
        posted_at=j.get("releasedDate", ""),
        job_url=canonicalize_url(url), application_url=canonicalize_url(j.get("applyUrl") or url),
        canonical_url=canonicalize_url(url),
    )


class SmartRecruitersProvider(Provider):
    name, kind, apply_mode = "smartrecruiters", KIND_ATS, APPLY_ASSIST
    homepage = "https://developers.smartrecruiters.com/docs/posting-api"
    notes = ("Official Posting API (no key). Target 'Company' fetches India postings; "
             "'Company?all' fetches every country.")
    LIST = "https://api.smartrecruiters.com/v1/companies/{c}/postings?limit=100&offset={off}{country}"
    DETAIL = "https://api.smartrecruiters.com/v1/companies/{c}/postings/{id}"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        company, _, opt = target.target.partition("?")
        country = "" if opt == "all" else "&country=in"
        items, total, pages = [], None, 0
        while pages < ctx.max_pages and (total is None or len(items) < total):
            data = ctx.http.get_json(self.LIST.format(c=quote(company), off=len(items), country=country))
            pages += 1
            batch = data.get("content") or []
            total = data.get("totalFound", 0)
            items.extend(batch)
            if not batch:
                break
        jobs = [parse_smartrecruiters_item(company, j) for j in items]
        listed = [j.source_id for j in jobs]
        if target.filter_titles:
            jobs = [j for j in jobs if ctx.title_ok(j.title)]
        budget = ctx.max_details
        for i, j in enumerate(jobs):
            if budget <= 0 or ctx.known(j.source, j.source_id):
                continue
            budget -= 1
            try:
                jobs[i] = parse_smartrecruiters_item(
                    company, ctx.http.get_json(self.DETAIL.format(c=quote(company), id=j.source_id)))
            except Exception:
                pass
        return FetchResult(jobs=jobs, complete=total is not None and len(items) >= total,
                           pages=pages, listed_ids=listed)


# --------------------------------------------------------------------------- #
# Recruitee
# --------------------------------------------------------------------------- #
def parse_recruitee(company_id: str, payload: dict) -> List[RawJob]:
    out = []
    for o in payload.get("offers", []):
        locs = [l.get("name") and ", ".join(x for x in (l.get("city") or l.get("name"),
                                                        l.get("country")) if x)
                for l in (o.get("locations") or [])]
        location = join_locations(locs) or o.get("location", "")
        mode = "remote" if o.get("remote") else "hybrid" if o.get("hybrid") else (
            "onsite" if location else "")
        if mode == "remote" and "remote" not in location.lower():
            location = f"Remote - {location}" if location else "Remote"
        sal = o.get("salary") or {}
        salary = (f"{sal.get('currency') or ''} {sal.get('min')}–{sal.get('max')} {sal.get('period') or ''}".strip()
                  if sal.get("min") and sal.get("max") else "")
        url = canonicalize_url(o.get("careers_url") or o.get("url") or "")
        out.append(RawJob(
            source="recruitee", source_id=str(o.get("id") or o.get("guid") or o.get("slug", "")),
            company=o.get("company_name") or company_id, board=company_id,
            title=(o.get("title") or "").strip(),
            description=strip_html(f"{o.get('description', '')} {o.get('requirements', '')}"),
            location=location, work_mode=mode,
            employment_type=(o.get("employment_type_code") or "").replace("_", " "),
            experience_req=(o.get("experience_code") or "").replace("_", " "),
            salary_range=salary, posted_at=o.get("published_at") or o.get("created_at", ""),
            job_url=url, application_url=canonicalize_url(o.get("careers_apply_url") or url),
            canonical_url=url,
        ))
    return out


class RecruiteeProvider(Provider):
    name, kind, apply_mode = "recruitee", KIND_ATS, APPLY_ASSIST
    homepage = "https://docs.recruitee.com/reference/offers"
    notes = "Public Careers Site API (no key)."
    URL = "https://{c}.recruitee.com/api/offers/"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs = parse_recruitee(target.target, ctx.http.get_json(self.URL.format(c=target.target)))
        return _board(jobs, target, ctx)


# --------------------------------------------------------------------------- #
# Personio
# --------------------------------------------------------------------------- #
def parse_personio(company_id: str, xml_text: str, host: str = "") -> List[RawJob]:
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    host = host or f"{company_id}.jobs.personio.de"
    out = []
    for p in root.findall("position"):
        g = lambda tag: (p.findtext(tag) or "").strip()
        offices = [g("office")] + [(o.text or "").strip() for o in p.findall("additionalOffices/office")]
        location = join_locations(offices)
        parts = []
        for jd in p.findall("jobDescriptions/jobDescription"):
            parts.append(f"{jd.findtext('name') or ''}: {jd.findtext('value') or ''}")
        years, seniority = g("yearsOfExperience"), g("seniority")
        # The feed's own seniority/years go into the text the verifier reads.
        meta = " ".join(x for x in (f"Seniority: {seniority}." if seniority else "",
                                    f"Experience: {years} years." if years else "") if x)
        url = f"https://{host}/job/{g('id')}"
        out.append(RawJob(
            source="personio", source_id=g("id"), company=g("subcompany") or company_id,
            board=company_id, title=g("name"),
            description=strip_html(" ".join(parts) + " " + meta).strip(),
            location=location, work_mode=infer_work_mode("", location),
            employment_type=" ".join(x for x in (g("employmentType"), g("schedule")) if x),
            experience_req=f"{years} years" if years else seniority,
            posted_at=g("createdAt"), job_url=url, application_url=url, canonical_url=url,
        ))
    return out


class PersonioProvider(Provider):
    name, kind, apply_mode = "personio", KIND_ATS, APPLY_ASSIST
    homepage = "https://developer.personio.de/docs/retrieving-open-job-positions"
    notes = "Public XML job feed (no key)."
    URL = "https://{c}.jobs.personio.de/xml?language=en"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs = parse_personio(target.target, ctx.http.get_text(self.URL.format(c=target.target)))
        return _board(jobs, target, ctx)


# --------------------------------------------------------------------------- #
# Breezy HR
# --------------------------------------------------------------------------- #
def parse_breezy(company_id: str, payload: list) -> List[RawJob]:
    out = []
    for j in payload or []:
        locs = j.get("locations") or [j.get("location") or {}]
        names, remote = [], False
        for l in locs:
            remote = remote or bool(l.get("is_remote"))
            country = (l.get("country") or {}).get("name", "")
            n = l.get("name") or ""
            names.append(f"{n}, {country}" if country and country not in n else n)
        location = join_locations(names)
        if remote and "remote" not in location.lower():
            location = f"Remote - {location}" if location else "Remote"
        url = canonicalize_url(j.get("url", ""))
        out.append(RawJob(
            source="breezy", source_id=str(j.get("id", "")),
            company=(j.get("company") or {}).get("name") or company_id, board=company_id,
            title=(j.get("name") or "").strip(), location=location,
            work_mode="remote" if remote else infer_work_mode("", location),
            employment_type=(j.get("type") or {}).get("name", ""),
            salary_range=j.get("salary") or "", posted_at=j.get("published_date", ""),
            job_url=url, application_url=url, canonical_url=url,
        ))
    return out


class BreezyProvider(Provider):
    name, kind, apply_mode = "breezy", KIND_ATS, APPLY_ASSIST
    homepage = "https://breezy.hr"
    notes = "Public careers JSON (no descriptions; listings verify on title/location)."
    URL = "https://{c}.breezy.hr/json"

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        jobs = parse_breezy(target.target, ctx.http.get_json(self.URL.format(c=target.target)))
        return _board(jobs, target, ctx)


# --------------------------------------------------------------------------- #
# Workday
# --------------------------------------------------------------------------- #
_WD_TARGET = re.compile(r"^(?P<tenant>[\w-]+)\.(?P<wd>wd\d+)/(?P<site>[\w-]+)$")


def workday_base(target: str) -> tuple[str, str, str]:
    """'nvidia.wd5/NVIDIAExternalCareerSite' -> (api base, public base, tenant)."""
    m = _WD_TARGET.match(target.strip())
    if not m:
        raise ValueError("Workday target must look like 'tenant.wd5/SiteName'")
    t, wd, site = m.group("tenant"), m.group("wd"), m.group("site")
    host = f"https://{t}.{wd}.myworkdayjobs.com"
    return f"{host}/wday/cxs/{t}/{site}", f"{host}/{site}", t


def parse_workday_detail(target: str, path: str, payload: dict) -> RawJob:
    _, public, tenant = workday_base(target)
    info = payload.get("jobPostingInfo") or {}
    location = join_locations([info.get("location", "")] + list(info.get("additionalLocations") or []))
    desc = strip_html(info.get("jobDescription", ""))
    url = canonicalize_url(info.get("externalUrl") or f"{public}{path}")
    return RawJob(
        source="workday", source_id=info.get("jobReqId") or path, board=target,
        company=((payload.get("hiringOrganization") or {}).get("name") or tenant),
        title=(info.get("title") or "").strip(), description=desc, location=location,
        work_mode=infer_work_mode(desc, location), employment_type=info.get("timeType", ""),
        posted_at=info.get("startDate", ""), job_url=url, application_url=url, canonical_url=url,
    )


class WorkdayProvider(Provider):
    name, kind, apply_mode = "workday", KIND_ATS, APPLY_ASSIST
    homepage = "https://www.workday.com"
    notes = ("Public careers-site JSON used by myworkdayjobs.com pages. Searched with your "
             "queries; keeps India / remote postings. Target: 'tenant.wd5/SiteName'.")

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        api, _, _ = workday_base(target.target)
        found: dict = {}
        pages = 0
        for q in (ctx.queries or [""]):
            offset = 0
            while pages < ctx.max_pages * max(1, len(ctx.queries)):
                data = ctx.http.post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20,
                                                          "offset": offset, "searchText": q})
                pages += 1
                posts = data.get("jobPostings") or []
                for p in posts:
                    if p.get("externalPath"):
                        found.setdefault(p["externalPath"], p)
                offset += len(posts)
                if not posts or offset >= (data.get("total") or 0) or offset >= 100:
                    break
        jobs, budget = [], ctx.max_details
        for path, p in found.items():
            title = (p.get("title") or "").strip()
            if target.filter_titles and not ctx.title_ok(title):
                continue
            # The path starts with the primary location ("/job/India-Bangalore/...").
            where = f"{path.split('/')[2] if path.count('/') > 2 else ''} {p.get('locationsText', '')}"
            where = where.replace("-", " ")
            multi = bool(re.search(r"\d+ Locations", p.get("locationsText", "")))
            if not (_india_or_remote(where) or multi):
                continue
            sid = (p.get("bulletFields") or [path])[0]
            if ctx.known("workday", sid):
                jobs.append(RawJob(source="workday", source_id=sid, company="", board=target.target,
                                   title=title))
                continue
            if budget <= 0:
                continue
            budget -= 1
            try:
                job = parse_workday_detail(target.target, path, ctx.http.get_json(f"{api}{path}"))
            except Exception:
                continue
            if _india_or_remote(job.location):
                jobs.append(job)
        return FetchResult(jobs=jobs, complete=False, pages=pages)
