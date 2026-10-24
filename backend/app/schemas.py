"""Pydantic models = the typed contract between agents and the API.

Agents consume/produce these so every hand-off is validated. This is the
"structured agent outputs" requirement.
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


# ---- Discovery ----
class RawJob(BaseModel):
    source: str
    source_id: str = ""
    company: str
    title: str
    description: str = ""
    location: str = ""
    work_mode: str = ""
    employment_type: str = ""
    experience_req: str = ""
    salary_range: str = ""
    posted_at: str = ""
    job_url: str = ""
    application_url: str = ""
    canonical_url: str = ""


# ---- Verification ----
class VerificationResult(BaseModel):
    status: str                         # EligibilityStatus value
    reasons: List[str] = Field(default_factory=list)


# ---- Scoring ----
class ScoreResult(BaseModel):
    total: float
    breakdown: dict
    matching_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    recommended_resume_version: str = ""
    explanation: str = ""


# ---- Materials ----
class ApplicationMaterials(BaseModel):
    cover_letter: str = ""
    recruiter_message: str = ""
    form_answers: dict = Field(default_factory=dict)
    salary_expectation: str = ""
    unknown_fields: List[str] = Field(default_factory=list)  # must ask the human


# ---- API read models ----
class JobOut(BaseModel):
    job_id: str
    source: str
    company: str
    title: str
    location: str
    work_mode: str
    employment_type: str
    experience_req: str
    salary_range: str
    posted_at: str
    job_url: str
    application_url: str
    eligibility_status: str
    eligibility_reasons: List[str] = Field(default_factory=list)
    match_score: Optional[float] = None
    matching_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    score_explanation: str = ""
    recommended_resume_version: str = ""
    tailored_resume_path: str = ""
    cover_letter_path: str = ""
    application_status: str
    submission_date: str = ""
    confirmation_reference: str = ""
    next_action: str = ""
    notes: str = ""
    pipeline_stage: str

    class Config:
        from_attributes = True


class StatsOut(BaseModel):
    discovered: int = 0
    verified_eligible: int = 0
    shortlisted: int = 0
    prepared: int = 0
    submitted_confirmed: int = 0
    submission_failed_or_unverified: int = 0
    interviews: int = 0
    rejected: int = 0
    pending_followups: int = 0
    by_source: dict = Field(default_factory=dict)
    by_status: dict = Field(default_factory=dict)
