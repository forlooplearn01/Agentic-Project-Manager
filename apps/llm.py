import json
import logging
import os
import socket
import time
from urllib.parse import urlparse

from dotenv import load_dotenv
import litellm

load_dotenv()

log = logging.getLogger(__name__)

# Every LLM call in this app routes through complete() below, so provider/model choice is
# entirely a .env concern - no code change needed to switch OpenAI, Anthropic, Gemini, or a
# self-hosted open-source model (Ollama, vLLM, etc.). LLM_API_KEY falls back to
# OPENAI_API_KEY and LLM_MODEL defaults to gpt-4o-mini, so leaving all of these unset
# reproduces the exact previous behavior - this is a non-disruptive swap, not a breaking one.
_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
_API_BASE = os.getenv("LLM_API_BASE") or None

# Optional fallback. With LLM_FALLBACK_MODEL set, a call the primary model cannot serve (server off, network away, server error) is
# repeated once on the fallback model with its OWN key and address (the key defaults to OPENAI_API_KEY, the address to the
# provider's own), so "Gemma on the DGX Spark, else gpt-4o-mini on OpenAI" is two lines in .env. The primary's address, key and extra
# request fields are never sent to the fallback (OpenAI rejects a vLLM-only field). Unset, nothing here runs.
_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL") or None
_FALLBACK_API_KEY = os.getenv("LLM_FALLBACK_API_KEY") or os.getenv("OPENAI_API_KEY")
_FALLBACK_API_BASE = os.getenv("LLM_FALLBACK_API_BASE") or None
# Seconds a primary call may take. A long answer from a slow self-hosted model is not a failure, so this is generous.
_TIMEOUT = float(os.getenv("LLM_TIMEOUT") or 300)
# After the primary fails because it is not there, skip it for this many seconds, so one outage does not add a wait to every call.
_COOLDOWN = float(os.getenv("LLM_FALLBACK_COOLDOWN") or 30)
_primary_down_until = 0.0
# "The server is not there or not healthy" (the cooldown applies). Any other error falls back for that one call only, because a bad
# request says nothing about whether the server is up.
_OUTAGE = (litellm.APIConnectionError, litellm.Timeout, litellm.InternalServerError, litellm.ServiceUnavailableError)

_EXTRA_HEADERS = None
_raw_extra_headers = os.getenv("LLM_EXTRA_HEADERS")
if _raw_extra_headers:
    try:
        _EXTRA_HEADERS = json.loads(_raw_extra_headers)
    except json.JSONDecodeError:
        _EXTRA_HEADERS = None

# Extra JSON fields sent in every request body, for server-specific switches. Example: a vLLM server
# running a model with a "thinking" mode, where '{"chat_template_kwargs": {"enable_thinking": false}}'
# skips the hidden reasoning (6.5 s down to 0.3 s for a one-word answer on Gemma 4).
_EXTRA_BODY = None
_raw_extra_body = os.getenv("LLM_EXTRA_BODY")
if _raw_extra_body:
    try:
        _EXTRA_BODY = json.loads(_raw_extra_body)
    except json.JSONDecodeError:
        _EXTRA_BODY = None


def _server_answers(api_base: str, timeout: float = 3.0) -> bool:
    """Can a connection be opened to the server at all? litellm ignores a short connect timeout for self-hosted servers, so an
    unreachable address would otherwise cost the operating system's ~75 s before any fallback could start."""
    if not api_base:
        return True
    url = urlparse(api_base)
    try:
        with socket.create_connection((url.hostname, url.port or (443 if url.scheme == "https" else 80)), timeout=timeout):
            return True
    except OSError:
        return False


def _request(messages, tools, response_format, temperature, model, api_key, api_base, primary: bool) -> dict:
    kwargs = {
        "model": model,
        "messages": messages,
        "api_key": api_key,
    }
    # None omits the parameter: reasoning models (gpt-5, o-series) reject temperature=0.
    if temperature is not None:
        kwargs["temperature"] = temperature
    if api_base:
        kwargs["api_base"] = api_base
    if primary and _EXTRA_HEADERS:
        kwargs["extra_headers"] = _EXTRA_HEADERS
    if primary and _EXTRA_BODY:
        kwargs["extra_body"] = _EXTRA_BODY
    if tools is not None:
        kwargs["tools"] = tools
    if response_format is not None:
        kwargs["response_format"] = response_format
    return kwargs


def complete(messages: list, tools: list = None, response_format: dict = None, temperature: float = 0, model: str = None):
    """Single choke point for every LLM call in this app - apps/agent/react_loop.py and
    apps/supervisor.py call this directly for tool-calling/JSON-forced calls, call_llm/
    call_llm_json below wrap it for the simple prompt-in-text-out case. Returns litellm's
    response object, which mirrors the OpenAI SDK's response shape (response.choices[0]
    .message.content, .tool_calls, etc.) regardless of which provider actually served it."""
    global _primary_down_until
    shape = (messages, tools, response_format, temperature)
    primary = _request(*shape, model or _MODEL, _API_KEY, _API_BASE, True)
    if not _FALLBACK_MODEL:
        return litellm.completion(**primary)

    if time.time() >= _primary_down_until:
        if _server_answers(_API_BASE):
            try:
                return litellm.completion(**primary, timeout=_TIMEOUT, max_retries=0)
            except Exception as e:
                log.warning("LLM %s failed (%s); using the fallback %s", primary["model"], type(e).__name__, _FALLBACK_MODEL)
                if isinstance(e, _OUTAGE):
                    _primary_down_until = time.time() + _COOLDOWN
        else:
            log.warning("LLM server %s is not reachable; using the fallback %s for the next %.0f s", _API_BASE, _FALLBACK_MODEL, _COOLDOWN)
            _primary_down_until = time.time() + _COOLDOWN
    return litellm.completion(**_request(*shape, _FALLBACK_MODEL, _FALLBACK_API_KEY, _FALLBACK_API_BASE, False))


def call_llm(prompt: str) -> str:
    response = complete(messages=[{"role": "user", "content": prompt}])
    return response.choices[0].message.content


def call_llm_json(prompt: str) -> dict:
    response = complete(messages=[{"role": "user", "content": prompt}])
    raw_text = response.choices[0].message.content.strip()
    return json.loads(raw_text)
