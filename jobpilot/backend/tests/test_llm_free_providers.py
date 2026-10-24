"""Groq / Gemini providers: OpenAI-compatible HTTP, mocked with respx.
No real network calls and no real keys."""
import httpx
import pytest
import respx
import tenacity

from app.llm.provider import OpenAICompatLLM

BASE = "https://api.groq.com/openai/v1"
PROMPT = "Write a summary.\nFALLBACK:::Grounded fallback text."


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(tenacity, "wait_exponential", lambda **kw: tenacity.wait_none())


def _llm():
    return OpenAICompatLLM("groq", BASE, "test-key", "llama-3.3-70b-versatile", "GROQ_API_KEY")


def _ok(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


@respx.mock
def test_success_sends_prompt_without_fallback_marker():
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=_ok(" Tailored. "))
    res = _llm().complete("sys", PROMPT)
    assert res.text == "Tailored." and res.used_api and res.provider == "groq"
    sent = route.calls[0].request
    assert sent.headers["Authorization"] == "Bearer test-key"
    assert b"FALLBACK" not in sent.content


@respx.mock
def test_rate_limit_is_retried():
    respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[httpx.Response(429), _ok("after retry")])
    assert _llm().complete("sys", PROMPT).text == "after retry"


@respx.mock
def test_persistent_failure_falls_back_to_grounded_text():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(429))
    res = _llm().complete("sys", PROMPT)
    assert res.text == "Grounded fallback text."
    assert not res.used_api and "groq failed" in res.note


def test_missing_key_degrades_to_rule_based(monkeypatch):
    import app.config as cfg
    from app.llm import provider
    monkeypatch.setenv("JOBPILOT_LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(cfg, "_export_secret_keys", lambda _p: None)  # ignore a real .env
    cfg._settings = None
    provider.reset_llm()
    try:
        assert provider.get_llm().provider_name == "none"
    finally:
        monkeypatch.setenv("JOBPILOT_LLM_PROVIDER", "none")
        cfg._settings = None
        provider.reset_llm()
