"""Multi-source pipeline: failure isolation, circuit breaker, company discovery,
cross-source dedup, closed listings, migration, lifecycle states, the new API
endpoints and URL import. No network (fake providers / fake HTTP)."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.agents.base import dedup_key, normalize_company
from app.agents.discovery import DiscoveryAgent
from app.config import get_settings
from app.database import SessionLocal, add_missing_columns
from app.models import (AgentRun, ApplicationAttempt, ApplicationStatus, Company, Event, Job,
                        JobSource, KeyValue, ProviderHealth, ResumeVersion)
from app.orchestrator import Orchestrator, lifecycle_state
from app.providers import PROVIDERS
from app.providers.base import FetchResult, KIND_ATS, KIND_FEED, Provider
from app.schemas import RawJob


@pytest.fixture(autouse=True)
def clean_db():
    db = SessionLocal()
    for model in (ApplicationAttempt, ResumeVersion, Event, AgentRun, JobSource, Job,
                  Company, ProviderHealth, KeyValue):
        db.query(model).delete()
    db.commit()
    db.close()
    yield


class FakeProvider(Provider):
    def __init__(self, name, kind=KIND_ATS, jobs=None, error=None, complete=True, boards=()):
        self.name, self.kind = name, kind
        self.jobs, self.error, self.complete, self.boards = jobs or [], error, complete, boards
        self.calls = 0

    def fetch(self, target, ctx):
        self.calls += 1
        if self.error:
            raise self.error
        return FetchResult(jobs=list(self.jobs), complete=self.complete,
                           discovered_boards=list(self.boards))


def _job(source, sid, title="Machine Learning Engineer", company="Acme", loc="Bengaluru, India",
         board="acme", **kw):
    return RawJob(source=source, source_id=sid, company=company, title=title, location=loc,
                  board=board, description="Entry level. Python, PyTorch.",
                  job_url=f"https://{source}.example/{board}/{sid}", **kw)


@pytest.fixture
def fake_sources(monkeypatch):
    """Replace the provider registry with fakes; profile sources point at them."""
    good = FakeProvider("goodats", jobs=[_job("goodats", "1")])
    bad = FakeProvider("badats", error=RuntimeError("boom"))
    feed = FakeProvider("fakefeed", kind=KIND_FEED, jobs=[_job("fakefeed", "f1", company="Other",
                                                              board="")],
                        complete=False)
    # "ashby" exists so boards discovered in feed listings can be registered
    reg = {"goodats": good, "badats": bad, "fakefeed": feed, "ashby": FakeProvider("ashby")}
    monkeypatch.setattr("app.agents.discovery.PROVIDERS", reg)
    monkeypatch.setattr("app.providers.PROVIDERS", reg)

    def fake_targets(raw_sources, enabled):
        from app.providers.base import Target
        return [Target("goodats", "acme"), Target("badats", "broken"), Target("fakefeed", "ml")]
    monkeypatch.setattr("app.agents.discovery.profile_targets", fake_targets)
    return reg


# ---- discovery: isolation, health, breaker, company discovery -------------- #
def test_one_failing_source_does_not_stop_others(profile, fake_sources):
    agent = DiscoveryAgent(profile)
    db = SessionLocal()
    try:
        jobs = agent.discover(db)
        assert {j.source for j in jobs} == {"goodats", "fakefeed"}
        rep = {r["key"]: r for r in agent.last_report}
        assert rep["badats:broken"]["status"] == "error" and "boom" in rep["badats:broken"]["error"]
        assert rep["goodats:acme"]["complete"] and not rep["fakefeed:ml"]["complete"]
        h = db.get(ProviderHealth, "badats:broken")
        assert h.consecutive_failures == 1 and h.disabled_until is None
        assert db.get(ProviderHealth, "goodats:acme").jobs_last == 1
    finally:
        db.close()


def test_circuit_breaker_cools_down_and_recovers(profile, fake_sources):
    agent = DiscoveryAgent(profile)
    db = SessionLocal()
    try:
        for _ in range(get_settings().breaker_threshold):
            agent.discover(db)
        h = db.get(ProviderHealth, "badats:broken")
        assert h.disabled_until and h.disabled_until > datetime.utcnow()
        calls = fake_sources["badats"].calls
        agent.discover(db)                                  # cooling down: not called
        assert fake_sources["badats"].calls == calls
        assert any(r["status"] == "cooling_down" for r in agent.last_report)
        # after the cool-down it is retried, and a success resets it
        h.disabled_until = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
        fake_sources["badats"].error = None
        agent.discover(db)
        db.refresh(h)
        assert fake_sources["badats"].calls == calls + 1
        assert h.consecutive_failures == 0 and h.disabled_until is None
    finally:
        db.close()


def test_company_discovery_from_feed_links(profile, fake_sources):
    fake_sources["fakefeed"].jobs = [_job("fakefeed", "f2", company="Sarvam",
                                          application_url="https://jobs.ashbyhq.com/sarvam/abc")]
    agent = DiscoveryAgent(profile)
    db = SessionLocal()
    try:
        agent.discover(db)
        c = db.query(Company).filter_by(provider="ashby", board="sarvam").one()
        assert c.watch and c.discovered_by == "fakefeed:ml" and c.name == "Sarvam"
        # profile boards are registered too; no duplicates on a second run
        agent.discover(db)
        assert db.query(Company).filter_by(provider="ashby", board="sarvam").count() == 1
    finally:
        db.close()


def test_registry_companies_become_targets(profile, fake_sources):
    db = SessionLocal()
    try:
        db.add(Company(provider="goodats", board="newco", name="NewCo", watch=True))
        db.add(Company(provider="goodats", board="muted", name="Muted", watch=False))
        db.commit()
        keys = [t.key for t in DiscoveryAgent(profile).targets(db)]
        assert "goodats:newco" in keys and "goodats:muted" not in keys
    finally:
        db.close()


# ---- dedup ----------------------------------------------------------------- #
def test_normalizers():
    assert normalize_company("Razorpay Software Private Limited") == "razorpay"
    assert normalize_company("razorpaysoftwareprivatelimited") == "razorpay"
    assert dedup_key("Acme Inc.", "ML Engineer (Remote)", "Bangalore, Karnataka") == \
        dedup_key("acme", "ml engineer", "Bengaluru, India")
    assert dedup_key("Acme", "ML Engineer", "Pune") != dedup_key("Acme", "ML Engineer", "Delhi")


def test_cross_source_duplicate_is_one_job_with_two_sources():
    orch = Orchestrator(enabled_agents={"discovery"})
    feed_copy = RawJob(source="remotive", source_id="r1", company="Acme Inc", title="ML Engineer",
                       location="Bengaluru, India", job_url="https://remotive.com/r1",
                       description="from the feed")
    ats_copy = RawJob(source="greenhouse", source_id="77", company="acme", board="acme",
                      title="ML Engineer", location="Bangalore", description="from the ATS",
                      job_url="https://boards.greenhouse.io/acme/jobs/77")
    orch.run_full_cycle(discover_raw=[feed_copy])
    orch.run_full_cycle(discover_raw=[ats_copy])
    orch.run_full_cycle(discover_raw=[feed_copy, ats_copy])          # re-runs stay idempotent
    db = SessionLocal()
    try:
        jobs = db.query(Job).all()
        assert len(jobs) == 1
        job = jobs[0]
        # the company's own ATS posting became the primary (it can be applied to)
        assert job.source == "greenhouse" and job.board == "acme" and job.company == "acme"
        assert job.description == "from the ATS"
        assert sorted(s.source for s in db.query(JobSource).filter_by(job_id=job.job_id)) == \
            ["greenhouse", "remotive"]
    finally:
        db.close()


def test_same_title_twice_on_one_board_stays_two_jobs():
    orch = Orchestrator(enabled_agents={"discovery"})
    a = RawJob(source="greenhouse", source_id="1", company="acme", title="Software Engineer",
               location="Bengaluru", job_url="https://boards.greenhouse.io/acme/jobs/1")
    b = a.model_copy(update={"source_id": "2", "job_url": "https://boards.greenhouse.io/acme/jobs/2"})
    orch.run_full_cycle(discover_raw=[a, b])
    db = SessionLocal()
    try:
        assert db.query(Job).count() == 2
    finally:
        db.close()


def test_applied_job_is_not_switched_or_duplicated():
    orch = Orchestrator(enabled_agents={"discovery"})
    feed = RawJob(source="remotive", source_id="r9", company="Beta", title="AI Engineer",
                  location="Remote", job_url="https://remotive.com/r9")
    orch.run_full_cycle(discover_raw=[feed])
    db = SessionLocal()
    job = db.query(Job).one()
    job.application_status = ApplicationStatus.SUBMISSION_UNVERIFIED.value
    db.commit()
    db.close()
    ats = RawJob(source="lever", source_id="L9", company="beta", board="beta", title="AI Engineer",
                 location="Remote", job_url="https://jobs.lever.co/beta/L9")
    orch.run_full_cycle(discover_raw=[ats])
    db = SessionLocal()
    try:
        assert db.query(Job).count() == 1          # never a second application target
        assert db.query(Job).one().source == "remotive"
    finally:
        db.close()


# ---- closed listings ------------------------------------------------------- #
def test_job_missing_from_complete_board_is_closed():
    orch = Orchestrator(enabled_agents={"discovery", "verification"})
    a = _job("greenhouse", "1", board="acme", company="acme")
    b = _job("greenhouse", "2", title="AI Engineer", board="acme", company="acme")
    orch.run_full_cycle(discover_raw=[a, b])
    orch.discovery.last_report = [{"provider": "greenhouse", "target": "acme", "status": "ok",
                                   "complete": True, "source_ids": ["1"]}]
    db = SessionLocal()
    try:
        assert orch._close_missing(db) == 1
        closed = db.query(Job).filter_by(source_id="2").one()
        assert closed.eligibility_status == "EXPIRED" and lifecycle_state(closed) == "CLOSED"
        # a partial list (feed / page cap) or an empty response closes nothing
        orch.discovery.last_report = [{"provider": "greenhouse", "target": "acme", "status": "ok",
                                       "complete": False, "source_ids": []}]
        assert orch._close_missing(db) == 0
    finally:
        db.close()


def test_closure_uses_full_board_list_not_filtered_titles():
    """A stored job whose title no longer passes the filter is still on the
    board — it must not be marked closed."""
    from app.providers.ats import _board
    from app.providers.base import FetchContext, Target
    jobs = [_job("greenhouse", "1"), _job("greenhouse", "2", title="Account Executive")]
    res = _board(jobs, Target("greenhouse", "acme"), FetchContext(
        http=None, title_ok=lambda t: "engineer" in t.lower()))
    assert [j.source_id for j in res.jobs] == ["1"] and res.listed_ids == ["1", "2"]


def test_job_cap_is_spread_across_sources():
    from app.agents.discovery import _round_robin
    big = [_job("greenhouse", str(i)) for i in range(100)]
    small = [_job("remotive", f"r{i}") for i in range(3)]
    kept = _round_robin([big, small], 10)
    assert len(kept) == 10 and sum(j.source == "remotive" for j in kept) == 3
    assert len(_round_robin([big, small], 1000)) == 103


# ---- lifecycle ------------------------------------------------------------- #
def test_lifecycle_states():
    j = Job(job_id="x", source="s", company="c", title="t",
            eligibility_status="UNCHECKED", application_status="NONE")
    assert lifecycle_state(j) == "DISCOVERED"
    j.eligibility_status = "VERIFIED"
    assert lifecycle_state(j) == "VERIFIED"
    j.match_score = 30
    assert lifecycle_state(j) == "ELIGIBLE"
    j.match_score = 70
    assert lifecycle_state(j) == "SHORTLISTED"
    for app_status, want in (("READY_FOR_REVIEW", "READY_FOR_REVIEW"), ("APPROVED", "READY_FOR_REVIEW"),
                             ("ACTION_REQUIRED", "ACTION_REQUIRED"),
                             ("SUBMISSION_UNVERIFIED", "SUBMISSION_UNVERIFIED"),
                             ("SUBMITTED_CONFIRMED", "SUBMITTED_CONFIRMED"),
                             ("REJECTED_BY_USER", "REJECTED"), ("FAILED", "FAILED")):
        j.application_status = app_status
        assert lifecycle_state(j) == want


# ---- migration ------------------------------------------------------------- #
def test_migration_adds_columns_to_an_old_database(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, job_id VARCHAR(40), "
                       "source VARCHAR(40), company VARCHAR(200), title VARCHAR(300))"))
        c.execute(text("INSERT INTO jobs (job_id, source, company, title) "
                       "VALUES ('a', 'greenhouse', 'acme', 'SE')"))
    assert sorted(add_missing_columns(eng)) == ["jobs.board", "jobs.dedup_key", "jobs.last_seen_at"]
    cols = {c["name"] for c in inspect(eng).get_columns("jobs")}
    assert {"board", "dedup_key", "last_seen_at"} <= cols
    with eng.begin() as c:                                      # old rows kept
        assert c.execute(text("SELECT title FROM jobs")).scalar() == "SE"
    assert add_missing_columns(eng) == []                       # idempotent


# ---- API ------------------------------------------------------------------- #
@pytest.fixture
def client():
    from app.api.deps import set_runtime
    from app.api.routes import router
    from app.api.sources_routes import router as sources_router
    app = FastAPI()
    app.include_router(router)
    app.include_router(sources_router)
    orch = Orchestrator(enabled_agents={"discovery", "verification", "matching"})
    orch.is_running = lambda: True   # imports don't start a background run during tests
    set_runtime(orch, None, None)
    c = TestClient(app)
    c.headers["X-API-Key"] = get_settings().api_key
    return c


def test_sources_endpoint_lists_capabilities(client):
    r = client.get("/api/sources").json()
    by = {p["name"]: p for p in r["providers"]}
    assert by["greenhouse"]["apply_mode"] == "auto" and by["ashby"]["kind"] == "ats"
    assert by["linkedin"]["status"] == "handoff"
    assert by["adzuna"]["needs_key"] is True


def test_company_registry_crud(client):
    r = client.post("/api/companies", json={"url": "https://jobs.ashbyhq.com/sarvam"}).json()
    assert (r["provider"], r["board"], r["existing"]) == ("ashby", "sarvam", False)
    again = client.post("/api/companies", json={"url": "https://jobs.ashbyhq.com/sarvam/xyz"}).json()
    assert again["existing"] is True
    other = client.post("/api/companies", json={"url": "https://acme.ai/careers", "name": "Acme"}).json()
    assert other["provider"] == "careers"
    rows = client.get("/api/companies").json()
    assert {c["provider"] for c in rows} == {"ashby", "careers"}
    assert client.post(f"/api/companies/{r['id']}/watch?watch=false").json()["watch"] is False
    assert client.delete(f"/api/companies/{other['id']}").status_code == 200
    assert client.post("/api/companies", json={}).status_code == 422


def test_jobs_endpoint_filters_and_sources(client):
    orch = Orchestrator(enabled_agents={"discovery"})
    orch.run_full_cycle(discover_raw=[
        RawJob(source="remotive", source_id="r1", company="Acme", title="ML Engineer",
               location="Remote", work_mode="remote", job_url="https://remotive.com/r1"),
        RawJob(source="lever", source_id="L1", company="acme", board="acme", title="ML Engineer",
               location="Remote", work_mode="remote", job_url="https://jobs.lever.co/acme/L1"),
        RawJob(source="lever", source_id="L2", company="acme", board="acme", title="Backend Engineer",
               location="Pune, India", work_mode="onsite", job_url="https://jobs.lever.co/acme/L2",
               salary_range="INR 10-12 LPA"),
    ])
    all_jobs = client.get("/api/jobs").json()
    assert len(all_jobs) == 2
    ml = next(j for j in all_jobs if j["title"] == "ML Engineer")
    assert {s["source"] for s in ml["sources"]} == {"remotive", "lever"} and ml["lifecycle"]
    # a source filter finds jobs merged from that source too
    assert [j["title"] for j in client.get("/api/jobs?source=remotive").json()] == ["ML Engineer"]
    assert [j["title"] for j in client.get("/api/jobs?work_mode=onsite").json()] == ["Backend Engineer"]
    assert [j["title"] for j in client.get("/api/jobs?salary_disclosed=true").json()] == ["Backend Engineer"]
    assert [j["title"] for j in client.get("/api/jobs?location=pune").json()] == ["Backend Engineer"]
    assert client.get("/api/jobs?lifecycle=DISCOVERED").json()
    mon = client.get("/api/monitoring").json()
    assert mon["jobs"] == 2 and mon["listings_seen"] == 3 and mon["jobs_with_multiple_sources"] == 1


def test_portal_links_endpoint(client):
    r = client.get("/api/portals/links?q=ai engineer").json()
    assert r[0]["query"] == "ai engineer" and "linkedin" in r[0]["links"]


def test_import_portal_url_requires_details_then_hands_off(client, monkeypatch):
    monkeypatch.setattr("app.importer.fetch_listing", lambda url, http=None: (None, None))
    url = "https://www.linkedin.com/jobs/view/4242"
    r = client.post("/api/jobs/import", json={"url": url})
    assert r.status_code == 422 and "Linkedin" in r.json()["detail"]
    r = client.post("/api/jobs/import", json={"url": url, "title": "AI Engineer", "company": "Acme",
                                              "location": "Bengaluru", "description": "Python"})
    assert r.status_code == 200 and r.json()["how"] == "manual"
    db = SessionLocal()
    try:
        job = db.query(Job).filter_by(job_id=r.json()["job_id"]).one()
        assert job.source == "linkedin" and job.application_url == url
    finally:
        db.close()


def test_import_ats_url_registers_company(client, monkeypatch):
    found = RawJob(source="lever", source_id="abc", company="acme", board="acme",
                   title="AI Engineer", location="Bengaluru",
                   job_url="https://jobs.lever.co/acme/abc")
    monkeypatch.setattr("app.importer.fetch_listing",
                        lambda url, http=None: (found, ("lever", "acme")))
    r = client.post("/api/jobs/import", json={"url": "https://jobs.lever.co/acme/abc/apply"})
    assert r.status_code == 200 and r.json()["how"] == "ats"
    db = SessionLocal()
    try:
        assert db.query(Company).filter_by(provider="lever", board="acme", watch=True).count() == 1
    finally:
        db.close()


def test_approve_never_submits_twice(client):
    orch = Orchestrator(enabled_agents={"discovery"})
    orch.run_full_cycle(discover_raw=[RawJob(source="greenhouse", source_id="9", company="acme",
                                             title="SE", job_url="https://boards.greenhouse.io/acme/jobs/9")])
    db = SessionLocal()
    job = db.query(Job).one()
    job.application_status = ApplicationStatus.SUBMITTED_CONFIRMED.value
    db.commit()
    jid = job.job_id
    db.close()
    r = client.post(f"/api/jobs/{jid}/approve").json()
    assert r["status"] == "already_submitted"
