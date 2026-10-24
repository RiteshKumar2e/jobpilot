"""End-to-end orchestrator tests with INJECTED jobs (no live HTTP)."""
import pytest
from app.orchestrator import Orchestrator, compute_stats
from app.schemas import RawJob
from app.database import SessionLocal
from app.models import (Job, ApplicationAttempt, ResumeVersion, Event,
                        AgentRun, KeyValue, ApplicationStatus)


@pytest.fixture(autouse=True)
def clean_db():
    """Each orchestrator test starts from an empty DB for deterministic counts."""
    db = SessionLocal()
    for model in (ApplicationAttempt, ResumeVersion, Event, AgentRun, Job):
        db.query(model).delete()
    db.query(KeyValue).delete()
    db.commit()
    db.close()
    yield


def _raw():
    return [
        RawJob(source="greenhouse", source_id="1", company="labco",
               title="Computer Vision Engineer",
               description="Entry level steel defect MobileNetV2 NEU-DET OpenCV PyTorch FastAPI python India",
               location="Jamshedpur, India", job_url="https://boards.greenhouse.io/labco/jobs/1"),
        RawJob(source="lever", source_id="2", company="bigco",
               title="Senior ML Engineer", description="8+ years lead team",
               location="India", job_url="https://jobs.lever.co/bigco/2"),
        # duplicate of #1
        RawJob(source="greenhouse", source_id="1", company="labco",
               title="Computer Vision Engineer", description="dup",
               location="India", job_url="https://boards.greenhouse.io/labco/jobs/1?utm_source=x"),
    ]


def test_full_cycle_safe_mode_no_submission():
    orch = Orchestrator()
    stats = orch.run_full_cycle(discover_raw=_raw())
    assert stats["discovered"] == 3
    assert stats["errors"] == 0
    # 0 submitted in SAFE mode
    assert stats["submitted"] == 0

    db = SessionLocal()
    try:
        # dedup: 2 unique jobs, not 3
        assert db.query(Job).count() == 2
        cve = db.query(Job).filter_by(title="Computer Vision Engineer").all()
        assert len(cve) == 1
        assert cve[0].application_status == ApplicationStatus.READY_FOR_REVIEW.value
        assert cve[0].tailored_resume_path  # resume generated
        # senior rejected
        snr = db.query(Job).filter_by(title="Senior ML Engineer").first()
        assert snr.eligibility_status == "REJECTED"
    finally:
        db.close()


def test_rerun_is_idempotent():
    orch = Orchestrator()
    orch.run_full_cycle(discover_raw=_raw())
    before = SessionLocal().query(Job).count()
    orch.run_full_cycle(discover_raw=_raw())
    after = SessionLocal().query(Job).count()
    assert before == after  # no duplicate rows on re-run


def test_disable_agent():
    orch = Orchestrator(enabled_agents={"discovery", "verification"})
    orch.run_full_cycle(discover_raw=_raw())
    db = SessionLocal()
    try:
        # matching disabled -> no scores
        scored = [j for j in db.query(Job).all() if j.match_score is not None]
        assert scored == []
    finally:
        db.close()


def test_compute_stats_shape():
    orch = Orchestrator()
    orch.run_full_cycle(discover_raw=_raw())
    db = SessionLocal()
    try:
        s = compute_stats(db)
        for k in ["discovered", "verified_eligible", "shortlisted", "prepared",
                  "submitted_confirmed", "rejected", "by_source", "by_status"]:
            assert k in s
    finally:
        db.close()
