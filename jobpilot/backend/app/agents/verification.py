"""Agent 2 — Job Verification.

Applies HARD eligibility rules to each job and classifies it:
  VERIFIED | NEEDS_REVIEW | EXPIRED | REJECTED

These are kept SEPARATE from the match score (a 95-score senior role is still
REJECTED). Nothing here is treated as confirmed unless the text supports it;
ambiguous cases become NEEDS_REVIEW rather than a false VERIFIED.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from ..models import EligibilityStatus
from ..schemas import RawJob, VerificationResult
from .base import is_india_location

_YEARS_RE = re.compile(r"(\d+)\s*\+?\s*(?:-\s*\d+\s*)?years?", re.I)
_MIN_YEARS_RE = re.compile(r"(?:minimum|at least|min\.?)\s*(\d+)\s*\+?\s*years?", re.I)


class VerificationAgent:
    name = "verification"

    def __init__(self, profile):
        self.profile = profile
        self.ex = profile.exclude

    def verify(self, job: RawJob, max_years: int = 2) -> VerificationResult:
        reasons: List[str] = []
        blob = f"{job.title}\n{job.description}".lower()
        title_l = job.title.lower()

        # 1) Internship-only -> REJECT
        if self.ex.get("internship_only", True):
            if any(sig in blob for sig in self.ex.get("internship_signals", [])):
                # but allow "internship" mentioned only as a perk/alongside full-time
                if "intern" in title_l or "internship" in title_l:
                    reasons.append("Internship-only role (title indicates intern).")
                    return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)
                reasons.append("Mentions internship; needs human check it's not intern-only.")

        # 2) Seniority -> REJECT when clearly senior
        if self.ex.get("senior_roles", True):
            for sig in self.ex.get("senior_signals", []):
                # word-boundary match: "lead " used to miss "Technical Lead" at end of title
                s = sig.strip()
                tail = r"\b" if s[-1:].isalnum() else ""
                if s and re.search(rf"\b{re.escape(s)}{tail}", title_l):
                    reasons.append(f"Seniority signal in title: '{sig.strip()}'.")
                    return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)

        # 3) Experience requirement vs fresher cap
        over, why = self._exceeds_experience(blob, max_years)
        if over:
            reasons.append(why)
            return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)

        # 4) India exclusion for remote
        for sig in self.ex.get("exclude_india_signals", []):
            if sig in blob:
                reasons.append(f"Listing appears to exclude India: '{sig}'.")
                return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)

        # 5) Unpaid
        if self.ex.get("unpaid", True) and ("unpaid" in blob or "no stipend" in blob):
            reasons.append("Listing appears unpaid.")
            return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)

        # 6) Location / remote eligibility (soft -> NEEDS_REVIEW)
        loc_status, loc_reason = self._location_ok(job)
        if loc_reason:
            reasons.append(loc_reason)
        if loc_status == EligibilityStatus.REJECTED:
            return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)

        # 7) Degree requirement (soft) — flag mismatches, don't auto-reject
        if re.search(r"ph\.?d|doctorate", blob) and "phd" not in title_l:
            reasons.append("Mentions PhD requirement — verify a B.Tech fresher qualifies.")
            loc_status = EligibilityStatus.NEEDS_REVIEW

        if loc_status == EligibilityStatus.NEEDS_REVIEW:
            return VerificationResult(status=EligibilityStatus.NEEDS_REVIEW.value,
                                      reasons=reasons or ["Needs a quick human eligibility check."])

        if not reasons:
            reasons.append("Passed fresher/full-time/India/location checks.")
        return VerificationResult(status=EligibilityStatus.VERIFIED.value, reasons=reasons)

    # ---- helpers ---------------------------------------------------------- #
    def _exceeds_experience(self, blob: str, max_years: int) -> Tuple[bool, str]:
        m = _MIN_YEARS_RE.search(blob)
        if m and int(m.group(1)) > max_years:
            return True, f"Requires minimum {m.group(1)} years (> {max_years})."
        # generic "X years" or "X+ years"
        soft_signals = ["bonus", "plus", "nice to have", "preferred", "ideally",
                        "a plus", "not required", "welcome"]
        for m in _YEARS_RE.finditer(blob):
            yrs = int(m.group(1))
            # ignore phrases like "0-2 years", "within 2 years of graduation"
            if yrs > max_years and yrs <= 40:
                # context guard (both sides): skip if clearly a 'nice to have'
                ctx = blob[max(0, m.start() - 45): m.end() + 45]
                if any(w in ctx for w in soft_signals):
                    continue
                return True, f"Requires {yrs}+ years experience (> {max_years})."
        return False, ""

    def _location_ok(self, job: RawJob) -> Tuple[EligibilityStatus, str]:
        """Onsite/hybrid must be in India; remote is accepted from anywhere
        (explicit India exclusions are already rejected in step 4)."""
        loc = job.location or ""
        wm = (job.work_mode or "").lower()
        if is_india_location(loc):
            return EligibilityStatus.VERIFIED, ""
        if wm == "remote" or "remote" in loc.lower():
            if self.profile.locations.get("accept_remote_anywhere", True):
                return EligibilityStatus.VERIFIED, f"Remote role ({loc or 'no location given'}) — remote from anywhere accepted."
            return EligibilityStatus.REJECTED, "Remote outside India not accepted per profile."
        if not loc:
            return EligibilityStatus.NEEDS_REVIEW, "No location given — confirm it's in India or remote."
        return EligibilityStatus.REJECTED, f"{(wm or 'onsite').capitalize()} in '{loc}' — onsite/hybrid roles must be in India."
