"""Answer Bank, Inbox agent, outcomes, interview coach, LinkedIn kit,
follow-ups, weekly review and the spot-check endpoints. No network, no LLM."""
import shutil
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import coach
from app.agents.inbox import InboxAgent, calendar_link, classify, match_job, next_slots
from app.answer_bank import add_answer, coverage, pattern_for, pending
from app.config import get_settings
from app.database import SessionLocal
from app.models import (ApplicationAttempt, ApplicationStatus, InboxMessage, Interview, Job,
                        JobSource)


@pytest.fixture(autouse=True)
def clean_db():
    db = SessionLocal()
    for m in (InboxMessage, Interview, ApplicationAttempt, JobSource, Job):
        db.query(m).delete()
    db.commit()
    db.close()
    yield


def _add_job(db, jid="j1", company="DevRev", title="Solution Engineer", status="SUBMISSION_UNVERIFIED",
             score=70.0, submitted_days_ago=1, **kw):
    j = Job(job_id=jid, source="greenhouse", source_id=jid, company=company, title=title,
            description="Python FastAPI Docker AWS Kubernetes React", location="Bengaluru",
            eligibility_status="VERIFIED", application_status=status, match_score=score,
            submission_date=(datetime.now(timezone.utc) - timedelta(days=submitted_days_ago)).isoformat(),
            job_url=f"https://x/{jid}", **kw)
    db.add(j)
    db.commit()
    return j


