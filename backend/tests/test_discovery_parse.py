"""Parser tests use captured API response SHAPES (the documented public schemas
of Greenhouse & Lever). The live HTTP fetch runs on the user's machine; here we
prove normalization/parsing is correct and deterministic.
"""
from app.agents.discovery import parse_greenhouse, parse_lever
from app.agents.base import canonicalize_url


GREENHOUSE_SAMPLE = {
    "jobs": [
        {
            "id": 12345,
            "title": "Machine Learning Engineer",
            "updated_at": "2026-10-01T10:00:00Z",
            "location": {"name": "Bengaluru, India"},
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/12345?gh_src=abc",
            "content": "<p>We seek an <strong>entry-level</strong> ML engineer. Python, PyTorch.</p>",
        }
    ]
}

LEVER_SAMPLE = [
    {
        "id": "abc-123",
        "text": "AI Engineer",
        "createdAt": 1696156800000,
        "categories": {"location": "Remote - India", "commitment": "Full-time"},
        "descriptionPlain": "Build RAG systems. Python, LLMs.",
        "hostedUrl": "https://jobs.lever.co/startco/abc-123",
        "applyUrl": "https://jobs.lever.co/startco/abc-123/apply",
    }
]


def test_greenhouse_parse():
    jobs = parse_greenhouse("acme", GREENHOUSE_SAMPLE)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.source == "greenhouse"
    assert j.source_id == "12345"
    assert j.title == "Machine Learning Engineer"
    assert j.location == "Bengaluru, India"
    assert "entry-level" in j.description  # html stripped
    assert "<" not in j.description
    # tracking param stripped by canonicalize
    assert "gh_src" not in j.job_url


def test_lever_parse():
    jobs = parse_lever("startco", LEVER_SAMPLE)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.source == "lever"
    assert j.title == "AI Engineer"
    assert j.location == "Remote - India"
    assert j.employment_type == "Full-time"
    assert j.application_url.endswith("/apply")


def test_canonicalize_strips_trackers_and_fragments():
    u = "https://x.com/jobs/1?utm_source=li&ref=xyz#section"
    assert canonicalize_url(u) == "https://x.com/jobs/1"


def test_empty_payloads():
    assert parse_greenhouse("x", {"jobs": []}) == []
    assert parse_lever("x", []) == []
