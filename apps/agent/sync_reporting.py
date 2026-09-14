import json
from pathlib import Path

from apps.agent.react_loop import run_react_loop
from apps.agent.tool_registry import SYNC_REPORTING_TOOL_SCHEMAS, build_sync_reporting_impls
from apps.logger import get_logger

logger = get_logger("SYNC_REPORTING")

_MANIFEST_PATH = Path(__file__).parent / "manifests" / "sync_reporting.md"
_MANIFEST = _MANIFEST_PATH.read_text()

_SYSTEM_PROMPT = f"""{_MANIFEST}

## Output format
Respond with ONLY a JSON object, no other text:
{{"jira_synced": ["<task_id>", ...], "report_generated": true|false,
"report_path": "<path or null>"}}
"""


def should_trigger_sync_reporting(state: dict) -> bool:
    """Cheap, deterministic pre-filter (mirrors normalize's followup_signal pattern) -
    only invoke the agent (and its LLM calls) when this run actually changed something.
    A run that only confirmed existing state with no new/changed task should not pay
    for a Sync/Reporting agent call at all."""
    if state.get("events_to_create"):
        return True
    for event in state.get("matched_events", []):
        if event.get("event_type") not in ("new_task", None):
            return True
    return False


def run_sync_reporting_agent(state: dict) -> tuple:
    """Returns (result_dict, trace). Only meaningful to call when
    should_trigger_sync_reporting(state) is True."""
    project_id = state.get("project_id", "default")
    changed_tasks = [
        {
            "task_id": e.get("matched_task_id"),
            "task_text": e.get("task_text"),
            "event_type": e.get("event_type"),
            "owner": e.get("owner"),
            "status": e.get("status"),
        }
        for e in state.get("matched_events", [])
        if e.get("matched_task_id")
    ]

    if not changed_tasks:
        logger.info("Sync/Reporting triggered but no linked tasks found - skipping.")
        return {"jira_synced": [], "report_generated": False, "report_path": None}, []

    user_message = json.dumps({"project_id": project_id, "changed_tasks": changed_tasks})

    try:
        result, trace = run_react_loop(
            agent_name="sync_reporting",
            system_prompt=_SYSTEM_PROMPT,
            user_message=user_message,
            tool_schemas=SYNC_REPORTING_TOOL_SCHEMAS,
            tool_impls=build_sync_reporting_impls(project_id),
            max_tool_calls=4,
        )
    except Exception as e:
        logger.error(f"Sync/Reporting agent failed: {e}")
        return {"jira_synced": [], "report_generated": False, "report_path": None}, []

    # Grounding check: don't trust the model's self-reported summary at face value -
    # derive jira_synced/report_generated/report_path from what it actually called.
    grounded = _ground_result_in_trace(trace)
    if grounded != result:
        logger.warning(f"Sync/Reporting self-report didn't match its own trace, correcting: {result} -> {grounded}")

    logger.info(f"Sync/Reporting done via {len(trace)} tool call(s): {grounded}")
    return grounded, trace


def _ground_result_in_trace(trace: list) -> dict:
    jira_synced = []
    report_generated = False
    report_path = None

    for entry in trace:
        try:
            result_data = json.loads(entry.get("result", "{}"))
        except json.JSONDecodeError:
            continue

        if entry.get("tool") == "sync_task_to_jira" and result_data.get("status") == "created":
            jira_synced.append(result_data.get("task_id"))
        elif entry.get("tool") == "generate_report" and "error" not in result_data:
            report_generated = True
            report_path = result_data.get("file", report_path)

    return {
        "jira_synced": jira_synced,
        "report_generated": report_generated,
        "report_path": report_path,
    }
