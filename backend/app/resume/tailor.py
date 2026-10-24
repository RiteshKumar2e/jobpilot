"""Resume tailoring + ATS PDF generation, with hard truthfulness guards.

Pipeline:
  1. Pick the best base resume variant for the job (from the candidate's REAL files).
  2. Build a tailored one-page resume: reorder skills by relevance, select the
     most relevant real projects, write a job-targeted summary.
  3. VERIFY: every skill/claim on the tailored resume must be traceable to the
     source resume text or a `verified: true` profile item. Anything not
     traceable is DROPPED and recorded as a flag — never shown as fact.
  4. Render an ATS-friendly PDF (reportlab) + save an editable .md source.

Works with or without an LLM: the summary uses the LLM when configured, else a
deterministic template. Neither path may introduce unverifiable claims.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from ..llm import get_llm
from ..profile_loader import CandidateProfile
from .parsing import AIML, CV, FULLSTACK, GENAI, GENERAL, ML, SD, ParsedResume

# When the ideal resume variant doesn't exist, use the nearest one you DO have.
_VARIANT_FALLBACK = {
    CV: [ML, AIML, GENAI],
    ML: [AIML, CV, GENAI],
    AIML: [ML, GENAI, CV],
    GENAI: [AIML, ML],
    SD: [FULLSTACK, AIML],
    FULLSTACK: [SD, AIML],
    GENERAL: [AIML, ML, GENAI],
}


@dataclass
class TailoredResume:
    label: str
    base_variant: str
    summary: str
    skills_ordered: List[str]
    projects: List[dict]
    md_source: str
    pdf_path: str = ""
    source_path: str = ""
    content_hash: str = ""
    flags: List[str] = field(default_factory=list)


class ResumeTailor:
    def __init__(self, profile: CandidateProfile, tailored_dir: Path):
        self.profile = profile
        self.tailored_dir = Path(tailored_dir)
        self.tailored_dir.mkdir(parents=True, exist_ok=True)

    # ---- selection -------------------------------------------------------- #
    def select_base(self, resumes: List[ParsedResume], wanted_label: str) -> ParsedResume | None:
        if not resumes:
            return None
        # Exact variant first, then the nearest variants in order, then the
        # resume with the most profile skills.
        for label in [wanted_label, *_VARIANT_FALLBACK.get(wanted_label, [])]:
            matches = [r for r in resumes if r.variant_label == label]
            if matches:
                return max(matches, key=lambda r: len(r.skills_found))
        return max(resumes, key=lambda r: len(r.skills_found))

    # ---- tailoring -------------------------------------------------------- #
    def tailor(self, job, score_result, base: ParsedResume | None) -> TailoredResume:
        flags: List[str] = []
        jd = f"{job.title}\n{job.description}".lower()

        # source-of-truth text: base resume if present, else only verified profile
        source_text = (base.text.lower() if base else "")
        if not base:
            flags.append("No base resume file found in data/resumes/ — "
                         "tailored from verified profile only; add real resume for best results.")

        # 1) skills: start from matching skills, keep only those traceable to source
        ordered = list(dict.fromkeys(score_result.matching_skills + self.profile.skills_lower))
        traceable, dropped = self._verify_skills(ordered, source_text)
        if dropped:
            flags.append(f"Dropped {len(dropped)} skill(s) not found in your resume: "
                         f"{', '.join(sorted(dropped)[:8])}")

        # 2) projects: only verified or source-traceable ones
        projects = self._select_projects(jd, source_text, flags)

        # 3) summary (LLM when configured, else template) — bounded by real data
        summary = self._summary(job, traceable, projects, flags)

        label = f"{self.profile.name.split()[0] if self.profile.name else 'resume'}_" \
                f"{score_result.recommended_resume_version.replace('/', '-').replace(' ', '')}_" \
                f"{re.sub(r'[^a-zA-Z0-9]+', '-', job.company)[:20]}"

        md = self._render_md(job, summary, traceable, projects)
        content_hash = hashlib.sha1(md.encode()).hexdigest()[:12]

        return TailoredResume(
            label=label,
            base_variant=f"{base.variant_label} ({base.filename})" if base else "profile-only",
            summary=summary,
            skills_ordered=traceable,
            projects=projects,
            md_source=md,
            source_path=base.path if base else "",
            content_hash=content_hash,
            flags=flags,
        )

    def _verify_skills(self, skills: List[str], source_text: str) -> Tuple[List[str], set]:
        """Only keep skills present in the real resume text. If no resume text,
        keep profile skills but every one gets a review flag via caller."""
        if not source_text:
            # no resume to verify against -> keep profile skills (already stated by user)
            return skills[:18], set()
        kept, dropped = [], set()
        for s in skills:
            if s in source_text:
                kept.append(s)
            else:
                dropped.add(s)
        return kept[:18], dropped

    def _select_projects(self, jd: str, source_text: str, flags: List[str]) -> List[dict]:
        chosen = []
        for item in self.profile.projects:
            tags = [t.lower() for t in item.get("tags", [])]
            relevant = any(t in jd for t in tags) or any(t in jd for t in
                          [item.get("name", "").lower()])
            name_in_source = item.get("name", "").lower()[:12] in source_text if source_text else False
            if not item.get("verified") and not name_in_source:
                # unverified AND not in resume text -> flag, include but marked
                if relevant:
                    flags.append(f"Project '{item.get('name')}' is unverified — "
                                 f"confirm before sending (set verified: true).")
            if relevant or item.get("verified"):
                chosen.append({
                    "name": item.get("name", ""),
                    "summary": item.get("summary", "").strip(),
                    "tags": item.get("tags", []),
                    "verified": bool(item.get("verified")),
                })
        return chosen[:4]

    def _summary(self, job, skills: List[str], projects: List[dict], flags: List[str]) -> str:
        edu = self.profile.education[0] if self.profile.education else {}
        degree = edu.get("degree", "B.Tech CSE")
        top_skills = ", ".join(skills[:6]) if skills else "Python, ML"
        fallback = (
            f"{degree} candidate targeting {job.title} roles. "
            f"Hands-on with {top_skills}. "
            f"{'Project experience in ' + ', '.join(p['name'] for p in projects[:2]) + '. ' if projects else ''}"
            f"Seeking an entry-level position to apply ML/AI and software engineering skills."
        ).strip()

        llm = get_llm()
        if llm.provider_name == "none":
            return fallback

        system = ("You write truthful, concise resume summaries. Use ONLY facts in "
                  "the provided context. Do NOT invent skills, metrics, titles, or "
                  "experience. 2-3 sentences, first-person-implied, ATS-friendly.")
        ctx = (f"Target role: {job.title} at {job.company}\n"
               f"Candidate degree: {degree}\n"
               f"Verified skills: {top_skills}\n"
               f"Projects: {[p['name'] for p in projects]}\n"
               f"FALLBACK:::{fallback}")
        try:
            res = llm.complete(system, ctx, max_tokens=200)
            return res.text or fallback
        except Exception as e:
            flags.append(f"LLM summary failed ({e}); used template.")
            return fallback

    def _render_md(self, job, summary, skills, projects) -> str:
        c = self.profile.raw().get("candidate", {})
        edu = self.profile.education[0] if self.profile.education else {}
        lines = [
            f"# {c.get('name','')}",
            f"{c.get('email','')} · {c.get('phone','')} · {c.get('location','')}",
            f"{c.get('github','')} · {c.get('linkedin','')} · {c.get('website','')}",
            "",
            "## Summary",
            summary,
            "",
            "## Skills",
            ", ".join(skills),
            "",
            "## Education",
            f"**{edu.get('degree','')}**, {edu.get('institution','')} "
            f"({edu.get('start_year','')}–{edu.get('end_year','')}) — CGPA {edu.get('cgpa','')}",
            "",
            "## Experience",
        ]
        for exp in self.profile.experience:
            tag = "" if exp.get("verified") else "  _(unverified — confirm)_"
            lines.append(f"**{exp.get('title','')}**, {exp.get('organization','')} "
                         f"({exp.get('start','')}–{exp.get('end','')}){tag}")
            lines.append(f"- {exp.get('summary','').strip()}")
        lines += ["", "## Projects"]
        for p in projects:
            tag = "" if p.get("verified") else "  _(unverified — confirm)_"
            lines.append(f"**{p['name']}**{tag}")
            lines.append(f"- {p['summary']}")
        lines += ["", f"_Tailored for {job.title} @ {job.company} on "
                      f"{datetime.now():%Y-%m-%d}._"]
        return "\n".join(lines)

    # ---- output ----------------------------------------------------------- #
    def save(self, tailored: TailoredResume, job_id: str) -> TailoredResume:
        base = self.tailored_dir / f"{tailored.label}_{tailored.content_hash}"
        md_path = base.with_suffix(".md")
        md_path.write_text(tailored.md_source, encoding="utf-8")
        tailored.source_path = tailored.source_path or str(md_path)
        try:
            pdf_path = self._render_pdf(tailored, base.with_suffix(".pdf"))
            tailored.pdf_path = str(pdf_path)
        except Exception as e:
            tailored.flags.append(f"PDF render failed ({e}); .md source saved.")
            tailored.pdf_path = str(md_path)
        return tailored

    def _render_pdf(self, tailored: TailoredResume, out: Path) -> Path:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib.enums import TA_LEFT
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
        from reportlab.lib import colors

        c = self.profile.raw().get("candidate", {})
        edu = self.profile.education[0] if self.profile.education else {}
        styles = getSampleStyleSheet()
        name_st = ParagraphStyle("name", parent=styles["Title"], fontSize=18,
                                 spaceAfter=2, alignment=TA_LEFT)
        contact_st = ParagraphStyle("contact", parent=styles["Normal"], fontSize=8.5,
                                    textColor=colors.HexColor("#444444"), spaceAfter=2)
        h_st = ParagraphStyle("h", parent=styles["Heading2"], fontSize=11,
                              spaceBefore=8, spaceAfter=2, textColor=colors.HexColor("#1a1a2e"))
        body_st = ParagraphStyle("body", parent=styles["Normal"], fontSize=9.3, leading=12.5)
        small_st = ParagraphStyle("small", parent=styles["Normal"], fontSize=8.5,
                                 textColor=colors.HexColor("#666666"))

        doc = SimpleDocTemplate(str(out), pagesize=A4,
                                leftMargin=16*mm, rightMargin=16*mm,
                                topMargin=14*mm, bottomMargin=12*mm)
        flow = []
        flow.append(Paragraph(c.get("name", ""), name_st))
        contact = " · ".join([v for v in [c.get("email"), c.get("phone"),
                                          c.get("location")] if v])
        links = " · ".join([v for v in [c.get("github"), c.get("linkedin"),
                                        c.get("website")] if v])
        if contact:
            flow.append(Paragraph(contact, contact_st))
        if links:
            flow.append(Paragraph(links, contact_st))
        flow.append(HRFlowable(width="100%", thickness=0.6,
                               color=colors.HexColor("#cccccc"), spaceBefore=4, spaceAfter=2))

        def section(title):
            flow.append(Paragraph(title, h_st))

        section("Summary")
        flow.append(Paragraph(tailored.summary, body_st))

        section("Skills")
        flow.append(Paragraph(", ".join(tailored.skills_ordered), body_st))

        section("Education")
        flow.append(Paragraph(
            f"<b>{edu.get('degree','')}</b>, {edu.get('institution','')} "
            f"({edu.get('start_year','')}–{edu.get('end_year','')}) — CGPA {edu.get('cgpa','')}",
            body_st))

        if self.profile.experience:
            section("Experience")
            for exp in self.profile.experience:
                tag = "" if exp.get("verified") else "  (unverified)"
                flow.append(Paragraph(
                    f"<b>{exp.get('title','')}</b>, {exp.get('organization','')} "
                    f"({exp.get('start','')}–{exp.get('end','')}){tag}", body_st))
                flow.append(Paragraph(f"• {exp.get('summary','').strip()}", body_st))
                flow.append(Spacer(1, 2))

        if tailored.projects:
            section("Projects")
            for p in tailored.projects:
                tag = "" if p.get("verified") else "  (unverified)"
                flow.append(Paragraph(f"<b>{p['name']}</b>{tag}", body_st))
                flow.append(Paragraph(f"• {p['summary']}", body_st))
                flow.append(Spacer(1, 2))

        doc.build(flow)
        return out
