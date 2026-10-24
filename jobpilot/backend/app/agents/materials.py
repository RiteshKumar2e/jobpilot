"""Agent 5 — Application Materials.

Cover letter, recruiter message, form-answer suggestions, salary expectation.
Truthful and concise. Any mandatory field it can't answer from known data is
returned in `unknown_fields` so the human is ASKED, never guessed.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from ..config import get_settings
from ..llm import get_llm
from ..schemas import ApplicationMaterials
from .base import log_event

# Fields we must never invent — if unknown, ask the human.
_SENSITIVE_UNKNOWNS = ["notice_period", "current_salary", "work_authorization",
                       "visa_sponsorship", "gender", "ethnicity", "disability",
                       "veteran_status", "date_of_birth"]


class MaterialsAgent:
    name = "materials"

    def __init__(self, profile):
        self.profile = profile
        self.settings = get_settings()

    def build(self, db, job, tailored_summary: str = "", need_cover: bool = True) -> ApplicationMaterials:
        name = self.profile.name
        comp = self.profile.compensation
        salary = (f"{comp.get('target_min_lpa','')}–{comp.get('target_max_lpa','')} LPA "
                  f"(negotiable; open to strong entry-level offers)")

        cover_fallback = self._cover_fallback(job, name, tailored_summary)
        cover = ""
        if need_cover:
            cover = self._cover(job, name, tailored_summary, cover_fallback)

        recruiter_msg = (
            f"Hi, I'm {name}, a final-year B.Tech CSE student/fresher focused on "
            f"AI/ML and backend engineering. I'm very interested in the {job.title} "
            f"role at {job.company} and believe my computer-vision and Python work "
            f"is a strong fit. I'd welcome the chance to share my resume. Thank you!"
        )

        # Safe, known-answer form fields only.
        form_answers = {
            "full_name": name,
            "email": self.profile.raw().get("candidate", {}).get("email", ""),
            "phone": self.profile.raw().get("candidate", {}).get("phone", ""),
            "linkedin": self.profile.raw().get("candidate", {}).get("linkedin", ""),
            "github": self.profile.raw().get("candidate", {}).get("github", ""),
            "portfolio": self.profile.raw().get("candidate", {}).get("website", ""),
            "years_of_experience": "0",
            "willing_to_relocate": "Yes" if self.profile.locations.get("relocate") else "Open to discuss",
            "expected_salary": salary,
        }

        # Anything sensitive we don't have -> must ask.
        unknown = []
        cand = self.profile.raw().get("candidate", {})
        if not cand.get("email"):
            unknown.append("email")
        if not cand.get("phone"):
            unknown.append("phone")
        unknown += _SENSITIVE_UNKNOWNS

        mats = ApplicationMaterials(
            cover_letter=cover,
            recruiter_message=recruiter_msg,
            form_answers=form_answers,
            salary_expectation=salary,
            unknown_fields=sorted(set(unknown)),
        )

        if need_cover and cover:
            path = self._save_cover(job, cover)
            log_event(db, level="info", agent=self.name,
                      message=f"Prepared materials for {job.title} @ {job.company}",
                      job_id=getattr(job, "job_id", ""),
                      data={"cover_letter_path": path, "ask_human": mats.unknown_fields[:3]})
            mats.form_answers["_cover_letter_path"] = path
        return mats

    def _cover(self, job, name, summary, fallback) -> str:
        llm = get_llm()
        if llm.provider_name == "none":
            return fallback
        system = ("Write a concise, truthful cover letter (<=200 words). Use ONLY "
                  "the provided facts. Never invent experience, metrics, or claims.")
        ctx = (f"Candidate: {name}\nRole: {job.title} at {job.company}\n"
               f"Summary of fit: {summary}\n"
               f"Job: {job.description[:800]}\nFALLBACK:::{fallback}")
        try:
            return llm.complete(system, ctx, max_tokens=360).text or fallback
        except Exception:
            return fallback

    def _cover_fallback(self, job, name, summary) -> str:
        return (
            f"Dear {job.company} Hiring Team,\n\n"
            f"I am writing to apply for the {job.title} role. As a B.Tech Computer "
            f"Science candidate focused on AI/ML and software engineering, I am "
            f"excited by the opportunity to contribute. {summary}\n\n"
            f"My hands-on work spans Python, machine learning, computer vision, and "
            f"backend development, and I am eager to apply these skills in a "
            f"full-time entry-level role. I would welcome the chance to discuss how "
            f"I can add value to your team.\n\n"
            f"Thank you for your consideration.\n\nSincerely,\n{name}"
        )

    def _save_cover(self, job, text) -> str:
        import re
        safe = re.sub(r"[^a-zA-Z0-9]+", "-", f"{job.company}-{job.title}")[:50]
        out = Path(self.settings.tailored_dir) / f"cover_{safe}_{getattr(job,'job_id','x')}.txt"
        out.write_text(text, encoding="utf-8")
        return str(out)
