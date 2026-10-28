"""Provider adapters, tested against payloads captured from each live API
(trimmed, in tests/fixtures). No network: a fake HTTP client serves fixtures."""
import json
from pathlib import Path

import httpx
import pytest

from app.providers import PROVIDERS, profile_targets
from app.providers.ats import (parse_ashby, parse_breezy, parse_personio, parse_recruitee,
                               parse_smartrecruiters_item, parse_workable_list,
                               apply_workable_detail, parse_workday_detail, workday_base)
from app.providers.base import FetchContext, Http, Target, parse_date, strip_html
from app.providers.careers import detect_ats, find_ats_links, parse_jsonld_jobs
from app.providers.feeds import (parse_arbeitnow, parse_himalayas, parse_remoteok,
                                 parse_remotive, parse_wwr, parse_adzuna)
from app.providers.portals import portal_for_url, search_links

FX = Path(__file__).parent / "fixtures"


def fx(name):
    text = (FX / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


class FakeHttp:
    """Serves fixtures by URL substring; records every call."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _hit(self, url):
        self.calls.append(url)
        for frag, val in self.routes.items():
            if frag in url:
                if isinstance(val, Exception):
                    raise val
                return val(url) if callable(val) else val
        raise AssertionError(f"unexpected URL {url}")

    def get_json(self, url, **kw):
        return self._hit(url)

    def get_text(self, url, **kw):
        return self._hit(url)

    def post_json(self, url, payload, **kw):
        return self._hit(url)

    def close(self):
        pass


def ctx(http, **kw):
    return FetchContext(http=http, **kw)


# ---- ATS parsers ------------------------------------------------------------ #
def test_ashby_parse():
    jobs = parse_ashby("ramp", fx("ashby.json"), "Ramp")
    j = jobs[0]
    assert j.source == "ashby" and j.board == "ramp" and j.company == "Ramp"
    assert j.title == "Security Engineer, Cloud"            # whitespace trimmed
    assert "Remote (Canada)" in j.location and j.work_mode == "hybrid"
    assert j.employment_type == "Full-time"
    assert j.application_url.endswith("/application")
    assert j.salary_range.startswith("$")


def test_workable_parse_and_detail():
    jobs = parse_workable_list("huggingface", fx("workable_list.json"))
    j = jobs[0]
    assert j.company == "Hugging Face" and j.source_id == "81B46579FE"
    assert j.work_mode == "remote" and "Paris" in j.location
    j2 = apply_workable_detail(j, fx("workable_detail.json"))
    assert "Hugging Face" in j2.description and "<p>" not in j2.description


def test_smartrecruiters_parse():
    item = fx("smartrecruiters_list.json")["content"][0]
    j = parse_smartrecruiters_item("ServiceNow", item)
    assert j.company == "ServiceNow" and "Hyderabad" in j.location and j.work_mode == "onsite"
    assert j.employment_type == "Full-time" and j.experience_req == ""   # "Not Applicable" dropped
    d = parse_smartrecruiters_item("ServiceNow", fx("smartrecruiters_detail.json"))
    assert d.description and d.job_url.startswith("https://jobs.smartrecruiters.com/ServiceNow/")


def test_recruitee_personio_breezy_parse():
    r = parse_recruitee("bunq", fx("recruitee.json"))
    assert r and r[0].company == "bunq" and "Sofia" in r[0].location
    p = parse_personio("personio", fx("personio.xml"))
    assert p[0].title == "Staff Software Engineer, Data Platform"
    assert "Munich" in p[0].location and "Berlin" in p[0].location
    assert "7-10 years" in p[0].description     # feed's own experience is visible to the verifier
    b = parse_breezy("breezy", fx("breezy.json"))
    assert b[0].job_url.startswith("https://breezy.breezy.hr/p/")


def test_workday_detail_parse():
    target = "nvidia.wd5/NVIDIAExternalCareerSite"
    api, public, tenant = workday_base(target)
    assert api == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite"
    j = parse_workday_detail(target, "/job/x", fx("workday_detail.json"))
    assert j.source_id == "JR2021061" and "Vietnam" in j.location and j.description
    with pytest.raises(ValueError):
        workday_base("not-a-target")


# ---- feed parsers ----------------------------------------------------------- #
def test_feed_parsers():
    rm = parse_remotive(fx("remotive.json"))
    assert rm[0].work_mode == "remote" and rm[0].location.startswith("Remote")
    ok = parse_remoteok(fx("remoteok.json"))
    assert len(ok) == 2                 # legal-notice element skipped
    an = parse_arbeitnow(fx("arbeitnow.json"))
    assert an[0].posted_at.startswith("20") and "&lt;" not in an[0].description
    hm = parse_himalayas(fx("himalayas.json"))
    assert hm[0].location == "Remote - United Kingdom"   # locationRestrictions kept
    ww = parse_wwr(fx("wwr.rss"))
    assert ww[0].company == "Jumio" and ww[0].title == "Machine Learning Engineer"
    assert ww[0].location == "Remote"   # "Anywhere in the World"
    ad = parse_adzuna({"results": [{"id": 9, "title": "<strong>ML</strong> Engineer",
                                    "company": {"display_name": "Acme"},
                                    "location": {"display_name": "Bengaluru, Karnataka"},
                                    "description": "Python", "redirect_url": "https://adzuna.in/x/9",
                                    "salary_min": 600000, "salary_max": 900000}]})
    assert ad[0].title == "ML Engineer" and ad[0].salary_range == "INR 600,000–900,000"


# ---- fetch behaviour: filtering, pagination, detail budget ------------------ #
def test_title_filter_applies_before_storage():
    http = FakeHttp({"remotive.com": fx("remotive.json")})
    res = PROVIDERS["remotive"].fetch(Target("remotive", "ml"), ctx(http, title_ok=lambda t: False))
    assert res.jobs == [] and not res.complete       # feeds never claim completeness


def test_smartrecruiters_paginates_and_limits_details():
    item = fx("smartrecruiters_list.json")["content"][0]
    pages = {0: [dict(item, id=str(i)) for i in range(100)],
             100: [dict(item, id=str(100 + i)) for i in range(30)]}

    def lister(url):
        off = int(url.split("offset=")[1].split("&")[0])
        return {"totalFound": 130, "content": pages[off]}

    http = FakeHttp({"/postings?": lister, "/postings/": fx("smartrecruiters_detail.json")})
    res = PROVIDERS["smartrecruiters"].fetch(Target("smartrecruiters", "ServiceNow"),
                                             ctx(http, max_pages=5, max_details=3))
    assert len(res.jobs) == 130 and res.complete and res.pages == 2
    assert sum("/postings/" in c for c in http.calls) == 3          # detail budget respected
    assert "country=in" in http.calls[0]


def test_smartrecruiters_page_cap_means_incomplete():
    item = fx("smartrecruiters_list.json")["content"][0]
    http = FakeHttp({"/postings?": lambda u: {"totalFound": 500,
                                             "content": [dict(item, id=u[-30:] + str(i)) for i in range(100)]},
                     "/postings/": fx("smartrecruiters_detail.json")})
    res = PROVIDERS["smartrecruiters"].fetch(Target("smartrecruiters", "X"),
                                             ctx(http, max_pages=2, max_details=0))
    assert res.pages == 2 and not res.complete     # never close jobs from a partial list


def test_workable_skips_details_for_known_jobs():
    http = FakeHttp({"/api/v1/widget": fx("workable_list.json"), "/api/v2/": fx("workable_detail.json")})
    PROVIDERS["workable"].fetch(Target("workable", "huggingface"),
                                ctx(http, known=lambda s, i: True))
    assert not any("/api/v2/" in c for c in http.calls)


def test_workday_keeps_only_india_or_remote():
    listing = {"total": 3, "jobPostings": [
        {"title": "Software Engineer", "externalPath": "/job/India-Bangalore/SE_JR1",
         "locationsText": "India, Bangalore", "bulletFields": ["JR1"]},
        {"title": "Software Engineer", "externalPath": "/job/US-CA-Santa-Clara/SE_JR2",
         "locationsText": "US, CA, Santa Clara", "bulletFields": ["JR2"]},
        {"title": "Sales Lead", "externalPath": "/job/India-Pune/SL_JR3",
         "locationsText": "India, Pune", "bulletFields": ["JR3"]}]}
    detail = {"jobPostingInfo": {"title": "Software Engineer", "jobReqId": "JR1",
                                 "location": "India, Bangalore", "jobDescription": "<p>Python</p>",
                                 "externalUrl": "https://nvidia.wd5.myworkdayjobs.com/S/job/x"}}
    http = FakeHttp({"/jobs": listing, "/job/India-Bangalore": detail})
    res = PROVIDERS["workday"].fetch(Target("workday", "nvidia.wd5/S"),
                                     ctx(http, queries=["engineer"],
                                         title_ok=lambda t: "engineer" in t.lower()))
    assert [j.source_id for j in res.jobs] == ["JR1"]
    assert not any("Santa-Clara" in c for c in http.calls)        # no detail call wasted


# ---- HTTP retry ------------------------------------------------------------- #
def test_http_retries_then_succeeds(monkeypatch):
    seq = iter([503, 503, 200])

    def handler(request):
        code = next(seq)
        return httpx.Response(code, json={"ok": code == 200})

    monkeypatch.setattr("tenacity.nap.time.sleep", lambda s: None)
    h = Http(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
    assert h.get_json("https://api.example.com/x") == {"ok": True}


def test_http_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda s: None)
    h = Http(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))),
             min_interval=0)
    with pytest.raises(Exception):
        h.get_json("https://api.example.com/x")


def test_http_404_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    h = Http(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
    with pytest.raises(httpx.HTTPStatusError):
        h.get_json("https://api.example.com/missing")
    assert len(calls) == 1


# ---- careers pages / ATS detection ----------------------------------------- #
@pytest.mark.parametrize("url,want", [
    ("https://boards.greenhouse.io/stripe/jobs/123", ("greenhouse", "stripe")),
    ("https://job-boards.greenhouse.io/embed/job_app?for=devrev&token=5", ("greenhouse", "devrev")),
    ("https://jobs.lever.co/paytm/abc-123/apply", ("lever", "paytm")),
    ("https://jobs.ashbyhq.com/sarvam/uuid", ("ashby", "sarvam")),
    ("https://apply.workable.com/huggingface/j/81B46579FE/", ("workable", "huggingface")),
    ("https://jobs.smartrecruiters.com/ServiceNow/7440001", ("smartrecruiters", "ServiceNow")),
    ("https://bunq.recruitee.com/o/role", ("recruitee", "bunq")),
    ("https://acme.jobs.personio.de/job/1", ("personio", "acme")),
    ("https://acme.breezy.hr/p/1-x", ("breezy", "acme")),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x",
     ("workday", "nvidia.wd5/NVIDIAExternalCareerSite")),
    ("https://www.linkedin.com/jobs/view/1", None),
    ("https://apply.workable.com/j/81B46579FE", None),            # short link, no account
])
def test_detect_ats(url, want):
    assert detect_ats(url) == want


JSONLD_PAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"JobPosting","title":"AI Engineer",
 "description":"<p>Build RAG apps in Python.</p>","datePosted":"2026-10-20",
 "employmentType":["FULL_TIME"],"identifier":{"@type":"PropertyValue","value":"AI-7"},
 "hiringOrganization":{"@type":"Organization","name":"Acme AI"},
 "jobLocation":{"@type":"Place","address":{"addressLocality":"Bengaluru","addressCountry":"IN"}},
 "url":"/careers/ai-engineer"},
 {"@type":"JobPosting","title":"Remote ML Engineer","jobLocationType":"TELECOMMUTE",
  "applicantLocationRequirements":{"@type":"Country","name":"India"},
  "hiringOrganization":{"name":"Acme AI"},"url":"https://acme.ai/careers/ml"}]}
</script></head><body><a href="https://jobs.lever.co/acme">All jobs</a></body></html>"""


def test_jsonld_and_board_links():
    jobs = parse_jsonld_jobs(JSONLD_PAGE, "https://acme.ai/careers")
    assert [j.title for j in jobs] == ["AI Engineer", "Remote ML Engineer"]
    assert jobs[0].company == "Acme AI" and jobs[0].source_id == "AI-7"
    assert jobs[0].job_url == "https://acme.ai/careers/ai-engineer"
    assert "Bengaluru" in jobs[0].location and jobs[0].employment_type == "FULL_TIME"
    assert jobs[1].location.startswith("Remote - India") and jobs[1].work_mode == "remote"
    assert find_ats_links(JSONLD_PAGE) == [("lever", "acme")]
    res = PROVIDERS["careers"].fetch(Target("careers", "https://acme.ai/careers"),
                                     ctx(FakeHttp({"acme.ai": JSONLD_PAGE})))
    assert len(res.jobs) == 2 and ("lever", "acme", "") in res.discovered_boards


def test_bad_jsonld_is_ignored():
    assert parse_jsonld_jobs('<script type="application/ld+json">{oops</script>', "https://x") == []


# ---- portals / targets / helpers ------------------------------------------- #
def test_portal_links_and_detection():
    links = search_links(["ml engineer"])[0]["links"]
    assert links["linkedin"].startswith("https://www.linkedin.com/jobs/search/?keywords=ml+engineer")
    assert links["naukri"] == "https://www.naukri.com/ml-engineer-jobs?experience=0"
    assert "site%3Ajobs.lever.co" in links["web:lever"]
    assert portal_for_url("https://www.linkedin.com/jobs/view/42") == "linkedin"
    assert portal_for_url("https://jobs.lever.co/x") == ""


def test_profile_targets():
    sources = {"greenhouse_boards": ["stripe"], "ashby_boards": [{"id": "sarvam", "name": "Sarvam AI"}],
               "search_queries": ["ai engineer", "python developer"],
               "feeds": {"remotive": True, "remoteok": True, "weworkremotely": ["remote-programming-jobs"],
                         "arbeitnow": False}}
    ts = profile_targets(sources, set(PROVIDERS))
    keys = [t.key for t in ts]
    assert "greenhouse:stripe" in keys and "ashby:sarvam" in keys
    assert keys.count("remotive:ai engineer") == 1 and "remoteok:latest" in keys
    assert "weworkremotely:remote-programming-jobs" in keys
    assert not any(k.startswith("arbeitnow") for k in keys)
    gh = next(t for t in ts if t.provider == "greenhouse")
    ash = next(t for t in ts if t.provider == "ashby")
    assert gh.filter_titles is False and ash.filter_titles is True and ash.label == "Sarvam AI"
    # disabled provider -> no targets
    assert not [t for t in profile_targets(sources, {"greenhouse"}) if t.provider != "greenhouse"]


def test_helpers():
    assert strip_html("&lt;p&gt;Hi &amp;amp; bye&lt;/p&gt;") == "Hi & bye"
    assert parse_date("1793170523").year == 2026
    assert parse_date("2026-10-27 12:11:34 UTC").day == 27
    assert parse_date("") is None and parse_date("garbage") is None
