from app.agents.verification import VerificationAgent
from app.schemas import RawJob
from app.models import EligibilityStatus


def mk(title, desc, loc="", wm=""):
    return RawJob(source="greenhouse", company="x", title=title,
                  description=desc, location=loc, work_mode=wm)


def test_fresher_india_fulltime_is_verified(profile):
    v = VerificationAgent(profile)
    r = v.verify(mk("Machine Learning Engineer",
                    "Entry level role for freshers. Python, PyTorch.", "Bengaluru, India"))
    assert r.status == EligibilityStatus.VERIFIED.value


def test_senior_title_rejected(profile):
    v = VerificationAgent(profile)
    r = v.verify(mk("Senior ML Engineer", "Lead the team.", "India"))
    assert r.status == EligibilityStatus.REJECTED.value
    assert any("senior" in x.lower() for x in r.reasons)


def test_internship_rejected(profile):
    v = VerificationAgent(profile)
    r = v.verify(mk("AI Engineer Intern", "Summer internship.", "India"))
    assert r.status == EligibilityStatus.REJECTED.value


def test_high_experience_requirement_rejected(profile):
    v = VerificationAgent(profile)
    r = v.verify(mk("ML Engineer", "Minimum 6 years experience required.", "India"))
    assert r.status == EligibilityStatus.REJECTED.value


def test_experience_nice_to_have_not_rejected(profile):
    v = VerificationAgent(profile)
    # "5 years ... preferred" should NOT auto-reject
    r = v.verify(mk("ML Engineer", "Freshers welcome. 5 years preferred but not required.", "India"))
    assert r.status != EligibilityStatus.REJECTED.value


def test_india_exclusion_rejected(profile):
    v = VerificationAgent(profile)
    r = v.verify(mk("Python Developer", "Remote. US citizens only.", "Remote", "remote"))
    assert r.status == EligibilityStatus.REJECTED.value


def test_remote_anywhere_is_verified(profile):
    v = VerificationAgent(profile)
    for loc in ("Remote", "Remote - US", "Remote, Europe"):
        r = v.verify(mk("AI Engineer", "Python.", loc, "remote"))
        assert r.status == EligibilityStatus.VERIFIED.value, loc


def test_onsite_and_hybrid_india_verified(profile):
    v = VerificationAgent(profile)
    for loc, wm in (("Bengaluru, Karnataka", "onsite"), ("Gurugram", "hybrid"),
                    ("Hybrid - Pune", "hybrid"), ("New York; Hyderabad, India", "onsite")):
        r = v.verify(mk("AI Engineer", "Python.", loc, wm))
        assert r.status == EligibilityStatus.VERIFIED.value, loc


def test_onsite_or_hybrid_outside_india_rejected(profile):
    v = VerificationAgent(profile)
    for loc, wm in (("San Francisco, CA", "onsite"), ("London (Hybrid)", "hybrid"),
                    ("Singapore", "onsite")):
        r = v.verify(mk("AI Engineer", "Offices in India and the US. Python.", loc, wm))
        assert r.status == EligibilityStatus.REJECTED.value, loc


def test_no_location_needs_review(profile):
    v = VerificationAgent(profile)
    r = v.verify(mk("AI Engineer", "Python.", "", ""))
    assert r.status == EligibilityStatus.NEEDS_REVIEW.value
