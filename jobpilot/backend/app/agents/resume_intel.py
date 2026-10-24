"""Agent 4 — Resume Intelligence.

Owns the resume files: discovers variants in data/resumes/, keeps a searchable
index, and for each shortlisted job produces a tailored, truthful, ATS PDF +
editable source. Records every version in resume_versions for audit.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from ..config import get_settings
from ..models import ResumeVersion
from ..resume.parsing import discover_resumes, ParsedResume
from ..resume.tailor import ResumeTailor
from .base import log_event


class ResumeIntelAgent:
    name = "resume_intel"

    def __init__(self, profile):
        self.profile = profile
        self.settings = get_settings()
        self.tailor = ResumeTailor(profile, self.settings.tailored_dir)
        self._index: List[ParsedResume] | None = None
        self._index_sig: tuple = ()

    def _folder_signature(self) -> tuple:
        d = Path(self.settings.resumes_dir)
        if not d.exists():
            return ()
        return tuple(sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                            for p in d.iterdir() if p.is_file()))

    def index(self, force: bool = False) -> List[ParsedResume]:
        # Re-parse whenever a resume is added, replaced, or removed — no restart needed.
        sig = self._folder_signature()
        if self._index is None or force or sig != self._index_sig:
            self._index = discover_resumes(self.settings.resumes_dir, self.profile.all_skills)
            self._index_sig = sig
        return self._index

    def variants(self) -> List[dict]:
        return [{"filename": r.filename, "label": r.variant_label,
                 "skills_found": r.skills_found} for r in self.index()]

    def tailor_for_job(self, db, job, score_result) -> dict:
        resumes = self.index()
        base = self.tailor.select_base(resumes, score_result.recommended_resume_version)
        tailored = self.tailor.tailor(job, score_result, base)
        tailored = self.tailor.save(tailored, job.job_id)

        db.add(ResumeVersion(
            job_id=job.job_id,
            base_variant=tailored.base_variant,
            label=tailored.label,
            pdf_path=tailored.pdf_path,
            source_path=tailored.source_path,
            content_hash=tailored.content_hash,
            flags=tailored.flags,
        ))
        db.commit()
        log_event(db, level="info", agent=self.name,
                  message=f"Tailored resume for {job.title} @ {job.company}",
                  job_id=job.job_id,
                  data={"pdf": tailored.pdf_path, "flags": tailored.flags})
        return {
            "pdf_path": tailored.pdf_path,
            "source_path": tailored.source_path,
            "label": tailored.label,
            "flags": tailored.flags,
        }
