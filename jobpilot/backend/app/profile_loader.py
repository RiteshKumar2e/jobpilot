"""Loads and validates the candidate profile YAML. Cached, hot-reloadable."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from .config import get_settings


class CandidateProfile:
    def __init__(self, data: Dict[str, Any]):
        self._d = data

    # convenience accessors used across agents
    @property
    def name(self) -> str:
        return self._d.get("candidate", {}).get("name", "")

    @property
    def all_skills(self) -> List[str]:
        skills = self._d.get("skills", {})
        out: List[str] = []
        for group in skills.values():
            if isinstance(group, list):
                out.extend(str(s) for s in group)
        return out

    @property
    def skills_lower(self) -> List[str]:
        return [s.lower() for s in self.all_skills]

    @property
    def role_keywords(self) -> List[str]:
        return [k.lower() for k in self._d.get("role_keywords", [])]

    @property
    def target_roles(self) -> List[str]:
        return self._d.get("target_roles", [])

    @property
    def exclude(self) -> Dict[str, Any]:
        return self._d.get("exclude", {})

    @property
    def compensation(self) -> Dict[str, Any]:
        return self._d.get("compensation", {})

    @property
    def locations(self) -> Dict[str, Any]:
        return self._d.get("locations", {})

    @property
    def sources(self) -> Dict[str, List[str]]:
        s = self._d.get("sources", {})
        return {
            "greenhouse_boards": s.get("greenhouse_boards", []) or [],
            "lever_accounts": s.get("lever_accounts", []) or [],
        }

    @property
    def projects(self) -> List[Dict[str, Any]]:
        return self._d.get("projects", [])

    @property
    def experience(self) -> List[Dict[str, Any]]:
        return self._d.get("experience", [])

    @property
    def education(self) -> List[Dict[str, Any]]:
        return self._d.get("education", [])

    def verified_items(self) -> Dict[str, List[str]]:
        """Which experience/projects are confirmed vs must be flagged for review."""
        verified, unverified = [], []
        for item in self.experience + self.projects:
            label = item.get("title") or item.get("name") or "item"
            (verified if item.get("verified") else unverified).append(label)
        return {"verified": verified, "unverified": unverified}

    def raw(self) -> Dict[str, Any]:
        return self._d


_cache: CandidateProfile | None = None


def load_profile(path: Path | None = None, force: bool = False) -> CandidateProfile:
    global _cache
    if _cache is not None and not force:
        return _cache
    settings = get_settings()
    p = Path(path or settings.profile_path)
    if not p.exists():
        raise FileNotFoundError(f"Candidate profile not found: {p}")
    with open(p, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    _cache = CandidateProfile(data)
    return _cache
