from app.schemas import RawJob, ApplicationMaterials
from app.agents.matching import MatchingAgent
from app.agents.resume_intel import ResumeIntelAgent
from app.agents.browser_apply import BrowserApplyAgent
from app.database import SessionLocal


class _Job:
    """Lightweight job stand-in with attributes the agents read."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_tailored_resume_flags_missing_file_and_unverified(profile):
    m = MatchingAgent(profile)
    ri = ResumeIntelAgent(profile)
    job = RawJob(source="greenhouse", company="labco", title="Computer Vision Engineer",
                 description="steel defect MobileNetV2 opencv pytorch fastapi india entry level",
                 location="India")
    sr = m.score(job, "VERIFIED")
    j = _Job(**{k: getattr(job, k) for k in job.model_fields}, job_id="t1")
    db = SessionLocal()
    try:
        res = ri.tailor_for_job(db, j, sr)
        assert res["pdf_path"]
        # with no resume file present, must flag it
        assert any("No base resume" in f for f in res["flags"])
    finally:
        db.close()


def test_safe_mode_blocks_autosubmit(profile):
    ba = BrowserApplyAgent(profile)  # SAFE mode from conftest env
    job = _Job(source="greenhouse", company="labco", eligibility_status="VERIFIED")
    mats = ApplicationMaterials(unknown_fields=[])
    allow, why = ba.may_autosubmit(job, mats, 0)
    assert allow is False
    assert "SAFE" in why


def test_autosubmit_requires_allowlist(monkeypatch, profile):
    import app.config as cfg
    monkeypatch.setenv("JOBPILOT_APPROVAL_MODE", "RESTRICTED_AUTO")
    monkeypatch.setenv("JOBPILOT_AUTO_APPLY_ALLOWLIST", "greenhouse")
    monkeypatch.setenv("JOBPILOT_DAILY_AUTOSUBMIT_LIMIT", "5")
    cfg._settings = None
    ba = BrowserApplyAgent(profile)

    ok_job = _Job(source="greenhouse", company="labco", eligibility_status="VERIFIED")
    bad_src = _Job(source="indeed", company="x", eligibility_status="VERIFIED")
    unverified = _Job(source="greenhouse", company="labco", eligibility_status="NEEDS_REVIEW")
    mats = ApplicationMaterials(unknown_fields=[])

    assert ba.may_autosubmit(ok_job, mats, 0)[0] is True
    assert ba.may_autosubmit(bad_src, mats, 0)[0] is False
    assert ba.may_autosubmit(unverified, mats, 0)[0] is False
    # over cap
    assert ba.may_autosubmit(ok_job, mats, 5)[0] is False

    cfg._settings = None  # reset for other tests
