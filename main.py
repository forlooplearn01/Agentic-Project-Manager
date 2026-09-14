import argparse
import sys
import json

from dotenv import load_dotenv

from apps.graph import build_graph
from apps.logger import get_logger

# Load environment variables from .env (OPENAI_API_KEY, DB_*, GOOGLE_*, JIRA_*)
load_dotenv()
logger = get_logger("MAIN")


def _initial_state(input_text: str, project_id: str) -> dict:
    return {
        "raw_input": input_text,
        "project_id": project_id,
        "document_type": "",
        "tasks": [],
        "normalized_events": [],
        "followup_signal": False,
        "matched_events": [],
        "events_to_update": [],
        "events_to_create": [],
        "unresolved_events": [],
        "planned_tasks": [],
        "planning_trace": [],
        "sync_reporting_triggered": False,
        "sync_reporting_trace": [],
        "sync_reporting_result": None,
        "meeting_coordinator_triggered": False,
        "meeting_coordinator_trace": [],
        "scheduled_meeting": None,
        "final_output": {},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agentic Project Manager CLI")
    parser.add_argument(
        "--project-id",
        default="default",
        help="Project ID - scopes task matching, dependencies, and people to this project (default: 'default')",
    )
    args = parser.parse_args()
    project_id = args.project_id

    print(f"Project: {project_id}")
    print("Reading input from stdin... (use: python3 main.py --project-id <project> < input.txt)\n")

    user_input = sys.stdin.read().strip()

    graph = build_graph()
    result = graph.invoke(_initial_state(user_input, project_id))

    logger.info(f"document_type: {result.get('document_type')}")
    logger.info(f"Tasks processed: {result.get('final_output', {}).get('tasks_count', 0)}")
    logger.info(f"Planning tool calls: {len(result.get('planning_trace', []))}")
    logger.info(f"Sync/Reporting triggered: {result.get('sync_reporting_triggered')}")
    logger.info(f"Meeting Coordinator triggered: {result.get('meeting_coordinator_triggered')}")

    print()
    print("=== JSON_OUTPUT_BEGIN ===")
    print(json.dumps(result.get("final_output", {}), indent=2, default=str))
    print("=== JSON_OUTPUT_END ===")
