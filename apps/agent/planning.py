import json
from pathlib import Path

from apps.agent.react_loop import run_react_loop
from apps.agent.tool_registry import PLANNING_TOOL_SCHEMAS, PLANNING_TOOL_IMPLS
from apps.logger import get_logger

logger = get_logger("PLANNING")

_MANIFEST_PATH = Path(__file__).parent / "manifests" / "planning.md"
_MANIFEST = _MANIFEST_PATH.read_text()

_SYSTEM_PROMPT = f"""{_MANIFEST}

## Priority rules
- high: urgent, foundational, or blocking multiple other tasks
- medium: important but not foundational, not blocking multiple tasks
- low: final verification (QA, UAT, sign-off) or post-setup work
- A prerequisite task must have equal or higher priority than tasks that depend on it -
  use search_tasks/get_task_by_id to check this when a task's ordering isn't obvious
  from its text alone.

## Deadline rules
- Only set a deadline if the input gives one, or check_owner_availability suggests a
  concrete date. Do not invent a deadline from nothing.
- If check_owner_availability shows the owner is busy through the requested date,
  note that in your reasoning but still return the plan - you are not blocked from
  finishing, just informed.

## Dependency rules
- Each input task may include a `dependency` hint (free text extracted from the
  document, e.g. "Testing depends on deployment completion"). Use it, plus
  search_tasks/get_task_by_id, to identify real dependency relationships.
- `record_dependency` only works against a task_id that already exists in the
  database - call it when a task depends on a task search_tasks/get_task_by_id
  found (an existing, already-persisted task). It CANNOT be called for a
  dependency between two brand-new tasks in this same batch, since neither has a
  task_id yet - for that case, set `depends_on_task_text` in your output instead
  (the exact task text of the other task in this same batch it depends on) and the
  system resolves it once both are persisted.

## Output format
When you are done reasoning and have called whatever tools you needed, respond with
ONLY a JSON object, no other text:
{{"planned_tasks": [{{"task": "<task text>", "priority": "high"|"medium"|"low",
"deadline": "<YYYY-MM-DD or null>", "depends_on_task_text": "<other task text in
this batch, or null>"}}, ...]}}
Include every task passed to you, in the same order.
"""


def run_planning_agent(tasks_to_plan: list, project_id: str) -> tuple:
    """tasks_to_plan: list of {task, owner, deadline, blocker, confidence} dicts
    (built from events_to_create + events_to_update, same shape the old prioritize
    node consumed). Returns (planned_tasks, trace)."""
    if not tasks_to_plan:
        logger.info("No tasks to plan. Skipping.")
        return [], []

    user_message = json.dumps({"project_id": project_id, "tasks": tasks_to_plan})

    try:
        result, trace = run_react_loop(
            agent_name="planning",
            system_prompt=_SYSTEM_PROMPT,
            user_message=user_message,
            tool_schemas=PLANNING_TOOL_SCHEMAS,
            tool_impls=PLANNING_TOOL_IMPLS,
            max_tool_calls=5,
        )
    except Exception as e:
        logger.error(f"Planning agent failed: {e}")
        # Fallback: pass tasks through with default medium priority, no deadline change -
        # mirrors the old prioritize node's failure behavior so a bad LLM response never
        # blocks the rest of the pipeline.
        return [
            {"task": t["task"], "priority": "medium", "deadline": t.get("deadline"), "depends_on_task_text": None}
            for t in tasks_to_plan
        ], []

    planned = result.get("planned_tasks", [])
    if not isinstance(planned, list) or not planned:
        logger.warning("Planning agent returned no usable plan, using fallback")
        return [
            {"task": t["task"], "priority": "medium", "deadline": t.get("deadline")}
            for t in tasks_to_plan
        ], trace

    valid_priorities = {"high", "medium", "low"}
    cleaned = []
    for item in planned:
        if not isinstance(item, dict) or "task" not in item:
            continue
        priority = str(item.get("priority", "medium")).lower()
        if priority not in valid_priorities:
            priority = "medium"
        cleaned.append({
            "task": item["task"].strip(),
            "priority": priority,
            "deadline": item.get("deadline") or None,
            "depends_on_task_text": (item.get("depends_on_task_text") or "").strip() or None,
        })

    logger.info(f"Planned {len(cleaned)} tasks via {len(trace)} tool call(s)")
    return cleaned, trace
