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


def test_remote_open_to_india_is_verified(profile):
    v = VerificationAgent(profile)
    for loc in ("Remote", "Remote - Anywhere", "Remote, APAC", "Remote - India",
                "Bangalore, India; Remote, Poland", ""):
        r = v.verify(mk("AI Engineer", "Python.", loc, "remote"))
        assert r.status == EligibilityStatus.VERIFIED.value, loc


def test_remote_limited_to_another_country_rejected(profile):
    v = VerificationAgent(profile)
    for loc in ("Remote - US", "Remote, Europe", "Remote - Spain",
                "Remote, Canada; Remote, Poland", "San Francisco - SF9"):
        r = v.verify(mk("AI Engineer", "Python.", loc, "remote"))
        assert r.status == EligibilityStatus.REJECTED.value, loc


def test_non_entry_level_titles_rejected(profile):
    v = VerificationAgent(profile)
    for title in ("SDE II", "Software Engineer 3", "Software Engineer L2-Messaging API",
                  "Intermediate Backend Engineer, India", "Team Leader",
                  "Electrical Engineering Co-Op", "Operations Associate, Apprenticeship"):
        r = v.verify(mk(title, "Python.", "Bengaluru, India", "onsite"))
        assert r.status == EligibilityStatus.REJECTED.value, title


def test_off_target_roles_rejected(profile):
    v = VerificationAgent(profile)
    for title in ("Data Analyst, Business Finance", "Technical Support Engineer",
                  "UX Engineer", "People Systems Engineer", "Jira Administrator",
                  "Sales Support Associate", "Account Development Representative"):
        r = v.verify(mk(title, "Python.", "Bengaluru, India", "onsite"))
        assert r.status == EligibilityStatus.REJECTED.value, title
    for title in ("SDE I", "AI Agent Engineer", "Machine Learning Engineer",
                  "Software Engineer - Cloud: Azure", "Associate Software Engineer"):
        r = v.verify(mk(title, "Python.", "Bengaluru, India", "onsite"))
        assert r.status == EligibilityStatus.VERIFIED.value, title


def test_experience_in_yrs_and_ranges(profile):
    v = VerificationAgent(profile)
    assert v.verify(mk("Product Engineer | Experience : 6-9 yrs", "Python.",
                       "Bangalore", "onsite")).status == EligibilityStatus.REJECTED.value
    assert v.verify(mk("Software Engineer", "Needs 1 to 3 years of Python.",
                       "Bangalore", "onsite")).status == EligibilityStatus.VERIFIED.value


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


def test_scam_listings_rejected(profile):
    v = VerificationAgent(profile)
    for desc in ("Freshers welcome. Pay a refundable security deposit of Rs 2000.",
                 "Registration fee of 499 applies after selection.",
                 "Send your resume on WhatsApp to 98xxxxxx.",
                 "Join our Telegram channel t.me/jobs for the interview."):
        r = v.verify(mk("Python Developer", desc, "Bengaluru, India", "onsite"))
        assert r.status == EligibilityStatus.REJECTED.value and "scam" in r.reasons[0].lower(), desc
    # building WhatsApp / Telegram products is a normal job
    ok = v.verify(mk("Backend Engineer", "Build integrations with the WhatsApp Business API and "
                     "Telegram bots in Python.", "Bengaluru, India", "onsite"))
    assert ok.status == EligibilityStatus.VERIFIED.value


def test_no_location_needs_review(profile):
    v = VerificationAgent(profile)
    r = v.verify(mk("AI Engineer", "Python.", "", ""))
    assert r.status == EligibilityStatus.NEEDS_REVIEW.value
