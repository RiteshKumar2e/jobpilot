"""Agent 3 — Matching & Ranking.

Transparent 0–100 score with the exact weights from the brief:
  skills 30 | experience/fresher 25 | project/research relevance 20 |
  location/work-auth 10 | role alignment 10 | compensation 5

The score is advisory for PRIORITIZATION only. Hard eligibility (Agent 2) is
applied separately and can veto regardless of score.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from ..schemas import RawJob, ScoreResult
from ..resume.parsing import AIML, CV, FULLSTACK, GENAI, ML, SD
from .base import is_india_location

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]*")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "")}


class MatchingAgent:
    name = "matching"

    WEIGHTS = {
        "skills": 30,
        "experience": 25,
        "projects": 20,
        "location": 10,
        "role": 10,
        "compensation": 5,
    }

    def __init__(self, profile):
        self.profile = profile
        self.skills = profile.skills_lower
        self.role_keywords = profile.role_keywords

    def score(self, job: RawJob, eligibility_status: str) -> ScoreResult:
        blob = f"{job.title}\n{job.description}".lower()
        job_tokens = _tokens(blob)

        skill_pts, matching, missing = self._skill_component(job_tokens, blob)
        exp_pts, exp_note = self._experience_component(blob, eligibility_status)
        proj_pts, proj_note = self._project_component(blob)
        loc_pts, loc_note = self._location_component(job)
        role_pts, role_note = self._role_component(job.title.lower(), blob)
        comp_pts, comp_note = self._comp_component(job)

        breakdown = {
            "skills": round(skill_pts, 1),
            "experience": round(exp_pts, 1),
            "projects": round(proj_pts, 1),
            "location": round(loc_pts, 1),
            "role": round(role_pts, 1),
            "compensation": round(comp_pts, 1),
        }
        total = round(sum(breakdown.values()), 1)

        resume_version = self._recommend_resume(blob, job.title.lower())
        explanation = "; ".join([exp_note, role_note, proj_note, loc_note, comp_note]).strip("; ")

        return ScoreResult(
            total=total,
            breakdown=breakdown,
            matching_skills=sorted(matching),
            missing_skills=sorted(missing),
            recommended_resume_version=resume_version,
            explanation=explanation or "Scored against profile.",
        )

    # ---- components ------------------------------------------------------- #
    def _skill_component(self, job_tokens: set, blob: str) -> Tuple[float, List[str], List[str]]:
        matching, present_required = [], []
        for sk in self.skills:
            # multiword skills matched by substring, single by token
            hit = (sk in blob) if " " in sk else (sk in job_tokens)
            if hit:
                matching.append(sk)
        # "missing" = skills the job clearly asks for that candidate lacks.
        # Heuristic: common tech terms in the JD not in candidate skills.
        common_tech = {"java", "c++", "golang", "go", "rust", "kubernetes", "aws",
                       "gcp", "azure", "spark", "kafka", "scala", "ruby", "php",
                       "tensorflow", "pytorch", "react", "angular", "vue", "nlp",
                       "langchain", "llm", "rag", "docker", "sql", "nosql"}
        jd_tech = {t for t in common_tech if (t in blob)}
        missing = sorted(jd_tech - set(self.skills) - {m for m in matching})
        # score: proportion of candidate skills present, capped, with a floor if any match
        if not self.skills:
            pts = 0.0
        else:
            coverage = len(matching) / max(6, min(len(self.skills), 12))
            pts = min(1.0, coverage) * self.WEIGHTS["skills"]
        return pts, matching, missing

    def _experience_component(self, blob: str, eligibility_status: str) -> Tuple[float, str]:
        # Full points when it's clearly fresher/entry-friendly and eligibility OK.
        entry_signals = ["fresher", "entry level", "entry-level", "graduate",
                         "0-2 years", "0–2 years", "new grad", "junior", "sde i",
                         "sde-1", "0 to 2", "early career"]
        w = self.WEIGHTS["experience"]
        if eligibility_status == "REJECTED":
            return 0.0, "Experience: ineligible"
        if any(s in blob for s in entry_signals):
            return w, "Experience: entry-level friendly"
        if eligibility_status == "VERIFIED":
            return w * 0.8, "Experience: eligible (not explicitly entry-level)"
        return w * 0.5, "Experience: needs review"

    def _project_component(self, blob: str) -> Tuple[float, str]:
        tags = []
        for item in self.profile.projects + self.profile.experience:
            tags.extend(t.lower() for t in item.get("tags", []))
        tags = set(tags)
        if not tags:
            return self.WEIGHTS["projects"] * 0.4, "Projects: no tags configured"
        hits = {t for t in tags if t in blob}
        ratio = len(hits) / max(3, len(tags))
        pts = min(1.0, ratio) * self.WEIGHTS["projects"]
        note = (f"Projects: {len(hits)} relevant area(s) match"
                if hits else "Projects: limited overlap")
        return pts, note

    def _location_component(self, job: RawJob) -> Tuple[float, str]:
        w = self.WEIGHTS["location"]
        loc = job.location or ""
        if is_india_location(loc):
            return w, "Location: India"
        if (job.work_mode or "").lower() == "remote" or "remote" in loc.lower():
            if self.profile.locations.get("accept_remote_anywhere", True):
                return w * 0.9, "Location: remote (anywhere accepted)"
            return 0.0, "Location: remote outside India (not accepted)"
        if not loc:
            return w * 0.4, "Location: not given"
        return 0.0, "Location: onsite/hybrid outside India"

    def _role_component(self, title: str, blob: str) -> Tuple[float, str]:
        w = self.WEIGHTS["role"]
        for kw in self.role_keywords:
            if kw in title:
                return w, f"Role: strong title match ('{kw}')"
        for kw in self.role_keywords:
            if kw in blob:
                return w * 0.6, "Role: keyword match in description"
        return 0.0, "Role: no target-role match"

    def _comp_component(self, job: RawJob) -> Tuple[float, str]:
        w = self.WEIGHTS["compensation"]
        sal = (job.salary_range or "").lower()
        if not sal:
            # brief: don't penalize undisclosed salary heavily
            return w * 0.6, "Comp: undisclosed (neutral)"
        nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]{2,}", sal)]
        tgt_min = self.profile.compensation.get("target_min_lpa", 0)
        if nums:
            # crude: if any number looks >= target (in LPA or absolute), reward
            return w, "Comp: salary disclosed"
        return w * 0.6, "Comp: present but unparsed"

    def _recommend_resume(self, blob: str, title: str) -> str:
        """Map the job to a resume variant label (Agent 4 owns the files and
        falls back to the nearest variant you actually have). The title decides
        first; the description only breaks ties."""
        def has(text, words):
            return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)

        if has(title, ["computer vision", "cv engineer", "vision", "perception", "imaging"]):
            return CV
        if has(title, ["research engineer", "research assistant", "research scientist",
                       "researcher"]):
            return CV
        if has(title, ["genai", "gen ai", "generative", "llm", "rag", "nlp", "agentic",
                       "ai agent", "prompt"]):
            return GENAI
        if has(title, ["full stack", "full-stack", "fullstack", "frontend", "front end"]):
            return FULLSTACK
        if has(title, ["machine learning", "ml", "data scientist", "mlops"]):
            return ML
        if has(title, ["ai engineer", "applied ai", "ai developer", "artificial intelligence", "ai"]):
            return AIML
        if has(title, ["backend", "back end", "sde", "software engineer", "software developer",
                       "python developer", "developer", "programmer"]):
            return SD
        # Title was generic — use the strongest description signal.
        signals = {
            GENAI: ["llm", "rag", "generative ai", "langchain", "prompt engineering", "vector database"],
            CV: ["computer vision", "opencv", "image segmentation", "object detection"],
            ML: ["machine learning", "scikit-learn", "pytorch", "tensorflow", "model training"],
            FULLSTACK: ["react", "full stack", "full-stack", "frontend"],
            SD: ["backend", "microservice", "rest api", "fastapi", "distributed systems"],
        }
        counts = {lbl: sum(blob.count(k) for k in kws) for lbl, kws in signals.items()}
        best = max(counts, key=counts.get)
        return best if counts[best] else AIML