# ---- Answer Bank ------------------------------------------------------------ #
def test_add_answer_keeps_comments_and_parses(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("candidate:\n  name: X\n\napplication_answers:\n"
                 "  - {match: \"notice\", answer: \"Immediate\"}\n"
                 "  # a comment that must survive\n\nlocations:\n  relocate: true\n", encoding="utf-8")
    rule = add_answer("Can you commute to Whitefield, Bengaluru?", "Yes", path=p)
    text = p.read_text(encoding="utf-8")
    assert "# a comment that must survive" in text
    data = yaml.safe_load(text)
    assert data["locations"]["relocate"] is True
    assert {"match": rule["match"], "answer": "Yes"} in data["application_answers"]
    import re
    assert re.search(rule["match"], "can you commute to whitefield,   bengaluru", re.I)
    with pytest.raises(ValueError):
        add_answer("q", "", path=p)
    with pytest.raises(ValueError):
        add_answer("q", "a", pattern="(unclosed", path=p)


def test_add_answer_creates_block_when_missing(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("candidate:\n  name: X\n", encoding="utf-8")
    add_answer("Notice period", "Immediate", path=p)
    assert yaml.safe_load(p.read_text())["application_answers"][0]["answer"] == "Immediate"


def test_pattern_for():
    assert pattern_for("  Notice Period?  ") == r"notice\s+period"


def test_pending_groups_unanswered_questions(profile):
    db = SessionLocal()
    try:
        for i in (1, 2):
            _add_job(db, jid=f"p{i}", status="ACTION_REQUIRED")
            db.add(ApplicationAttempt(job_id=f"p{i}", result="ACTION_REQUIRED",
                                      detail={"missing": ["Do you hold a valid driving licence?",
                                                          "Phone", "unnamed field"]}))
        db.commit()
        rows = pending(db, profile)
        assert [r["question"] for r in rows] == ["Do you hold a valid driving licence?"]  # Phone is answered
        assert rows[0]["count"] == 2 and len(rows[0]["jobs"]) == 2
    finally:
        db.close()


def test_coverage_reports_answered_topics(profile):
    cov = {c["topic"]: c["answer"] for c in coverage(profile)}
    assert cov["Notice period"] == "Immediate" and cov["Sponsorship"] == "No"


# ---- Inbox ------------------------------------------------------------------ #
@pytest.mark.parametrize("subject,body,want", [
    ("Your application to DevRev", "Thank you for applying to DevRev!", "application_received"),
    ("Interview — Solution Engineer", "We'd like to schedule an interview. Please share your availability.", "interview"),
    ("DevRev update", "Unfortunately we have decided to move forward with other candidates.", "rejection"),
    ("Next step: coding test", "Please complete the HackerRank assessment by Friday.", "assessment"),
    ("Security code for your application", "Enter the code 123456 to submit.", "verification_code"),
    ("Offer", "We are pleased to offer you the role.", "offer"),
    ("Weekend sale", "50% off shoes", ""),
])
def test_classify(subject, body, want):
    assert classify(subject, body, "someone@company.com") == want


def _msg(subject, body, sender="Priya <priya@devrev.ai>", mid=None):
    return {"message_id": mid or f"<{subject}@x>", "date": datetime.now(timezone.utc),
            "sender": sender, "subject": subject, "body": body}


def test_inbox_process_updates_jobs_and_never_sends(profile):
    db = SessionLocal()
    try:
        _add_job(db, "d1")
        agent = InboxAgent(profile)
        stats = agent.process(db, [
            _msg("Thanks for applying to DevRev", "We have received your application for Solution Engineer."),
            _msg("DevRev interview", "We'd like to schedule an interview. Share your availability."),
            _msg("Big sale", "Shop now", sender="Shop <deals@shop.com>"),
            _msg("Daily job alert", "New roles hiring", sender="LinkedIn <jobalerts-noreply@linkedin.com>"),
        ])
        job = db.query(Job).filter_by(job_id="d1").one()
        assert job.application_status == "SUBMITTED_CONFIRMED"      # email = real evidence
        assert job.outcome == "INTERVIEW_INVITE" and "Priya" in job.recruiter
        msgs = db.query(InboxMessage).all()
        assert {m.category for m in msgs} == {"application_received", "interview"}  # sale/alert not stored
        invite = next(m for m in msgs if m.category == "interview")
        assert "Hi Priya" in invite.draft_reply and "IST" in invite.draft_reply
        assert invite.calendar_url.startswith("https://calendar.google.com/calendar/render?action=TEMPLATE")
        assert stats["confirmed"] == 1 and stats["matched"] == 2
        # idempotent: same messages again are skipped; outcome never moves backwards
        agent.process(db, [_msg("DevRev interview", "schedule an interview")])
        assert db.query(InboxMessage).count() == 2
        agent.process(db, [_msg("DevRev - quick note", "Thanks for your interest in the role",
                                mid="<later@x>")])
        db.refresh(job)
        assert job.outcome == "INTERVIEW_INVITE"
    finally:
        db.close()


def test_inbox_real_world_cases(profile):
    """Regressions from a live mailbox run (wording paraphrased)."""
    # a confirmation that says "IF you are not selected" is not a rejection
    assert classify("Thank you for applying to Observe.AI",
                    "We'll review your qualifications. If you are not selected for this "
                    "position, keep an eye on our jobs page.", "x@greenhouse-mail.io") == "application_received"
    assert classify("Update", "After careful review, we have decided not to move forward with "
                    "your application.", "x@y.com") == "rejection"
    db = SessionLocal()
    try:
        _add_job(db, "hr", company="hackerrank", title="Deep Learning Research Engineer",
                 status="READY_FOR_REVIEW")
        _add_job(db, "cb", company="coinbase", title="Machine Learning Engineer", status="ACTION_REQUIRED")
        InboxAgent(profile).process(db, [
            # HackerRank test sent for some other company: not the HackerRank job
            _msg("Your HackerRank test invite", "Complete the assessment for Acme by Friday.",
                 sender="HackerRank <support@hackerrankmail.com>"),
            # you applied yourself after a handoff -> confirmed + done, never re-applied
            _msg("Thank you for applying to Coinbase", "We have received your application.",
                 sender="Coinbase <no-reply@coinbase.com>"),
            _msg("Interview tips for you", "5 questions to ace your interview",
                 sender="Glassdoor <noreply@glassdoor.com>"),
        ])
        hr = db.query(Job).filter_by(job_id="hr").one()
        cb = db.query(Job).filter_by(job_id="cb").one()
        assert hr.outcome == "" and hr.application_status == "READY_FOR_REVIEW"
        assert cb.application_status == "SUBMITTED_CONFIRMED" and cb.pipeline_stage == "DONE"
        assert not db.query(InboxMessage).filter(InboxMessage.sender.like("%glassdoor%")).count()
    finally:
        db.close()


def test_match_job_prefers_title_and_company():
    a = Job(job_id="a", company="devrev", title="Solution Engineer : Delhi", submission_date="1")
    b = Job(job_id="b", company="devrev", title="Backend Engineer", submission_date="2")
    c = Job(job_id="c", company="stripe", title="SE", submission_date="3")
    assert match_job([a, b, c], "Re: Solution Engineer : Delhi", "", "x@devrev.ai").job_id == "a"
    assert match_job([c], "hello", "nothing here", "x@y.com") is None


def test_slots_skip_weekends_and_calendar_link():
    sat = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)   # a Friday in UTC -> Sat next
    slots = next_slots(sat, 3)
    assert all(s.weekday() < 5 for s in slots) and len(slots) == 3
    url = calendar_link("Interview: X", slots[0], 45, "details")
    assert "dates=" in url and "Interview%3A%20X" in url


# ---- coach ------------------------------------------------------------------ #
def test_dream_kit_and_note_length(profile):
    j = Job(job_id="k", company="Acme", title="Backend Engineer",
            description="Python, FastAPI, Docker, Kubernetes, Golang required.")
    kit = coach.dream_kit(j, profile)
    assert "python" in kit["you_have"] and "golang" in kit["you_lack"]
    assert 0 < kit["keyword_match_pct"] < 100 and len(kit["recruiter_note"]) <= 200
    assert "linkedin.com/search/results/people" in kit["find_recruiter_url"]


def test_prep_mock_debrief_without_llm(profile):
    j = Job(job_id="k", company="Acme", title="ML Engineer", description="Python PyTorch",
            job_url="https://x")
    brief = coach.prep_brief(j, profile, "technical round")
    assert "60-SECOND INTRO" in brief and "Expected CTC" in brief and "[CHECK]" in brief
    assert len(coach.mock_questions(j, "hr")) == 6
    weak = coach.score_answer(j, profile, "Tell me about yourself", "I like code.")
    strong = coach.score_answer(j, profile, "Tell me about a project",
                                "Situation: our steel defect dataset had 1,800 images. I built a "
                                "MobileNetV2 classifier, which reached 96% accuracy because I added "
                                "attention modules, so that inference ran in 20 ms. The result was a "
                                "model the lab now uses for every new batch of images it collects.")
    assert weak["score"] < strong["score"]
    d = coach.debrief(j, profile, "Asked about CNNs", "Priya Sharma")
    assert "Hi Priya" in d["thank_you"]


def test_linkedin_kit_limits(profile):
    db = SessionLocal()
    try:
        _add_job(db, "l1")
        kit = coach.linkedin_kit(db, profile)
        assert kit["headline_chars"] <= 220 and kit["about_chars"] <= 2600
        assert len(kit["top_skills"]) == 10 and "never edits" in kit["note"]
    finally:
        db.close()


def test_followups_and_weekly_review(profile):
    db = SessionLocal()
    try:
        _add_job(db, "old", submitted_days_ago=9, score=80)
        _add_job(db, "new", submitted_days_ago=1, score=90)
        _add_job(db, "replied", submitted_days_ago=9, score=95, outcome="RECRUITER_REPLY")
        f = coach.followups(db, profile)
        assert [x["job_id"] for x in f] == ["old"]           # 7+ days, no reply only
        r = coach.weekly_review(db, days=7)
        assert r["applied"] == 1 and r["by_source"]["greenhouse"]["applied"] == 1
        r30 = coach.weekly_review(db, days=30)
        assert r30["replies"] == 1 and r30["by_source"]["greenhouse"]["reply_rate"] == round(1 / 3, 2)
        assert "Three changes" in coach.weekly_review_text(r30)
    finally:
        db.close()


# ---- API -------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.api.coach_routes import router
    from app.api.deps import set_runtime
    from app.orchestrator import Orchestrator
    prof = tmp_path / "candidate_profile.yaml"
    shutil.copy(get_settings().profile_path, prof)
    monkeypatch.setattr(get_settings(), "profile_path", prof)
    app = FastAPI()
    app.include_router(router)
    set_runtime(Orchestrator(enabled_agents={"discovery"}), None, None)
    c = TestClient(app)
    c.headers["X-API-Key"] = get_settings().api_key
    yield c
    from app.profile_loader import load_profile
    monkeypatch.undo()
    load_profile(force=True)


def test_answers_api_roundtrip(client):
    r = client.post("/api/answers", json={"question": "Can you commute to Whitefield?", "answer": "Yes"})
    assert r.status_code == 200
    got = client.get("/api/answers").json()
    assert any(a["answer"] == "Yes" for a in got["answers"]) and got["coverage"]
    assert client.post("/api/answers", json={"question": "x", "answer": " "}).status_code == 422


def test_outcome_and_attempt_endpoints(client):
    db = SessionLocal()
    _add_job(db, "o1")
    db.add(ApplicationAttempt(job_id="o1", result="SUBMISSION_UNVERIFIED",
                              detail={"filled": ["Email"], "screenshot": "C:/Windows/win.ini"}))
    db.commit()
    aid = db.query(ApplicationAttempt).one().id
    db.close()
    assert client.post("/api/jobs/o1/outcome", json={"outcome": "INTERVIEW_BOOKED",
                                                     "interview_at": "2026-11-02T10:30"}).status_code == 200
    assert client.post("/api/jobs/o1/outcome", json={"outcome": "NOPE"}).status_code == 422
    att = client.get("/api/jobs/o1/attempts").json()
    assert att[0]["filled"] == ["Email"] and att[0]["has_screenshot"]
    # only files inside data/logs/screenshots are ever served
    assert client.get(f"/api/attempts/{aid}/screenshot").status_code == 404
    db = SessionLocal()
    try:
        assert db.query(Interview).filter_by(job_id="o1").count() == 1
    finally:
        db.close()


def test_coach_endpoints(client):
    db = SessionLocal()
    _add_job(db, "c1")
    db.close()
    assert "keyword_match_pct" in client.get("/api/jobs/c1/kit").json()
    assert "brief" in client.post("/api/jobs/c1/prep", json={"kind": "hr"}).json()
    assert client.post("/api/jobs/c1/mock", json={"question": "q", "answer": ""}).status_code == 422
    d = client.post("/api/jobs/c1/debrief", json={"notes": "went well"}).json()
    assert "thank_you" in d
    assert client.get("/api/coach/weekly").json()["text"]
    assert client.get("/api/coach/linkedin").json()["headline"]
    assert isinstance(client.get("/api/coach/followups").json(), list)
    assert client.get("/api/inbox").json()["messages"] == []
