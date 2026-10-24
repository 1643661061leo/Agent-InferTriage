import json

import httpx
import pytest
from conftest import response

from agent_infertriage.agent import AgentConfig, http_provider, run_agent
from agent_infertriage.analysis import analyze
from agent_infertriage.demo import make_demo
from agent_infertriage.diagnosis import Session


def test_http_adapter_body_and_explicit_endpoint_only(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response({"action": "finish"}))

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    cfg = AgentConfig(endpoint="http://local.test/v1", model="served")
    result = http_provider(cfg, [{"role": "user", "content": "diagnose"}], 5)
    assert result["usage"]["prompt_tokens"] == 100
    assert len(seen) == 1 and seen[0].url.path == "/v1/chat/completions"
    body = json.loads(seen[0].content)
    assert body["model"] == "served" and body["stream"] is False
    assert body["max_tokens"] == cfg.max_output_tokens


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "prompt_tokens_details": {"cached_tokens": 20},
        },
        {"prompt_tokens": -10},
        {"completion_tokens": True},
    ],
)
def test_missing_or_invalid_provider_usage_is_unknown_not_zero(tmp_path, usage):
    e = make_demo()
    session = Session(tmp_path / "usage", "agent")
    result = run_agent(
        e,
        analyze(e),
        session,
        AgentConfig(endpoint="http://unused.invalid", model="mock"),
        lambda *args: response({"action": "finish"}, usage),
    )
    assert result["status"] == "completed"
    assert session.usage()["total_tokens"] is None
    assert session.usage()["unknown_usage_call_count"] == 1


def test_missing_credentials_fails_before_counting_a_model_attempt(tmp_path, monkeypatch):
    monkeypatch.delenv("INFERTRIAGE_NONEXISTENT_TEST_KEY", raising=False)
    e = make_demo()
    session = Session(tmp_path / "credentials", "agent")
    with pytest.raises(ValueError, match="environment variable"):
        run_agent(
            e,
            analyze(e),
            session,
            AgentConfig(
                endpoint="http://unused.invalid",
                model="m",
                api_key_env="INFERTRIAGE_NONEXISTENT_TEST_KEY",
            ),
        )
    assert session.usage()["model_call_count"] == 0


@pytest.mark.parametrize("budget", ["input", "total", "deadline"])
def test_zero_requests_after_initial_budget_or_deadline_exhaustion(tmp_path, budget):
    e = make_demo()
    session = Session(tmp_path / "limited", "agent")
    kw = {"max_input_tokens": 1} if budget == "input" else {"max_total_tokens": 1}
    if budget == "deadline":
        session.max_seconds = 0
        kw = {}
    cfg = AgentConfig(endpoint="http://unused.invalid", model="mock", **kw)

    def forbidden(*args):
        raise AssertionError("Budget should prevent the next request")

    if budget == "deadline":
        with pytest.raises(TimeoutError):
            run_agent(e, analyze(e), session, cfg, forbidden)
    else:
        assert run_agent(e, analyze(e), session, cfg, forbidden)["status"] == "partial"
    assert session.usage()["model_call_count"] == 0


def test_keyboard_cancel_preserves_sending_attempt(tmp_path):
    e = make_demo()
    session = Session(tmp_path / "cancel", "agent")

    def interrupt(*args):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_agent(
            e,
            analyze(e),
            session,
            AgentConfig(endpoint="http://unused.invalid", model="m"),
            interrupt,
        )
    receipt = session.finalize("cancelled", [])
    assert receipt["model_call_count"] == 1 and receipt["total_tokens"] is None


def test_token_budget_applies_to_retry_too(tmp_path):
    e = make_demo()
    session = Session(tmp_path / "retry-budget", "agent")
    calls = 0

    def timeout(*args):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("potentially consumed")

    # Fits one prompt/output reservation but not a second; fixture prompt is <2500 tokens.
    cfg = AgentConfig(
        endpoint="http://unused.invalid", model="m", max_output_tokens=2500, max_total_tokens=6000
    )
    result = run_agent(e, analyze(e), session, cfg, timeout)
    assert result["status"] == "partial" and calls == 1
    assert session.usage()["total_tokens"] is None
