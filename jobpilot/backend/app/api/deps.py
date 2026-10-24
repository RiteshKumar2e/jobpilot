"""Shared API dependencies: API-key auth and the orchestrator singleton."""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from ..config import get_settings

# Orchestrator + scheduler are created in main.py and stashed here.
_state: dict = {}


def set_runtime(orchestrator, scheduler, sheets):
    _state["orchestrator"] = orchestrator
    _state["scheduler"] = scheduler
    _state["sheets"] = sheets


def get_orchestrator():
    return _state.get("orchestrator")


def get_scheduler():
    return _state.get("scheduler")


def get_sheets():
    return _state.get("sheets")


async def require_api_key(x_api_key: str = Header(default="")):
    settings = get_settings()
    # If the operator left the default, don't lock them out locally, but warn.
    if settings.api_key and settings.api_key != "change-me":
        if x_api_key != settings.api_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Invalid or missing X-API-Key")
    return True
