"""LLM_EXTRA_BODY puts server-specific JSON fields (for example vLLM's chat_template_kwargs) into every request; unset, nothing is added."""
import importlib

import litellm

from apps import llm


def _sent_kwargs(monkeypatch, extra_body_env):
    seen = {}
    monkeypatch.setattr(litellm, "completion", lambda **kw: seen.update(kw))
    if extra_body_env is None:
        monkeypatch.delenv("LLM_EXTRA_BODY", raising=False)
    else:
        monkeypatch.setenv("LLM_EXTRA_BODY", extra_body_env)
    importlib.reload(llm)
    llm.complete(messages=[{"role": "user", "content": "hi"}])
    return seen


def teardown_function():
    importlib.reload(llm)


def test_extra_body_is_sent_with_every_request(monkeypatch):
    sent = _sent_kwargs(monkeypatch, '{"chat_template_kwargs": {"enable_thinking": false}}')
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_nothing_is_added_when_unset(monkeypatch):
    assert "extra_body" not in _sent_kwargs(monkeypatch, None)


def test_invalid_json_is_ignored_like_extra_headers(monkeypatch):
    assert "extra_body" not in _sent_kwargs(monkeypatch, "{not json")
