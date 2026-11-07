import os
import json
from dotenv import load_dotenv
import litellm

load_dotenv()

# Every LLM call in this app routes through complete() below, so provider/model choice is
# entirely a .env concern - no code change needed to switch OpenAI, Anthropic, Gemini, or a
# self-hosted open-source model (Ollama, vLLM, etc.). LLM_API_KEY falls back to
# OPENAI_API_KEY and LLM_MODEL defaults to gpt-4o-mini, so leaving all of these unset
# reproduces the exact previous behavior - this is a non-disruptive swap, not a breaking one.
_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
_API_BASE = os.getenv("LLM_API_BASE") or None
# Single fallback model, tried only if the primary call fails (e.g. a self-hosted server
# down) - not a load-balancing/retry policy, just the simple case this app needs today.
_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL") or None

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


def complete(messages: list, tools: list = None, response_format: dict = None, temperature: float = 0, model: str = None):
    """Single choke point for every LLM call in this app - apps/agent/react_loop.py and
    apps/supervisor.py call this directly for tool-calling/JSON-forced calls, call_llm/
    call_llm_json below wrap it for the simple prompt-in-text-out case. Returns litellm's
    response object, which mirrors the OpenAI SDK's response shape (response.choices[0]
    .message.content, .tool_calls, etc.) regardless of which provider actually served it."""
    kwargs = {
        "model": model or _MODEL,
        "messages": messages,
        "api_key": _API_KEY,
    }
    # None omits the parameter: reasoning models (gpt-5, o-series) reject temperature=0.
    if temperature is not None:
        kwargs["temperature"] = temperature
    if _API_BASE:
        kwargs["api_base"] = _API_BASE
    if _EXTRA_HEADERS:
        kwargs["extra_headers"] = _EXTRA_HEADERS
    if _EXTRA_BODY:
        kwargs["extra_body"] = _EXTRA_BODY
    if _FALLBACK_MODEL:
        kwargs["fallbacks"] = [_FALLBACK_MODEL]
    if tools is not None:
        kwargs["tools"] = tools
    if response_format is not None:
        kwargs["response_format"] = response_format

    return litellm.completion(**kwargs)


def call_llm(prompt: str) -> str:
    response = complete(messages=[{"role": "user", "content": prompt}])
    return response.choices[0].message.content


def call_llm_json(prompt: str) -> dict:
    response = complete(messages=[{"role": "user", "content": prompt}])
    raw_text = response.choices[0].message.content.strip()
    return json.loads(raw_text)
