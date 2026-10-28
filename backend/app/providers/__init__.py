"""Provider registry: every job source JobPilot can poll, and how your profile
(`candidate_profile.yaml -> sources`) and the company registry turn into fetch
targets. See base.py for the interface and README "Adding a job source"."""
from __future__ import annotations

from typing import Dict, List

from .ats import (AshbyProvider, BreezyProvider, GreenhouseProvider, LeverProvider,
                  PersonioProvider, RecruiteeProvider, SmartRecruitersProvider,
                  WorkableProvider, WorkdayProvider)
from .base import Provider, Target
from .careers import CareerPageProvider, detect_ats, find_ats_links
from .feeds import (AdzunaProvider, ArbeitnowProvider, HimalayasProvider, RemoteOKProvider,
                    RemotiveProvider, WeWorkRemotelyProvider)
from .portals import portal_capabilities, portal_for_url, search_links

PROVIDERS: Dict[str, Provider] = {p.name: p for p in (
    GreenhouseProvider(), LeverProvider(), AshbyProvider(), WorkableProvider(),
    SmartRecruitersProvider(), RecruiteeProvider(), PersonioProvider(), BreezyProvider(),
    WorkdayProvider(), CareerPageProvider(),
    RemotiveProvider(), RemoteOKProvider(), ArbeitnowProvider(), HimalayasProvider(),
    WeWorkRemotelyProvider(), AdzunaProvider(),
)}

# profile key under `sources:` -> provider
SOURCE_KEYS = {
    "greenhouse_boards": "greenhouse", "lever_accounts": "lever", "ashby_boards": "ashby",
    "workable_accounts": "workable", "smartrecruiters_companies": "smartrecruiters",
    "recruitee_companies": "recruitee", "personio_companies": "personio",
    "breezy_companies": "breezy", "workday_sites": "workday", "career_pages": "careers",
}
# feeds searched once per query vs. fetched once per cycle
QUERY_FEEDS = ("remotive", "himalayas", "adzuna")
SINGLE_FEEDS = ("remoteok", "arbeitnow")
DEFAULT_WWR_CATEGORIES = ["remote-programming-jobs", "remote-back-end-programming-jobs",
                          "remote-full-stack-programming-jobs"]
DEFAULT_QUERIES = ["machine learning engineer", "ai engineer", "software engineer",
                   "python developer", "backend engineer", "computer vision"]


def _entries(value) -> List[tuple]:
    """Profile list items may be 'slug' or {id: slug, name: 'Display Name'}."""
    out = []
    for v in value or []:
        if isinstance(v, dict):
            if v.get("id"):
                out.append((str(v["id"]).strip(), str(v.get("name") or "")))
        elif str(v).strip():
            out.append((str(v).strip(), ""))
    return out


def search_queries(raw_sources: dict) -> List[str]:
    return [q for q in (raw_sources.get("search_queries") or DEFAULT_QUERIES) if str(q).strip()]


def profile_targets(raw_sources: dict, enabled: set) -> List[Target]:
    """Targets declared in candidate_profile.yaml -> sources."""
    out: List[Target] = []
    for key, provider in SOURCE_KEYS.items():
        if provider not in enabled:
            continue
        for ident, name in _entries(raw_sources.get(key)):
            # Boards you listed by hand keep the old behaviour (every title is
            # stored and verified); discovered boards are title-filtered.
            out.append(Target(provider, ident, name,
                              filter_titles=provider not in ("greenhouse", "lever")))
    feeds = raw_sources.get("feeds")
    if feeds is None:
        feeds = {f: True for f in (*QUERY_FEEDS, *SINGLE_FEEDS, "weworkremotely")}
    queries = search_queries(raw_sources)
    for feed, conf in (feeds or {}).items():
        if not conf or feed not in enabled or feed not in PROVIDERS:
            continue
        if feed in QUERY_FEEDS:
            out += [Target(feed, q) for q in queries]
        elif feed == "weworkremotely":
            cats = conf if isinstance(conf, list) else DEFAULT_WWR_CATEGORIES
            out += [Target(feed, c) for c in cats]
        else:
            out.append(Target(feed, "latest"))
    return out


def capability_matrix() -> List[dict]:
    """What each source can actually do — tested adapters vs. handoff portals."""
    return [p.describe() for p in PROVIDERS.values()] + portal_capabilities()


__all__ = ["PROVIDERS", "SOURCE_KEYS", "Target", "profile_targets", "search_queries",
           "capability_matrix", "detect_ats", "find_ats_links", "search_links",
           "portal_for_url"]
