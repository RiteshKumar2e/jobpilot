"""Job portals without a permitted public API: search links + manual handoff.

LinkedIn, Naukri, Indeed, Glassdoor, Foundit, Instahyre, Cutshort, Hirist,
iimjobs, Wellfound, Y Combinator's Work at a Startup, Dice, ... either have no
public jobs API, restrict API access to partners, or forbid automated access in
their terms. JobPilot therefore does NOT scrape them or log in for you. Instead:

  * `search_links()` builds ready-to-open searches for your target roles;
  * a job you find there can be imported by URL (dashboard → Sources → Import)
    and goes through the same verification, scoring, resume selection and
    cover-letter preparation — then you submit it yourself (handoff);
  * listings on these portals that point to a company ATS (Greenhouse, Lever,
    Ashby, ...) are better imported via that ATS link, which we can poll.

The "site:" templates open a normal web search for ATS-hosted postings — the
legitimate way to find employers' boards beyond the registry.
"""
from __future__ import annotations

from typing import Dict, List
from urllib.parse import quote_plus

from .base import APPLY_HANDOFF, KIND_PORTAL

# name -> (url template, note). {q} = query, {slug} = query-with-dashes, {loc} = location
PORTALS: Dict[str, tuple] = {
    "linkedin": ("https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}&f_E=1%2C2&f_TPR=r604800",
                 "No public jobs API; automated access is against LinkedIn's terms. Search link "
                 "(entry level, last 7 days) + import by URL."),
    "naukri": ("https://www.naukri.com/{slug}-jobs?experience=0",
               "No public API. Search link + import by URL."),
    "indeed": ("https://in.indeed.com/jobs?q={q}&l={loc}&fromage=7",
               "Publisher API closed to new users. Search link + import by URL."),
    "glassdoor": ("https://www.glassdoor.co.in/Job/jobs.htm?sc.keyword={q}",
                  "No public API. Search link."),
    "foundit": ("https://www.foundit.in/srp/results?query={q}&locations={loc}&experienceRanges=0~2",
                "No public API. Search link."),
    "wellfound": ("https://wellfound.com/role/l/{slug}/india",
                  "No public API (startup jobs). Search link."),
    "workatastartup": ("https://www.workatastartup.com/jobs?query={q}",
                       "Y Combinator startups; requires your login. Search link."),
    "instahyre": ("https://www.google.com/search?q=site%3Ainstahyre.com+{q}",
                  "No public API. Web search for its listings."),
    "cutshort": ("https://www.google.com/search?q=site%3Acutshort.io%2Fjob+{q}",
                 "No public API. Web search for its listings."),
    "hirist": ("https://www.google.com/search?q=site%3Ahirist.tech+{q}",
               "No public API. Web search for its listings."),
    "iimjobs": ("https://www.google.com/search?q=site%3Aiimjobs.com+{q}",
                "No public API. Web search for its listings."),
    "timesjobs": ("https://www.google.com/search?q=site%3Atimesjobs.com+{q}",
                  "No public API. Web search for its listings."),
    "dice": ("https://www.dice.com/jobs?q={q}&filters.workplaceTypes=Remote",
             "US-focused; remote search link."),
}

# Web-search templates that surface ATS-hosted postings (company discovery).
ATS_SEARCHES = {
    "greenhouse": 'site:boards.greenhouse.io OR site:job-boards.greenhouse.io "{q}" India',
    "lever": 'site:jobs.lever.co "{q}" India',
    "ashby": 'site:jobs.ashbyhq.com "{q}" India',
    "workable": 'site:apply.workable.com "{q}" India',
    "smartrecruiters": 'site:jobs.smartrecruiters.com "{q}" India',
}


def portal_capabilities() -> List[dict]:
    return [{"name": n, "kind": KIND_PORTAL, "apply_mode": APPLY_HANDOFF, "needs_key": False,
             "configured": True, "homepage": t.split("?")[0], "notes": note}
            for n, (t, note) in PORTALS.items()]


def search_links(queries: List[str], location: str = "India") -> List[dict]:
    out = []
    for q in queries:
        enc, slug = quote_plus(q), "-".join(q.lower().split())
        links = {n: t.format(q=enc, slug=slug, loc=quote_plus(location)) for n, (t, _) in PORTALS.items()}
        links.update({f"web:{n}": "https://www.google.com/search?q=" + quote_plus(t.format(q=q))
                      for n, t in ATS_SEARCHES.items()})
        out.append({"query": q, "links": links})
    return out


def portal_for_url(url: str) -> str:
    """'https://www.linkedin.com/jobs/view/123' -> 'linkedin' ('' if not a portal)."""
    u = (url or "").lower()
    for name, host in (("linkedin", "linkedin.com"), ("naukri", "naukri.com"),
                       ("indeed", "indeed."), ("glassdoor", "glassdoor."), ("foundit", "foundit.in"),
                       ("wellfound", "wellfound.com"), ("workatastartup", "workatastartup.com"),
                       ("instahyre", "instahyre.com"), ("cutshort", "cutshort.io"),
                       ("hirist", "hirist."), ("iimjobs", "iimjobs.com"),
                       ("timesjobs", "timesjobs.com"), ("dice", "dice.com")):
        if host in u:
            return name
    return ""
