from app.agents.base import make_job_id, stronger_status
from app.models import ApplicationStatus as AS


def test_job_id_stable_and_unique():
    a = make_job_id("greenhouse", "101", "https://x/jobs/101")
    b = make_job_id("greenhouse", "101", "https://x/jobs/101?utm=1")  # id dominates
    c = make_job_id("greenhouse", "102", "https://x/jobs/102")
    assert a == b          # same requisition id -> same job id
    assert a != c
    assert len(a) == 16


def test_status_never_downgrades_confirmed():
    confirmed = AS.SUBMITTED_CONFIRMED.value
    assert stronger_status(confirmed, AS.DRAFTED.value) == confirmed
    assert stronger_status(confirmed, AS.FAILED.value) == confirmed
    assert stronger_status(confirmed, AS.READY_FOR_REVIEW.value) == confirmed


def test_status_upgrades_when_stronger():
    assert stronger_status(AS.DRAFTED.value, AS.READY_FOR_REVIEW.value) == AS.READY_FOR_REVIEW.value
    assert stronger_status(AS.READY_FOR_REVIEW.value, AS.SUBMITTED_CONFIRMED.value) == AS.SUBMITTED_CONFIRMED.value


def test_unknown_status_safe():
    assert stronger_status("GARBAGE", AS.DRAFTED.value) == AS.DRAFTED.value
