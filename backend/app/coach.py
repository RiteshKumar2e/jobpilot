"""Coach — everything after "find + apply" from the setup guide.

  dream_kit()      keyword match %, must-haves you have / lack, a recruiter
                   note under 200 characters, and a LinkedIn people-search link
                   (you send the note yourself — LinkedIn's rules).
  prep_brief()     one-page brief for a call: company, why you fit, 60-second
                   intro, likely questions, CTC / notice answers, questions to ask.
  mock_questions() / score_answer()   a mock interview, one question at a time.
  debrief()        after the call: thank-you email, tracker update, what to improve.
  linkedin_kit()   headline (≤220), About (≤2,600), top 10 skills ordered by how
                   often your target jobs ask for them, Open-to-Work settings.
                   You paste it into LinkedIn — JobPilot never edits LinkedIn.
  followups()      applied 7+ days ago, no reply: top 5 by fit, with notes.
  weekly_review()  applications, replies and interviews by source and resume,
                   rejection patterns, and three changes for next week.

Every text uses only your profile's facts. Items you haven't marked
`verified: true` in the profile are tagged [CHECK]. With an LLM configured the
wording is improved; without one, the same content comes from templates.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import quote_plus

from sqlalchemy import select

from .llm import get_llm
from .models import ApplicationStatus, EligibilityStatus, Job, OUTCOME_RANK

_TECH = {"python", "java", "c++", "golang", "rust", "javascript", "typescript", "react",
         "node.js", "node", "fastapi", "django", "flask", "express", "sql", "postgresql", "mysql",
         "mongodb", "redis", "kafka", "spark", "airflow", "docker", "kubernetes", "aws", "gcp",
         "azure", "linux", "git", "rest", "graphql", "microservices", "pytorch", "tensorflow",
         "keras", "scikit-learn", "pandas", "numpy", "opencv", "computer vision", "deep learning",
         "machine learning", "nlp", "llm", "llms", "rag", "langchain", "transformers",
         "hugging face", "vector database", "prompt engineering", "mlops", "data structures",
         "algorithms", "system design", "ci/cd", "terraform", "jwt", "oauth"}


_CHECK_RULE = (" Items tagged [CHECK] are NOT confirmed by the candidate: keep the tag, never "
               "call them verified, and don't add details to them.")


def _llm(system: str, prompt: str, fallback: str, max_tokens: int = 900) -> str:
    llm = get_llm()
    if llm.provider_name == "none":
        return fallback
    try:
        return llm.complete(system + _CHECK_RULE, f"{prompt}\nFALLBACK:::{fallback}",
                            max_tokens=max_tokens).text or fallback
    except Exception:
        return fallback


def _facts(profile) -> dict:
    raw = profile.raw()
    cand = raw.get("candidate", {}) or {}
    edu = (raw.get("education") or [{}])[0]
    tag = lambda item: "" if item.get("verified") else " [CHECK]"
    exp = [f"{e.get('title')} at {e.get('organization')}: {(e.get('summary') or '').strip()}{tag(e)}"
           for e in raw.get("experience") or []]
    proj = [f"{p.get('name')}: {(p.get('summary') or '').strip()}{tag(p)}"
            for p in raw.get("projects") or []]
    answers = {a.get("match", ""): str(a.get("answer", "")) for a in raw.get("application_answers") or []}
    look = lambda *pats: next((v for k, v in answers.items() if any(p in k for p in pats)), "")
    return {
        "name": cand.get("name", ""), "city": cand.get("city", ""),
        "linkedin": cand.get("linkedin", ""), "github": cand.get("github", ""),
        "education": (f"{edu.get('degree', '')}, {edu.get('institution', '')} "
                      f"({edu.get('end_year', '')}), CGPA {edu.get('cgpa', '')}").strip(),
        "experience": exp, "projects": proj, "skills": profile.all_skills,
        "targets": profile.target_roles,
        "expected_ctc": look("expected"), "notice": look("notice"), "current_ctc": look("current (ctc"),
    }


def _job_terms(job) -> List[str]:
    blob = f"{job.title} {job.description}".lower()
    return sorted({t for t in _TECH if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", blob)})


# ---- dream-job kit ---------------------------------------------------------- #
def dream_kit(job, profile) -> dict:
    mine = {s.lower() for s in profile.all_skills}
    terms = _job_terms(job)
    have = [t for t in terms if t in mine or any(t in s or s in t for s in mine if len(s) > 2)]
    lack = [t for t in terms if t not in have]
    pct = round(100 * len(have) / len(terms)) if terms else None
    f = _facts(profile)
    first = f["name"].split()[0] if f["name"] else ""
    hook = (f["projects"][0].split(":")[0] if f["projects"] else f["education"])
    note = (f"Hi, I'm {first}, a CS fresher ({f['education'].split(',')[0]}). I applied for "
            f"{job.title} at {job.company}; I built {hook}. Happy to share more!")
    if len(note) > 200:
        note = f"Hi, I'm {first}. I applied for {job.title} at {job.company} and would love to share more about my work. Thanks!"
    return {
        "keyword_match_pct": pct, "job_keywords": terms, "you_have": have, "you_lack": lack,
        "recruiter_note": note[:200], "recruiter_note_chars": len(note[:200]),
        "find_recruiter_url": "https://www.linkedin.com/search/results/people/?keywords="
                              + quote_plus(f"{job.company} recruiter"),
        "advice": ("Strong match — apply with your closest resume." if (pct or 0) >= 60 else
                   "Partial match — lead with the skills you have; don't claim the missing ones."),
    }


# ---- interview prep --------------------------------------------------------- #
def mock_questions(job, kind: str = "technical") -> List[str]:
    terms = _job_terms(job)[:4]
    if kind.lower().startswith("hr") or "recruit" in kind.lower():
        return ["Tell me about yourself.", f"Why do you want to join {job.company}?",
                f"Why are you a good fit for the {job.title} role?",
                "Tell me about a project you're proud of and your exact contribution.",
                "What is your expected CTC and notice period?",
                "Where do you see yourself in two years?"]
    qs = [f"Walk me through a project where you used {t}." for t in terms[:3]]
    qs += ["Explain a technical challenge you faced and how you debugged it.",
           f"How would you design a simple service for a core feature of {job.company}?",
           "What would you do in your first 30 days in this role?"]
    return qs[:6]


def prep_brief(job, profile, kind: str = "recruiter screen") -> str:
    f = _facts(profile)
    kit = dream_kit(job, profile)
    fit = (kit["you_have"][:3] or f["skills"][:3])
    intro = (f"I'm {f['name']}, {f['education']}. "
             + (f"I've worked as {f['experience'][0].split(':')[0]}. " if f["experience"] else "")
             + (f"Recently I built {f['projects'][0].split(':')[0]}. " if f["projects"] else "")
             + f"I'm looking for an entry-level {job.title} role where I can use "
             + ", ".join(fit) + ".")
    qs = mock_questions(job, kind)
    fallback = "\n".join([
        f"PREP BRIEF — {kind.title()} · {job.title} @ {job.company}", "",
        "1. COMPANY", f"   {job.company} — read their careers/about page and the job post before the call:",
        f"   {job.job_url}", "",
        "2. WHY YOU FIT",
        *[f"   - {s}: matches the job's requirements." for s in fit],
        *([f"   - Relevant project: {f['projects'][0]}"] if f["projects"] else []), "",
        "3. 60-SECOND INTRO", f"   {intro}", "",
        "4. LIKELY QUESTIONS (answer from your real work)",
        *[f"   - {q}" for q in qs], "",
        "5. CTC & NOTICE (from your Answer Bank)",
        f"   Expected CTC: {f['expected_ctc'] or '[add to Answer Bank]'}",
        f"   Notice period: {f['notice'] or '[add to Answer Bank]'}", "",
        "6. QUESTIONS TO ASK THEM",
        "   - What does success look like in the first 90 days?",
        "   - What does the team's tech stack and code review process look like?",
        "   - How are new graduates mentored and onboarded?",
        "   - What are the next steps in the process?",
        "", "Missing from your profile for this job: " + (", ".join(kit["you_lack"]) or "nothing obvious"),
    ])
    prompt = (f"Write a one-page interview prep brief for a {kind} for this job, following "
              f"the 6 sections of the draft. Use ONLY the candidate facts; keep [CHECK] tags.\n"
              f"CANDIDATE: {f}\nJOB: {job.title} at {job.company}\n{(job.description or '')[:2500]}")
    return _llm("You are an interview coach for an Indian fresher.", prompt, fallback, 1200)


def score_answer(job, profile, question: str, answer: str) -> dict:
    a = (answer or "").strip()
    words = len(a.split())
    has_num = bool(re.search(r"\d", a))
    star = sum(w in a.lower() for w in ("situation", "task", "i built", "i led", "result",
                                        "because", "so that", "which"))
    score = min(10, 2 + (3 if 40 <= words <= 220 else 1 if words >= 15 else 0)
                + (2 if has_num else 0) + min(3, star))
    missing = []
    if words < 40:
        missing.append("Too short — give context, what YOU did, and the result.")
    if not has_num:
        missing.append("No numbers — add a metric (accuracy, time saved, users, dataset size).")
    if star < 2:
        missing.append("Structure it: situation → your action → result.")
    fallback = (f"Score: {score}/10\nWhat was missing: " + (" ".join(missing) or "Solid answer.")
                + "\nStronger version: restate it as situation → your action → measurable result, "
                  "using only your real project facts.")
    f = _facts(profile)
    text = _llm("You are a strict but kind interviewer. Score the answer out of 10, say what "
                "was missing, and give a stronger version using ONLY the candidate's real facts.",
                f"Role: {job.title} at {job.company}\nCandidate facts: {f}\nQuestion: {question}\n"
                f"Answer: {a}\nFormat:\nScore: N/10\nWhat was missing: ...\nStronger version: ...",
                fallback, 700)
    m = re.search(r"(\d{1,2})\s*/\s*10", text)
    return {"score": int(m.group(1)) if m else score, "feedback": text}


def debrief(job, profile, notes: str, interviewer: str = "") -> dict:
    f = _facts(profile)
    who = interviewer.split()[0] if interviewer else ""
    thank_you = (f"Hi {who or 'there'},\n\nThank you for taking the time to speak with me today "
                 f"about the {job.title} role at {job.company}. I enjoyed learning about the team "
                 f"and I'm excited about the opportunity. Please let me know if you need anything "
                 f"else from me.\n\nBest regards,\n{f['name']}")
    fallback = (f"THANK-YOU EMAIL\n{thank_you}\n\nTRACKER UPDATE\nStatus: Interview done · "
                f"Next step: wait for their reply; follow up in 5 working days.\n\n"
                f"MAKE STRONGER NEXT TIME\n- Add a number to each project answer.\n"
                f"- Prepare one clear story for the hardest question you got.")
    text = _llm("You help a candidate after an interview. Be specific to their notes.",
                f"Interview notes for {job.title} at {job.company}: {notes}\nInterviewer: {interviewer}\n"
                f"Write: 1) a short thank-you email, 2) the tracker update (status, next step, date), "
                f"3) two answers to make stronger next time.\nCandidate: {f['name']}", fallback, 800)
    return {"text": text, "thank_you": thank_you}


# ---- LinkedIn kit ----------------------------------------------------------- #
def linkedin_kit(db, profile) -> dict:
    f = _facts(profile)
    # skills ordered by how often your target (verified) jobs ask for them
    counts: Counter = Counter()
    for (desc, title) in db.execute(select(Job.description, Job.title).where(
            Job.eligibility_status == EligibilityStatus.VERIFIED.value).limit(800)):
        blob = f"{title} {desc}".lower()
        for s in f["skills"]:
            if re.search(rf"(?<![a-z0-9]){re.escape(s.lower())}(?![a-z0-9])", blob):
                counts[s] += 1
    top = [s for s, _ in counts.most_common(10)]
    top += [s for s in f["skills"] if s not in top][: 10 - len(top)]
    roles = f["targets"][:3]
    headline = (f"{' | '.join(roles[:2])} · " + " · ".join(top[:4]) + f" | {f['education'].split(',')[0]}")[:220]
    about_fb = "\n".join([
        f"Entry-level {roles[0] if roles else 'engineer'} with hands-on work in {', '.join(top[:3])}.",
        f"{f['education']}.", "",
        *(["Experience:"] + [f"• {e}" for e in f["experience"]] + [""] if f["experience"] else []),
        "Projects:", *[f"• {p}" for p in f["projects"]], "",
        f"Skills: {', '.join(top)}.",
        f"Open to {', '.join(roles)} roles — India (onsite/hybrid) or remote.",
        f"GitHub: {f['github']}",
    ])
    about = _llm("Rewrite this LinkedIn About section. Max 2,600 characters. The first 2 lines "
                 "must hook a recruiter. Use only these facts; keep [CHECK] tags; no buzzwords "
                 "like 'passionate' or 'results-driven'.", about_fb, about_fb, 900)[:2600]
    return {
        "headline": headline, "headline_chars": len(headline),
        "about": about, "about_chars": len(about),
        "top_skills": top,
        "open_to_work": {"job_titles": roles + f["targets"][3:4],
                         "locations": ["India", "Remote"],
                         "visibility": "Recruiters only (if employed) or All LinkedIn members"},
        "note": "Paste these into LinkedIn yourself. JobPilot never edits your LinkedIn — "
                "automated LinkedIn edits break its User Agreement.",
        "check": [x for x in f["experience"] + f["projects"] if "[CHECK]" in x],
    }


# ---- follow-ups & weekly review -------------------------------------------- #
_APPLIED = [ApplicationStatus.SUBMITTED_CONFIRMED.value, ApplicationStatus.SUBMISSION_UNVERIFIED.value]


def _when(job) -> Optional[datetime]:
    from .providers.base import parse_date
    return parse_date(job.submission_date)


def followups(db, profile, days: int = 7, top: int = 5) -> List[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = [j for j in db.scalars(select(Job).where(Job.application_status.in_(_APPLIED),
                                                     (Job.outcome == "") | Job.outcome.is_(None)))
            if (_when(j) or datetime.now(timezone.utc)) <= cutoff]
    rows.sort(key=lambda j: -(j.match_score or 0))
    out = []
    for j in rows[:top]:
        kit = dream_kit(j, profile)
        out.append({"job_id": j.job_id, "title": j.title, "company": j.company,
                    "applied": j.submission_date[:10], "score": j.match_score,
                    "note": kit["recruiter_note"], "find_recruiter_url": kit["find_recruiter_url"],
                    "job_url": j.job_url})
    return out


def weekly_review(db, days: int = 7) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    applied = [j for j in db.scalars(select(Job).where(Job.application_status.in_(_APPLIED)))
               if (_when(j) or since) >= since]

    def table(key):
        t: dict = {}
        for j in applied:
            k = key(j) or "—"
            r = t.setdefault(k, {"applied": 0, "replies": 0, "interviews": 0})
            r["applied"] += 1
            if j.outcome:
                r["replies"] += 1
            if OUTCOME_RANK.get(j.outcome or "", 0) >= OUTCOME_RANK["INTERVIEW_INVITE"] and \
                    j.outcome != "REJECTED_BY_COMPANY":
                r["interviews"] += 1
        for r in t.values():
            r["reply_rate"] = round(r["replies"] / r["applied"], 2) if r["applied"] else 0
        return t

    by_source = table(lambda j: j.source)
    by_resume = table(lambda j: j.recommended_resume_version)
    rejected = db.scalars(select(Job).where(Job.eligibility_status == EligibilityStatus.REJECTED.value,
                                            Job.updated_at >= since.replace(tzinfo=None))).all()
    reasons = Counter(re.sub(r"'.*?'|\(.*?\)|\d+", "…", (j.eligibility_reasons or ["?"])[0])[:80]
                      for j in rejected)
    needs = db.scalars(select(Job).where(Job.application_status == ApplicationStatus.ACTION_REQUIRED.value)).all()
    missing = Counter(s for j in db.scalars(select(Job).where(Job.match_score >= 45)).all()
                      for s in (j.missing_skills or []))
    changes = []
    if needs:
        changes.append(f"{len(needs)} applications are waiting on you — answer the questions in "
                       f"Answer Bank so they go through by themselves.")
    best = max(by_resume.items(), key=lambda kv: kv[1]["reply_rate"], default=None)
    if best and best[1]["replies"]:
        changes.append(f"'{best[0]}' resume gets the most replies — use it for borderline roles.")
    if missing:
        s, n = missing.most_common(1)[0]
        changes.append(f"'{s}' is the skill most often missing in your shortlisted jobs ({n}) — "
                       f"worth a small project if you're genuinely learning it.")
    if not applied:
        changes.append("No applications this week — check Sources for failing boards and add companies.")
    replies = sum(1 for j in applied if j.outcome)
    return {
        "since": since.date().isoformat(), "applied": len(applied), "replies": replies,
        "interviews": sum(r["interviews"] for r in by_source.values()),
        "by_source": by_source, "by_resume": by_resume,
        "rejection_reasons": reasons.most_common(6),
        "waiting_on_you": len(needs),
        "changes": changes[:3] or ["Keep going — volume and fit look steady."],
    }


def weekly_review_text(r: dict) -> str:
    lines = [f"JobPilot weekly review (since {r['since']})", "",
             f"Applied: {r['applied']} · Replies: {r['replies']} · Interviews: {r['interviews']}",
             f"Waiting on you: {r['waiting_on_you']}", "", "By source:"]
    lines += [f"  {k}: {v['applied']} applied, {v['replies']} replies" for k, v in r["by_source"].items()] or ["  —"]
    lines += ["", "By resume:"]
    lines += [f"  {k}: {v['applied']} applied, {v['replies']} replies" for k, v in r["by_resume"].items()] or ["  —"]
    lines += ["", "Top rejection reasons:"] + [f"  {n}× {why}" for why, n in r["rejection_reasons"]]
    lines += ["", "Three changes for next week:"] + [f"  {i}. {c}" for i, c in enumerate(r["changes"], 1)]
    return "\n".join(lines)

