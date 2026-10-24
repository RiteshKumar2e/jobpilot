from types import SimpleNamespace

from app.agents.browser_apply import BrowserApplyAgent, form_url
from app.agents.notifier import NotifierAgent
from app.agents.verification import VerificationAgent
from app.schemas import ApplicationMaterials, RawJob


def test_greenhouse_uses_hosted_form_url():
    gh = SimpleNamespace(source="greenhouse", source_id="123", company="databricks",
                         application_url="https://databricks.com/careers?gh_jid=123", job_url="")
    assert form_url(gh) == "https://job-boards.greenhouse.io/embed/job_app?for=databricks&token=123"
    lv = SimpleNamespace(source="lever", source_id="x", company="paytm",
                         application_url="https://jobs.lever.co/paytm/x/apply", job_url="")
    assert form_url(lv) == "https://jobs.lever.co/paytm/x/apply"


def test_title_lead_at_end_is_senior(profile):
    v = VerificationAgent(profile)
    r = v.verify(RawJob(source="lever", company="x", title="Backend- Technical Lead",
                        description="python", location="Bengaluru, India"))
    assert r.status == "REJECTED"
    ok = v.verify(RawJob(source="lever", company="x", title="Software Engineer I",
                         description="Leading-edge python work", location="Bengaluru, India"))
    assert ok.status != "REJECTED"


def test_autosubmit_needs_min_score(monkeypatch, profile):
    import app.config as cfg
    monkeypatch.setenv("JOBPILOT_APPROVAL_MODE", "RESTRICTED_AUTO")
    monkeypatch.setenv("JOBPILOT_AUTO_APPLY_ALLOWLIST", "greenhouse,lever")
    monkeypatch.setenv("JOBPILOT_DAILY_AUTOSUBMIT_LIMIT", "5")
    monkeypatch.setenv("JOBPILOT_MIN_AUTOSUBMIT_SCORE", "55")
    cfg._settings = None
    try:
        ba = BrowserApplyAgent(profile)
        mats = ApplicationMaterials(unknown_fields=["phone"])  # phone is checked live, not here
        job = SimpleNamespace(source="lever", company="x", eligibility_status="VERIFIED",
                              match_score=70)
        assert ba.may_autosubmit(job, mats, 0)[0] is True
        job.match_score = 50
        assert ba.may_autosubmit(job, mats, 0)[0] is False
        no_email = ApplicationMaterials(unknown_fields=["email"])
        job.match_score = 70
        assert ba.may_autosubmit(job, no_email, 0)[0] is False
    finally:
        cfg._settings = None


def test_answer_rules_use_profile_facts_only(profile):
    ba = BrowserApplyAgent(profile)
    rules = ba.answer_rules(None)

    def answer(label):
        return next((v for rx, v in rules if rx.search(label)), "")

    assert answer("First Name") == "Ritesh"
    assert answer("Last Name") == "Kumar"
    assert answer("LinkedIn Profile URL") .startswith("https://www.linkedin.com/")
    # nothing in the profile answers this -> left empty (=> ACTION_REQUIRED, never guessed)
    assert answer("Describe a production ML system you built") == ""


def test_notifier_skips_without_smtp_and_sends_with_it(monkeypatch):
    import app.config as cfg
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p): sent.append(("login", u))
        def send_message(self, m): sent.append(("send", m["To"], m["Subject"]))

    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("JOBPILOT_SMTP_PASSWORD", "")  # ignore the real backend/.env
    cfg._settings = None
    try:
        assert NotifierAgent().send("x", "y") is False  # no SMTP password -> skipped
        monkeypatch.setenv("JOBPILOT_NOTIFY_EMAIL", "me@example.com")
        monkeypatch.setenv("JOBPILOT_SMTP_USER", "bot@example.com")
        monkeypatch.setenv("JOBPILOT_SMTP_PASSWORD", "app-pass")
        cfg._settings = None
        assert NotifierAgent().send("Applied: X", "body") is True
        assert ("send", "me@example.com", "Applied: X") in sent
    finally:
        cfg._settings = None
