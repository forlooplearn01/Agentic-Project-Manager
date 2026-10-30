"""Shared fixtures for the deterministic test suite (tests/unit, tests/db, tests/
pipeline_logic). Real-LLM evaluation lives separately in eval/ - see DECISIONS.md's
testing entry for why the two are kept apart.

Runs inside the api container (docker compose exec api pytest tests/) so it reuses the
exact same DB_HOST/DB_PORT and installed dependencies as the running app - no separate
test environment to keep in sync.
"""
import json
import uuid

import pytest

from apps.db.db import ensure_duplicate_handling_schema, ensure_registers_schema, get_connection
from apps.db.hours_db import ensure_hours_schema


@pytest.fixture
def test_project():
    """Yields a unique, disposable project_id and guarantees every row created under it
    is deleted afterward, regardless of test outcome - the exact manual cleanup pattern
    used throughout this project's own development (rag-test, litellm-verify, etc.),
    formalized so it happens automatically instead of by hand every time.

    Deletes across every table a project's data can land in - deliberately more thorough
    than production's delete_project(), which intentionally preserves project_people for
    real users. Test data has no such reason to survive."""
    ensure_duplicate_handling_schema()
    ensure_registers_schema()
    ensure_hours_schema()
    project_id = f"test-{uuid.uuid4().hex[:10]}"
    try:
        yield project_id
    finally:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM plan_changes WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM time_entries WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM project_hours_settings WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM hours_imports WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM duplicate_decisions WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM raid_items WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM deliverables WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM document_chunks WHERE project_id = %s", (project_id,))
        cur.execute(
            "DELETE FROM task_events WHERE source_doc_id IN "
            "(SELECT doc_id FROM source_documents WHERE project_id = %s)",
            (project_id,),
        )
        cur.execute("DELETE FROM source_documents WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM meetings WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM task_dependencies WHERE task_id IN "
                    "(SELECT task_id FROM tasks WHERE project_id = %s)", (project_id,))
        cur.execute("DELETE FROM tasks WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM project_people WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM project_integrations WHERE project_id = %s", (project_id,))
        conn.commit()
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Fake LLM responses for pipeline-logic tests (tests/pipeline_logic) - these mock
# apps.agent.react_loop.complete/apps.supervisor.complete to script a fixed sequence of
# responses, so what's under test is OUR loop-control code (budget enforcement, the
# JSON-retry fallback), never a real model's judgment. Shaped to match exactly what
# react_loop.py actually reads off a response: .choices[0].message.{content,tool_calls},
# and .model_dump(exclude_none=True) on the message when it's re-appended to the
# conversation after a tool-call round.
# ---------------------------------------------------------------------------

class _FakeFunction:
    def __init__(self, name, arguments_dict):
        self.name = name
        self.arguments = json.dumps(arguments_dict)


class _FakeToolCall:
    def __init__(self, call_id, name, arguments_dict):
        self.id = call_id
        self.function = _FakeFunction(name, arguments_dict)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none=True):
        d = {"role": "assistant"}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in self.tool_calls
            ]
        return d


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


def make_tool_call_response(call_id: str, tool_name: str, args: dict) -> "_FakeResponse":
    """A response where the model wants to call exactly one tool."""
    return _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall(call_id, tool_name, args)]))


def make_final_json_response(data: dict) -> "_FakeResponse":
    """A response where the model is done and returns its final JSON answer."""
    return _FakeResponse(_FakeMessage(content=json.dumps(data)))


def make_text_response(text: str) -> "_FakeResponse":
    """A response with plain (non-JSON) text - used to test the forced-retry fallback."""
    return _FakeResponse(_FakeMessage(content=text))


@pytest.fixture
def scripted_llm(monkeypatch):
    """Patches complete() to return a scripted sequence of fake responses in order, one
    per call. Defaults to apps.agent.react_loop.complete (where most pipeline-logic tests
    live); pass target= for a different caller, e.g. apps.supervisor.complete.
    Usage: scripted_llm([make_tool_call_response(...), make_final_json_response(...)])."""
    def _install(responses, target="apps.agent.react_loop.complete"):
        it = iter(responses)
        monkeypatch.setattr(target, lambda **kwargs: next(it))
    return _install
