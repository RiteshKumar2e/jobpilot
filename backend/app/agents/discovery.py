"""Agent 1 — Source Discovery (+ Company Discovery).

Polls every enabled job source through the provider layer (app/providers):
company ATS boards (Greenhouse, Lever, Ashby, Workable, SmartRecruiters,
Recruitee, Personio, Breezy, Workday), company careers pages (schema.org
JobPosting), and public job feeds (Remotive, Remote OK, Arbeitnow, Himalayas,
We Work Remotely, Adzuna).

  * Targets come from candidate_profile.yaml -> sources, plus the company
    registry (companies you added in the dashboard or that were discovered).
  * Sources run concurrently (bounded); one failing source never stops the rest.
  * Every (source, target) run is recorded in provider_health. After
    `breaker_threshold` consecutive failures a source cools down (1h, doubling,
    max 24h) and is retried automatically afterwards.
  * Company discovery: ATS boards linked from feed listings and careers pages
    are added to the registry and polled from the next cycle.

It never asserts a listing is open without the fetch succeeding right now.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select

from ..config import get_settings
from ..logging_conf import get_logger
from ..models import Company, Job, ProviderHealth
from ..providers import PROVIDERS, find_ats_links, profile_targets, search_queries
from ..providers.ats import parse_greenhouse, parse_lever  # noqa: F401  (public API, tests)
from ..providers.base import FetchContext, FetchResult, Http, KIND_FEED, Target
from ..providers.careers import detect_ats
from ..schemas import RawJob
from .verification import _DEFAULT_TITLE_MUST_MATCH, _has_word

log = get_logger("discovery")


def _now() -> datetime:
    # naive UTC: what SQLite DateTime columns round-trip as
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DiscoveryAgent:
    name = "discovery"

    def __init__(self, profile):
        self.profile = profile
        self.settings = get_settings()
        self.last_report: List[dict] = []
        raw = profile.raw()
        ex = raw.get("exclude", {}) or {}
        self._must = [w.lower() for w in (ex.get("title_must_match") or _DEFAULT_TITLE_MUST_MATCH)]
        self._senior = [s.strip().lower() for s in (ex.get("senior_signals") or []) if s.strip()]

    # ---- relevance pre-filter (before anything is stored) ----------------- #
    def title_ok(self, title: str) -> bool:
        t = (title or "").lower()
        if not any(_has_word(t, w) for w in self._must):
            return False
        return not (self.profile.exclude.get("senior_roles", True)
                    and any(_has_word(t, s) for s in self._senior))

    # ---- targets ----------------------------------------------------------- #
    def targets(self, db=None) -> List[Target]:
        enabled = self.settings.enabled_provider_set
        sources = self.profile.raw().get("sources") or {}
        out = profile_targets(sources, enabled)
        if db is not None:
            listed = {t.key for t in out}
            for c in db.scalars(select(Company).where(Company.watch.is_(True))):
                t = Target(c.provider, c.board, c.name)
                if c.provider in enabled and c.provider in PROVIDERS and t.key not in listed:
                    out.append(t)
                    listed.add(t.key)
            health = {h.key: h for h in db.scalars(select(ProviderHealth))}
            for t in out:
                h = health.get(t.key)
                t.since = h.last_ok.replace(tzinfo=timezone.utc) if h and h.last_ok else None
        return out

    # ---- main entry -------------------------------------------------------- #
    def discover(self, db=None) -> List[RawJob]:
        s = self.settings
        targets = self.targets(db)
        health: Dict[str, ProviderHealth] = (
            {h.key: h for h in db.scalars(select(ProviderHealth))} if db is not None else {})
        now = _now()
        runnable, report = [], []
        for t in targets:
            h = health.get(t.key)
            if h and h.disabled_until and h.disabled_until > now:
                report.append({"key": t.key, "provider": t.provider, "target": t.target,
                               "status": "cooling_down", "until": str(h.disabled_until)})
            elif not PROVIDERS[t.provider].configured():
                report.append({"key": t.key, "provider": t.provider, "target": t.target,
                               "status": "needs_key"})
            else:
                runnable.append(t)

        known = set()
        if db is not None:
            known = {(a, b) for a, b in db.execute(
                select(Job.source, Job.source_id).where(Job.description != ""))}
        http = Http(timeout=s.provider_timeout)
        ctx = FetchContext(http=http, max_pages=s.max_pages_per_provider,
                           max_details=s.max_details_per_target, title_ok=self.title_ok,
                           queries=search_queries(self.profile.raw().get("sources") or {}),
                           known=lambda src, sid: (src, sid) in known)
        jobs: List[RawJob] = []
        results: List[tuple] = []
        try:
            with ThreadPoolExecutor(max_workers=max(1, s.discovery_concurrency)) as ex:
                futs = {ex.submit(self._run_one, t, ctx): t for t in runnable}
                for f in as_completed(futs):
                    results.append((futs[f], *f.result()))
        finally:
            http.close()

        for t, res, err, ms in results:
            entry = {"key": t.key, "provider": t.provider, "target": t.target,
                     "status": "error" if err else "ok", "error": err, "ms": ms,
                     "count": len(res.jobs) if res else 0,
                     "complete": bool(res and res.complete and not err),
                     "source_ids": [j.source_id for j in res.jobs] if res else []}
            report.append(entry)
            if res:
                jobs.extend(res.jobs)
            if db is not None:
                self._record_health(db, health, t, entry)
            if err:
                log.warning(f"{t.key} failed: {err}")
            else:
                log.info(f"{t.provider} fetched", extra={"target": t.target, "count": entry["count"]})
        if db is not None:
            db.commit()
            self._register_companies(db, targets, results)

        if len(jobs) > s.max_jobs_per_cycle:
            log.warning(f"{len(jobs)} listings > max_jobs_per_cycle; keeping {s.max_jobs_per_cycle}")
            jobs = jobs[: s.max_jobs_per_cycle]
        self.last_report = report
        return jobs

    @staticmethod
    def _run_one(t: Target, ctx: FetchContext):
        start = time.monotonic()
        try:
            res = PROVIDERS[t.provider].fetch(t, ctx)
            return res, "", int((time.monotonic() - start) * 1000)
        except Exception as e:  # isolate: report, never raise
            return None, f"{type(e).__name__}: {e}"[:400], int((time.monotonic() - start) * 1000)

    # ---- provider health / circuit breaker -------------------------------- #
    def _record_health(self, db, health, t: Target, entry: dict) -> None:
        h = health.get(t.key)
        if h is None:
            h = ProviderHealth(key=t.key, provider=t.provider, target=t.target)
            db.add(h)
            health[t.key] = h
        now = _now()
        h.last_run = now
        h.total_runs = (h.total_runs or 0) + 1
        h.duration_ms = entry["ms"]
        if entry["status"] == "ok":
            h.last_ok = now
            h.consecutive_failures = 0
            h.disabled_until = None
            h.last_error = ""
            h.jobs_last = entry["count"]
            h.jobs_total = (h.jobs_total or 0) + entry["count"]
        else:
            h.consecutive_failures = (h.consecutive_failures or 0) + 1
            h.total_failures = (h.total_failures or 0) + 1
            h.last_error = entry["error"]
            over = h.consecutive_failures - self.settings.breaker_threshold
            if over >= 0:
                h.disabled_until = now + timedelta(hours=min(24, 2 ** over))

    # ---- company registry / discovery ------------------------------------- #
    def _register_companies(self, db, targets: List[Target], results) -> None:
        existing = {(c.provider, c.board.lower()) for c in db.scalars(select(Company))}

        def add(provider, board, name, by, watch, url=""):
            if (provider, board.lower()) in existing or provider not in PROVIDERS:
                return False
            existing.add((provider, board.lower()))
            db.add(Company(provider=provider, board=board, name=name or board, watch=watch,
                           discovered_by=by, careers_url=url))
            return True

        # profile-listed boards show up in the registry too
        for t in targets:
            if PROVIDERS[t.provider].kind != KIND_FEED:
                add(t.provider, t.target, t.label, "profile", True,
                    t.target if t.provider == "careers" else "")
        # boards found inside other listings / careers pages
        new = 0
        cap = self.settings.max_new_companies_per_cycle
        watch = self.settings.auto_watch_discovered
        for t, res, err, _ in results:
            if not res or err:
                continue
            found = list(res.discovered_boards)
            if PROVIDERS[t.provider].kind == KIND_FEED:
                for j in res.jobs:
                    hit = detect_ats(j.application_url) or detect_ats(j.job_url)
                    links = [hit] if hit else find_ats_links(j.description)[:1]
                    found += [(p, b, j.company) for p, b in links]
            for provider, board, name in found:
                if new >= cap:
                    break
                if add(provider, board, name, f"{t.provider}:{t.target}"[:80], watch):
                    new += 1
                    log.info(f"Discovered company board {provider}:{board} via {t.provider}")
            # remember how many relevant openings each registered board had
            if PROVIDERS[t.provider].kind != KIND_FEED:
                c = db.scalar(select(Company).where(Company.provider == t.provider,
                                                    Company.board == t.target))
                if c:
                    c.open_jobs = len(res.jobs)
        db.commit()


def summarize_report(report: List[dict]) -> Dict[str, dict]:
    """Per-provider totals for logs / the Sources page."""
    out: Dict[str, dict] = {}
    for r in report:
        p = out.setdefault(r["provider"], {"targets": 0, "ok": 0, "errors": 0, "jobs": 0,
                                           "cooling_down": 0, "needs_key": 0})
        p["targets"] += 1
        st = r["status"]
        p["ok" if st == "ok" else "errors" if st == "error" else st] += 1
        p["jobs"] += r.get("count", 0)
    return out


def remove_source_ids(report: List[dict]) -> List[dict]:
    return [{k: v for k, v in r.items() if k != "source_ids"} for r in report]


def get_target(report: List[dict], key: str) -> Optional[dict]:
    return next((r for r in report if r["key"] == key), None)
