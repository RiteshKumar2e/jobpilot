"""Company careers pages + ATS detection.

`detect_ats(url)` maps any job / careers URL to (provider, board id) for the
ATS platforms we have adapters for. It powers:
  * "Add company": paste a careers URL, we work out which board to poll;
  * company discovery: ATS links found inside feed listings and careers pages
    register those companies for future cycles;
  * URL import.

`CareerPageProvider` fetches a careers page and reads what it can legitimately
parse: schema.org JobPosting JSON-LD (what Google for Jobs uses) and links to
supported ATS boards. It does not crawl or guess at arbitrary HTML layouts.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from ..agents.base import canonicalize_url
from ..schemas import RawJob
from .base import (APPLY_HANDOFF, KIND_CAREERS, FetchContext, FetchResult, Provider, Target,
                   infer_work_mode, join_locations, strip_html)

_ATS_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"https?://boards-api\.greenhouse\.io/v1/boards/([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_(?:board|app)\?for=([\w-]+)", re.I)),
    ("lever", re.compile(r"https?://jobs\.(?:eu\.)?lever\.co/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([\w.%-]+)", re.I)),
    ("workable", re.compile(r"https?://apply\.workable\.com/([\w-]+)", re.I)),
    ("smartrecruiters", re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", re.I)),
    ("recruitee", re.compile(r"https?://([\w-]+)\.recruitee\.com", re.I)),
    ("personio", re.compile(r"https?://([\w-]+)\.jobs\.personio\.(?:de|com)", re.I)),
    ("breezy", re.compile(r"https?://([\w-]+)\.breezy\.hr", re.I)),
    ("workday", re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", re.I)),
]
_IGNORE_IDS = {"j", "embed", "v1", "api", "jobs", "job", "apply", "careers", "o", "p", "wday",
               "external", "static", "assets", "www"}


def detect_ats(url: str) -> Optional[Tuple[str, str]]:
    """'https://jobs.lever.co/acme/123' -> ('lever', 'acme'); None if unknown."""
    for provider, rx in _ATS_PATTERNS:
        m = rx.search(url or "")
        if not m:
            continue
        if provider == "workday":
            tenant, wd, site = m.groups()
            if site.lower() in _IGNORE_IDS:
                continue
            return provider, f"{tenant.lower()}.{wd.lower()}/{site}"
        board = m.group(1)
        if board.lower() in _IGNORE_IDS:
            continue
        return provider, board if provider == "smartrecruiters" else board.lower()
    return None


def find_ats_links(text: str) -> List[Tuple[str, str]]:
    """Every distinct supported ATS board referenced in some HTML/text."""
    seen, out = set(), []
    for m in re.finditer(r"https?://[^\s\"'<>)]+", text or ""):
        hit = detect_ats(m.group(0))
        if hit and hit not in seen:
            seen.add(hit)
            out.append(hit)
    return out


# ---- schema.org JobPosting ------------------------------------------------- #
_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)


def _walk_postings(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk_postings(x)
    elif isinstance(node, dict):
        t = node.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            yield node
        for k in ("@graph", "itemListElement", "item"):
            if k in node:
                yield from _walk_postings(node[k])


def _ld_location(p: dict) -> str:
    locs = p.get("jobLocation") or []
    if isinstance(locs, dict):
        locs = [locs]
    parts = []
    for l in locs:
        a = (l or {}).get("address") or {}
        if isinstance(a, str):
            parts.append(a)
            continue
        country = a.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name", "")
        parts.append(", ".join(x for x in (a.get("addressLocality"), a.get("addressRegion"), country) if x))
    loc = join_locations(parts)
    if str(p.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        req = p.get("applicantLocationRequirements") or []
        if isinstance(req, dict):
            req = [req]
        names = [r.get("name", "") for r in req if isinstance(r, dict)]
        loc = join_locations(["Remote" + (f" - {', '.join(n for n in names if n)}" if any(names) else ""), loc])
    return loc


def parse_jsonld_jobs(html: str, page_url: str, company_hint: str = "") -> List[RawJob]:
    out = []
    for block in _LD_RE.findall(html or ""):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for p in _walk_postings(data):
            org = p.get("hiringOrganization") or {}
            company = (org.get("name") if isinstance(org, dict) else str(org)) or company_hint
            url = canonicalize_url(urljoin(page_url, p.get("url") or page_url))
            desc = strip_html(p.get("description", ""))
            loc = _ld_location(p)
            ident = p.get("identifier")
            sid = (ident.get("value") if isinstance(ident, dict) else ident) or url
            sal = ""
            bs = p.get("baseSalary") or {}
            if isinstance(bs, dict) and isinstance(bs.get("value"), dict):
                v = bs["value"]
                if v.get("minValue") and v.get("maxValue"):
                    sal = f"{bs.get('currency', '')} {v['minValue']}–{v['maxValue']} {v.get('unitText', '')}".strip()
            et = p.get("employmentType") or ""
            out.append(RawJob(
                source="careers", source_id=str(sid), company=company,
                board=urlparse(page_url).netloc, title=strip_html(p.get("title", "")),
                description=desc, location=loc, work_mode=infer_work_mode(desc, loc),
                employment_type=", ".join(et) if isinstance(et, list) else str(et),
                salary_range=sal, posted_at=str(p.get("datePosted", "")),
                job_url=url, application_url=url, canonical_url=url,
            ))
    return out


class CareerPageProvider(Provider):
    name, kind, apply_mode = "careers", KIND_CAREERS, APPLY_HANDOFF
    homepage = "https://schema.org/JobPosting"
    notes = ("Any company careers URL: reads schema.org JobPosting data and registers "
             "linked Greenhouse/Lever/Ashby/... boards for polling.")

    def fetch(self, target: Target, ctx: FetchContext) -> FetchResult:
        html = ctx.http.get_text(target.target)
        jobs = parse_jsonld_jobs(html, target.target, target.label)
        jobs = [j for j in jobs if j.title and (not target.filter_titles or ctx.title_ok(j.title))]
        boards = [(p, b, target.label) for p, b in find_ats_links(html)]
        hit = detect_ats(target.target)
        if hit and hit not in [(p, b) for p, b, _ in boards]:
            boards.append((hit[0], hit[1], target.label))
        return FetchResult(jobs=jobs, pages=1, discovered_boards=boards)
