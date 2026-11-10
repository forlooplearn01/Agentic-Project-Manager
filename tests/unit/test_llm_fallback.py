"""With LLM_FALLBACK_MODEL set, a call the primary model (for example Gemma on a vLLM server) cannot serve is repeated once on the fallback
(for example gpt-4o-mini on OpenAI) with the fallback's own key and address. Unset, behaviour is exactly the single-model one."""
import importlib
import socket

import dotenv
import litellm
import pytest

from apps import llm

PRIMARY = {"LLM_MODEL": "hosted_vllm/gemma4", "LLM_API_BASE": "http://spark.test:8000/v1", "LLM_API_KEY": "vllm-key",
           "LLM_EXTRA_BODY": '{"chat_template_kwargs": {"enable_thinking": false}}'}
FALLBACK = {"LLM_FALLBACK_MODEL": "gpt-4o-mini", "OPENAI_API_KEY": "openai-key"}
ALL_VARS = ["LLM_MODEL", "LLM_API_BASE", "LLM_API_KEY", "LLM_EXTRA_BODY", "LLM_EXTRA_HEADERS", "LLM_FALLBACK_MODEL", "LLM_FALLBACK_API_KEY",
            "LLM_FALLBACK_API_BASE", "LLM_TIMEOUT", "LLM_FALLBACK_COOLDOWN", "OPENAI_API_KEY"]
MESSAGES = [{"role": "user", "content": "hi"}]


def _outage():
    return litellm.APIConnectionError(message="connection refused", llm_provider="hosted_vllm", model="gemma4")


@pytest.fixture
def setup(monkeypatch):
    """Reload apps.llm with exactly the env a test asks for (and not the real .env); litellm.completion records calls and can be told to fail."""
    calls = []
    behaviour = {"primary_error": None, "fallback_error": None}

    def fake_completion(**kw):
        is_primary = kw["model"] == "hosted_vllm/gemma4"
        calls.append(kw)
        error = behaviour["primary_error"] if is_primary else behaviour["fallback_error"]
        if error:
            raise error
        return f"answer from {kw['model']}"

    def configure(env, reachable=True):
        for name in ALL_VARS:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)
        monkeypatch.setattr(litellm, "completion", fake_completion)
        importlib.reload(llm)
        monkeypatch.setattr(llm, "_server_answers", lambda api_base, timeout=3.0: reachable)

    return configure, calls, behaviour


def teardown_function():
    importlib.reload(llm)


def test_without_a_fallback_the_call_is_the_plain_single_model_one(setup):
    configure, calls, _ = setup
    configure(PRIMARY)
    assert llm.complete(MESSAGES) == "answer from hosted_vllm/gemma4"
    assert len(calls) == 1
    assert calls[0]["api_base"] == "http://spark.test:8000/v1" and "extra_body" in calls[0]
    assert "timeout" not in calls[0] and "max_retries" not in calls[0] and "fallbacks" not in calls[0]


def test_without_a_fallback_an_error_is_raised(setup):
    configure, _, behaviour = setup
    configure(PRIMARY)
    behaviour["primary_error"] = _outage()
    with pytest.raises(litellm.APIConnectionError):
        llm.complete(MESSAGES)


def test_the_primary_is_used_when_it_works(setup):
    configure, calls, _ = setup
    configure({**PRIMARY, **FALLBACK})
    assert llm.complete(MESSAGES) == "answer from hosted_vllm/gemma4"
    assert len(calls) == 1
    assert calls[0]["max_retries"] == 0 and calls[0]["timeout"] == 300


def test_an_outage_falls_back_with_the_fallbacks_own_key_and_address(setup):
    configure, calls, behaviour = setup
    configure({**PRIMARY, **FALLBACK})
    behaviour["primary_error"] = _outage()
    assert llm.complete(MESSAGES) == "answer from gpt-4o-mini"
    second = calls[1]
    assert second["model"] == "gpt-4o-mini" and second["api_key"] == "openai-key"
    assert "api_base" not in second  # the Spark's address must never reach OpenAI
    assert "extra_body" not in second  # OpenAI rejects vLLM-only request fields


def test_an_unreachable_server_is_not_tried_at_all(setup):
    configure, calls, _ = setup
    configure({**PRIMARY, **FALLBACK}, reachable=False)
    assert llm.complete(MESSAGES) == "answer from gpt-4o-mini"
    assert [c["model"] for c in calls] == ["gpt-4o-mini"]


def test_after_an_outage_the_primary_is_skipped_until_the_cooldown_ends(setup, monkeypatch):
    configure, calls, behaviour = setup
    configure({**PRIMARY, **FALLBACK, "LLM_FALLBACK_COOLDOWN": "30"})
    clock = {"now": 1000.0}
    monkeypatch.setattr(llm.time, "time", lambda: clock["now"])
    behaviour["primary_error"] = _outage()
    llm.complete(MESSAGES)                       # primary tried and failed, then fallback
    assert [c["model"] for c in calls] == ["hosted_vllm/gemma4", "gpt-4o-mini"]
    clock["now"] += 10
    llm.complete(MESSAGES)                       # inside the cooldown: straight to the fallback
    assert [c["model"] for c in calls][2:] == ["gpt-4o-mini"]
    clock["now"] += 40
    behaviour["primary_error"] = None
    assert llm.complete(MESSAGES) == "answer from hosted_vllm/gemma4"  # cooldown over: the primary is back in use


def test_a_bad_request_falls_back_once_without_starting_a_cooldown(setup):
    configure, calls, behaviour = setup
    configure({**PRIMARY, **FALLBACK})
    behaviour["primary_error"] = litellm.BadRequestError(message="too long", model="gemma4", llm_provider="hosted_vllm")
    assert llm.complete(MESSAGES) == "answer from gpt-4o-mini"
    behaviour["primary_error"] = None
    assert llm.complete(MESSAGES) == "answer from hosted_vllm/gemma4"  # the primary was not written off


def test_a_failing_fallback_raises(setup):
    configure, _, behaviour = setup
    configure({**PRIMARY, **FALLBACK})
    behaviour["primary_error"] = _outage()
    behaviour["fallback_error"] = litellm.AuthenticationError(message="bad key", model="gpt-4o-mini", llm_provider="openai")
    with pytest.raises(litellm.AuthenticationError):
        llm.complete(MESSAGES)


def test_tools_and_json_mode_reach_the_fallback_too(setup):
    configure, calls, behaviour = setup
    configure({**PRIMARY, **FALLBACK})
    behaviour["primary_error"] = _outage()
    llm.complete(MESSAGES, tools=[{"type": "function"}], response_format={"type": "json_object"})
    assert calls[1]["tools"] == [{"type": "function"}] and calls[1]["response_format"] == {"type": "json_object"}


def test_server_answers_sees_an_open_and_a_closed_port():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        assert llm._server_answers(f"http://127.0.0.1:{port}/v1")
    finally:
        listener.close()
    assert not llm._server_answers(f"http://127.0.0.1:{port}/v1", timeout=1)
    assert llm._server_answers(None)  # no address set: nothing to check
