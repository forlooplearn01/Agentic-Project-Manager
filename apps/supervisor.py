import json
import re

from apps.db.db import count_tasks_for_project
from apps.llm import client
from apps.logger import get_logger
from apps.state import AgentState

logger = get_logger("SUPERVISOR")

# Strong lexical markers that a document is an SOW even if the project already has tasks
# (e.g. a new phase/revision for an existing project) - triggers the tie-breaker instead
# of trusting the task-count heuristic blindly.
_SOW_MARKERS = re.compile(
    r"\bstatement of work\b|\bscope of work\b|\bdeliverables?\s*:|\bthis agreement\b",
    re.IGNORECASE,
)


def _llm_tiebreak(raw_input: str) -> str:
    # The one conditional LLM call in the Supervisor - only reached when the cheap,
    # deterministic signal (task count) conflicts with a lexical signal in the document
    # itself. Every other case is resolved by count_tasks_for_project alone, no LLM call.
    prompt = f"""Classify this document as exactly one of "sow" or "mom" (meeting minutes).
An SOW describes scope/deliverables for a project or a new phase of one. Meeting minutes
record what was discussed/decided/updated in a meeting about an already-scoped project.

Return only JSON: {{"document_type": "sow" | "mom"}}

### Document
{raw_input[:3000]}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        doc_type = result.get("document_type")
        return doc_type if doc_type in ("sow", "mom") else "mom"
    except Exception as e:
        logger.error(f"Supervisor tie-break LLM call failed: {e}, defaulting to mom")
        return "mom"


def supervisor_node(state: AgentState) -> AgentState:
    logger.info("=" * 60)
    logger.info("Running Supervisor")

    project_id = state.get("project_id", "default")
    raw_input = state.get("raw_input", "")
    existing_task_count = count_tasks_for_project(project_id)
    looks_like_sow = bool(_SOW_MARKERS.search(raw_input))

    if existing_task_count == 0:
        document_type = "sow"
    elif not looks_like_sow:
        document_type = "mom"
    else:
        # Conflict: project already has tasks, but this document reads like a fresh SOW
        # (e.g. a new phase). Only case that pays for an LLM call.
        logger.info("Ambiguous: project has existing tasks but document has SOW markers - tie-breaking")
        document_type = _llm_tiebreak(raw_input)

    state["document_type"] = document_type
    logger.info(f"document_type={document_type} (existing_tasks={existing_task_count}, sow_markers={looks_like_sow})")

    return state
