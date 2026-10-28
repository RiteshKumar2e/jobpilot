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

# "3+ years", "1-3 yrs", "1 to 3 years" (a range counts by its lower bound)
_YEARS_RE = re.compile(r"(\d+)\s*\+?\s*(?:(?:-|–|to)\s*\d+\s*)?(?:years?|yrs?)\b", re.I)
_MIN_YEARS_RE = re.compile(r"(?:minimum|at least|min\.?)\s*(\d+)\s*\+?\s*(?:years?|yrs?)\b", re.I)

# Level markers in a title that mean "not entry level": SDE II, Engineer 3, L2,
# Intermediate, Team Leader ...
_LEVEL_RE = re.compile(
    r"\b(?:ii|iii|iv|l[2-9]|level\s*[2-9]|intermediate|mid[- ]level|team lead(?:er)?)\b"
    r"|\b(?:engineer|developer|sde|swe|scientist)[\s-]*[2-9]\b")

# A target role must look like one of these (engineering / ML / research)...
_DEFAULT_TITLE_MUST_MATCH = ["engineer", "engineering", "developer", "sde", "swe",
                             "scientist", "programmer", "machine learning", "ml", "ai",
                             "research", "software", "backend", "full stack", "fullstack"]
# ...and must not be one of these (support, sales, ops, design, analyst ...).
_DEFAULT_NON_TARGET_TITLES = ["support", "sales", "account", "operations", "people",
                              "ux", "designer", "administrator", "customer",
                              "technical services", "pricing", "monetization", "risk",
                              "finance", "analyst", "electrical", "mechanical", "recruit",
                              "marketing", "legal", "product manag", "hardware",
                              # data-labelling / content gigs that say "AI" in the title
                              "trainer", "annotation", "annotator", "tutor", "writer"]

# Scam signals: real employers never charge candidates or move hiring to chat apps.
# Phrases, not bare words — "WhatsApp Business API developer" is a real job.
_SCAM_RE = re.compile(
    r"\b(?:registration|training|joining|processing|application|security|kit|onboarding)"
    r"\s+(?:fee|fees|charges?|deposit)\b"
    r"|\bsecurity\s+deposit\b|\brefundable\s+deposit\b"
    r"|\bpay\s+(?:a|the|an)?\s*(?:small\s+)?(?:fee|deposit|amount)\s+(?:to|for|before)\b"
    r"|\b(?:contact|message|text|ping|dm|reach|apply|send\s+(?:your\s+)?(?:cv|resume))"
    r"\s+(?:us|me|hr|the\s+recruiter)?\s*(?:on|via|over|through|to)\s+(?:whatsapp|telegram)\b"
    r"|\bjoin\s+(?:our|the|my)\s+telegram\b|\bt\.me/", re.I)

# A remote location that is open to candidates in India.
_GLOBAL_REMOTE_RE = re.compile(
    r"\b(?:anywhere|worldwide|global|globally|international|apac|asia|asia[- ]pacific)\b")


def _has_word(text: str, phrase: str) -> bool:
    """Whole-word match ("lead" matches "Technical Lead", not "leading")."""
    s = phrase.strip().lower()
    tail = r"\b" if s[-1:].isalnum() else ""
    return bool(s) and re.search(rf"\b{re.escape(s)}{tail}", text) is not None


class VerificationAgent:
    name = "verification"

    def __init__(self, profile):
        self.profile = profile
        self.ex = profile.exclude

    def verify(self, job: RawJob, max_years: int = 2) -> VerificationResult:
        reasons: List[str] = []
        blob = f"{job.title}\n{job.description}".lower()
        title_l = job.title.lower()

        # 0) Scam signals -> REJECT (fees/deposits, hiring moved to WhatsApp/Telegram)
        m = _SCAM_RE.search(blob)
        if m:
            reasons.append(f"Looks like a scam: '{m.group(0).strip()}' — real jobs never "
                           f"charge you or move hiring to chat apps.")
            return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)

        # 1) Internship-only -> REJECT
        if self.ex.get("internship_only", True):
            if any(sig in blob for sig in self.ex.get("internship_signals", [])):
                # but allow "internship" mentioned only as a perk/alongside full-time
                if any(_has_word(title_l, w) for w in
                       ("intern", "internship", "co-op", "apprentice", "apprenticeship")):
                    reasons.append("Internship-only role (title indicates intern).")
                    return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)
                reasons.append("Mentions internship; needs human check it's not intern-only.")

        # 2) Seniority -> REJECT when clearly senior
        if self.ex.get("senior_roles", True):
            for sig in self.ex.get("senior_signals", []):
                if _has_word(title_l, sig):
                    reasons.append(f"Seniority signal in title: '{sig.strip()}'.")
                    return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)
            m = _LEVEL_RE.search(title_l)
            if m:
                reasons.append(f"Title indicates a non-entry level ('{m.group(0).strip()}').")
                return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)

        # 2b) Role relevance: engineering / ML / research — not support, sales,
        # operations, design or analyst work
        must = self.ex.get("title_must_match") or _DEFAULT_TITLE_MUST_MATCH
        if not any(_has_word(title_l, w) for w in must):
            reasons.append("Not a target role (no engineering/ML/research keyword in title).")
            return VerificationResult(status=EligibilityStatus.REJECTED.value, reasons=reasons)
        for w in self.ex.get("non_target_title_signals") or _DEFAULT_NON_TARGET_TITLES:
            if _has_word(title_l, w):
                reasons.append(f"Not a target role ('{w.strip()}' in title).")
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
        """Onsite/hybrid must be in India. Remote counts only when it's open to
        someone in India: listed for India, or not tied to a country ("Remote",
        "Remote - Anywhere", "Remote, APAC"). "Remote - Spain" / "Remote, US"
        hire residents of that country only, so applying from India is wasted."""
        loc = job.location or ""
        wm = (job.work_mode or "").lower()
        entries = [e.strip() for e in re.split(r"[;|]", loc) if e.strip()]
        if any(is_india_location(e) for e in entries):
            return EligibilityStatus.VERIFIED, ""
        remote = wm == "remote" or "remote" in loc.lower()
        if not entries:
            if remote:
                return EligibilityStatus.VERIFIED, "Remote, no location restriction."
            return EligibilityStatus.NEEDS_REVIEW, "No location given — confirm it's in India or remote."
        if remote:
            if not self.profile.locations.get("accept_remote_anywhere", True):
                return EligibilityStatus.REJECTED, "Remote outside India not accepted per profile."
            for e in entries:
                rest = re.sub(r"\bremote\b|[-,()–:/]", " ", e.lower()).strip()
                if not rest or _GLOBAL_REMOTE_RE.search(rest):
                    return EligibilityStatus.VERIFIED, f"Remote, open globally ({loc})."
            return EligibilityStatus.REJECTED, f"Remote but limited to '{loc}' — not open to candidates in India."
        return EligibilityStatus.REJECTED, f"{(wm or 'onsite').capitalize()} in '{loc}' — onsite/hybrid roles must be in India."
