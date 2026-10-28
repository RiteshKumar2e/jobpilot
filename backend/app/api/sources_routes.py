"""Sources, company registry, portal handoff links and discovery monitoring."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_session
from ..models import Company, Event, Job, JobSource, KeyValue, ProviderHealth
from ..orchestrator import lifecycle_state
from ..profile_loader import load_profile
from ..providers import PROVIDERS, capability_matrix, detect_ats, search_links, search_queries
from .deps import get_orchestrator, require_api_key

router = APIRouter()


def _health_rows(db) -> list:
    return [{"key": h.key, "provider": h.provider, "target": h.target,
             "last_run": str(h.last_run or ""), "last_ok": str(h.last_ok or ""),
             "last_error": h.last_error, "consecutive_failures": h.consecutive_failures,
             "total_runs": h.total_runs, "total_failures": h.total_failures,
             "jobs_last": h.jobs_last, "jobs_total": h.jobs_total, "duration_ms": h.duration_ms,
             "cooling_down_until": str(h.disabled_until or "")}
            for h in db.scalars(select(ProviderHealth).order_by(ProviderHealth.provider,
                                                                ProviderHealth.target))]


# ---------- sources / providers -------------------------------------------- #
@router.get("/api/sources")
def sources(db: Session = Depends(get_session)):
    """Every source: capability, enabled/configured, and per-target health."""
    s = get_settings()
    enabled = s.enabled_provider_set
    health = _health_rows(db)
    stored = dict(db.execute(select(JobSource.source, func.count(JobSource.id))
                             .group_by(JobSource.source)).all())
    eligible = dict(db.execute(select(Job.source, func.count(Job.id))
                               .where(Job.eligibility_status == "VERIFIED")
                               .group_by(Job.source)).all())
    allow = s.auto_apply_sources
    rows = []
    for cap in capability_matrix():
        n = cap["name"]
        hs = [h for h in health if h["provider"] == n]
        if cap["kind"] == "portal":
            status = "handoff"
        elif cap["needs_key"] and not cap["configured"]:
            status = "needs_key"
        elif n not in enabled:
            status = "disabled"
        elif not hs:
            status = "configured"
        elif any(h["cooling_down_until"] for h in hs):
            status = "degraded"
        elif all(h["consecutive_failures"] for h in hs):
            status = "failing"
        else:
            status = "active"
        rows.append({**cap, "enabled": n in enabled, "status": status,
                     "auto_submit": n in allow, "targets": len(hs),
                     "failing_targets": sum(1 for h in hs if h["consecutive_failures"]),
                     "last_ok": max((h["last_ok"] for h in hs), default=""),
                     "jobs_last": sum(h["jobs_last"] for h in hs),
                     "jobs_stored": stored.get(n, 0), "eligible": eligible.get(n, 0)})
    last = db.get(KeyValue, "last_discovery")
    return {"providers": rows, "targets": health, "last_discovery": last.value if last else {}}


class SourceTestIn(BaseModel):
    provider: str
    target: str


@router.post("/api/sources/test", dependencies=[Depends(require_api_key)])
def test_source(body: SourceTestIn):
    """Fetch one source now (nothing is stored) — to check a board id / URL."""
    from ..providers.base import FetchContext, Http, Target
    p = PROVIDERS.get(body.provider)
    if not p:
        raise HTTPException(404, f"Unknown provider '{body.provider}'")
    if not p.configured():
        return {"ok": False, "error": "Needs an API key — see README."}
    orch = get_orchestrator()
    http = Http(timeout=get_settings().provider_timeout)
    try:
        ctx = FetchContext(http=http, max_pages=1, max_details=5,
                           title_ok=orch.discovery.title_ok if orch else (lambda t: True),
                           queries=search_queries(load_profile().raw().get("sources") or {})[:1])
        res = p.fetch(Target(body.provider, body.target.strip()), ctx)
        return {"ok": True, "count": len(res.jobs), "complete": res.complete,
                "sample": [{"title": j.title, "company": j.company, "location": j.location,
                            "url": j.job_url} for j in res.jobs[:8]],
                "discovered_boards": [list(b) for b in res.discovered_boards[:10]]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:400]}
    finally:
        http.close()


# ---------- company registry ----------------------------------------------- #
def board_url(provider: str, board: str) -> str:
    return {
        "greenhouse": f"https://job-boards.greenhouse.io/{board}",
        "lever": f"https://jobs.lever.co/{board}",
        "ashby": f"https://jobs.ashbyhq.com/{board}",
        "workable": f"https://apply.workable.com/{board}",
        "smartrecruiters": f"https://careers.smartrecruiters.com/{board.split('?')[0]}",
        "recruitee": f"https://{board}.recruitee.com",
        "personio": f"https://{board}.jobs.personio.de",
        "breezy": f"https://{board}.breezy.hr",
        "careers": board,
    }.get(provider, "")


@router.get("/api/companies")
def companies(db: Session = Depends(get_session)):
    stored = {(s, b): n for s, b, n in db.execute(
        select(Job.source, Job.board, func.count(Job.id)).group_by(Job.source, Job.board))}
    eligible = {(s, b): n for s, b, n in db.execute(
        select(Job.source, Job.board, func.count(Job.id))
        .where(Job.eligibility_status == "VERIFIED").group_by(Job.source, Job.board))}
    health = {h.key: h for h in db.scalars(select(ProviderHealth))}
    allow = get_settings().auto_apply_sources
    out = []
    for c in db.scalars(select(Company).order_by(Company.name)):
        h = health.get(f"{c.provider}:{c.board}")
        out.append({"id": c.id, "name": c.name, "provider": c.provider, "board": c.board,
                    "careers_url": c.careers_url or board_url(c.provider, c.board),
                    "watch": c.watch, "discovered_by": c.discovered_by,
                    "open_jobs": c.open_jobs,
                    "stored_jobs": stored.get((c.provider, c.board), 0),
                    "eligible_jobs": eligible.get((c.provider, c.board), 0),
                    "status": "not polled yet" if not h else "error" if h.consecutive_failures else "ok",
                    "last_error": h.last_error if h else "",
                    "auto_submit": c.provider in allow})
    return out


class CompanyIn(BaseModel):
    url: str = ""          # careers page or any job link — ATS auto-detected
    provider: str = ""     # or provider + board id directly
    board: str = ""
    name: str = ""


@router.post("/api/companies", dependencies=[Depends(require_api_key)])
def add_company(body: CompanyIn, db: Session = Depends(get_session)):
    provider, board = body.provider.strip().lower(), body.board.strip()
    url = body.url.strip()
    if url:
        hit = detect_ats(url)
        provider, board = hit if hit else ("careers", url)
    if provider not in PROVIDERS or not board:
        raise HTTPException(422, "Give a careers/job URL, or a provider and board id.")
    if PROVIDERS[provider].kind == "feed":
        raise HTTPException(422, "Feeds are configured in candidate_profile.yaml -> sources.feeds.")
    existing = db.scalar(select(Company).where(Company.provider == provider, Company.board == board))
    if existing:
        existing.watch = True
        db.commit()
        return {"id": existing.id, "provider": provider, "board": board, "existing": True}
    c = Company(provider=provider, board=board, name=body.name.strip() or board, watch=True,
                discovered_by="user", careers_url=url)
    db.add(c)
    db.commit()
    return {"id": c.id, "provider": provider, "board": board, "existing": False}


@router.post("/api/companies/{cid}/watch", dependencies=[Depends(require_api_key)])
def watch_company(cid: int, watch: bool = True, db: Session = Depends(get_session)):
    c = db.get(Company, cid)
    if not c:
        raise HTTPException(404, "Company not found")
    c.watch = watch
    db.commit()
    return {"id": cid, "watch": watch}


@router.delete("/api/companies/{cid}", dependencies=[Depends(require_api_key)])
def delete_company(cid: int, db: Session = Depends(get_session)):
    c = db.get(Company, cid)
    if not c:
        raise HTTPException(404, "Company not found")
    db.delete(c)
    db.commit()
    return {"deleted": cid}


# ---------- portals (handoff) ---------------------------------------------- #
@router.get("/api/portals/links")
def portal_links(q: Optional[str] = None, location: str = "India"):
    """Ready-made searches on LinkedIn, Naukri, Indeed, ... for your target roles."""
    queries = [q] if q else search_queries(load_profile().raw().get("sources") or {})
    return search_links(queries, location)


# ---------- monitoring ----------------------------------------------------- #
@router.get("/api/monitoring")
def monitoring(db: Session = Depends(get_session)):
    total_jobs = db.scalar(select(func.count(Job.id))) or 0
    sightings = db.scalar(select(func.count(JobSource.id))) or 0
    multi = db.scalar(select(func.count()).select_from(
        select(JobSource.job_id).group_by(JobSource.job_id)
        .having(func.count(JobSource.id) > 1).subquery())) or 0
    per_source = [{"source": s, "listings": n} for s, n in db.execute(
        select(JobSource.source, func.count(JobSource.id)).group_by(JobSource.source)
        .order_by(desc(func.count(JobSource.id)))).all()]
    by_life: dict = {}
    for j in db.scalars(select(Job)):
        k = lifecycle_state(j)
        by_life[k] = by_life.get(k, 0) + 1
    last = db.get(KeyValue, "last_discovery")
    recent = db.scalars(select(Event).where(Event.agent.in_(["discovery", "verification"]))
                        .order_by(desc(Event.ts)).limit(15)).all()
    return {
        "jobs": total_jobs,
        "listings_seen": sightings,
        "duplicate_rate": round((sightings - total_jobs) / sightings, 3) if sightings else 0,
        "jobs_with_multiple_sources": multi,
        "per_source": per_source,
        "by_lifecycle": by_life,
        "eligible": sum(by_life.get(k, 0) for k in ("VERIFIED", "ELIGIBLE", "SHORTLISTED")),
        "prepared": by_life.get("READY_FOR_REVIEW", 0),
        "submitted_confirmed": by_life.get("SUBMITTED_CONFIRMED", 0),
        "submitted_unverified": by_life.get("SUBMISSION_UNVERIFIED", 0),
        "awaiting_action": by_life.get("ACTION_REQUIRED", 0),
        "provider_errors": [h for h in _health_rows(db) if h["consecutive_failures"]][:30],
        "last_discovery": last.value if last else {},
        "recent": [{"ts": str(e.ts), "agent": e.agent, "message": e.message} for e in recent],
    }
