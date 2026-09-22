from apps.logger import get_logger
from apps.state import AgentState
from apps.db.db import (
    insert_task,
    insert_event,
    update_task_field,
    fetch_task_by_normalized_text,
    fetch_semantic_task_match,
    log_unresolved_event,
    upsert_source_document,
    add_task_dependency,
)
import re

# Initialize logger for this node
logger = get_logger("FORMAT")


def task_format_node(state: AgentState) -> AgentState:
    # Log entry into final formatter node
    logger.info("=" * 60)  # separator for each new node run
    logger.info("Running Final Format Node")

    # Store new tasks in DB and apply updates before preparing final output
    store_tasks(state)
    store_events(state)
    apply_task_updates(state)
    log_unresolved_events(state)
    record_same_batch_dependencies(state)

    # Convert Planning agent's output into clean final output format
    formatted_tasks = []

    for item in state.get("planned_tasks", []):
        if isinstance(item, dict):
            task_text = item.get("task", "").strip()

            # Normalize priority value
            priority = item.get("priority", "medium")
            priority = priority.lower() if isinstance(priority, str) else "medium"
            if priority not in {"high", "medium", "low"}:
                priority = "medium"

            formatted_tasks.append({
                "task": task_text,
                "priority": priority,
                "deadline": item.get("deadline"),
            })

    # Summarise unresolved events for the agent (strip source_doc_id - UUID reference not useful at agent level)
    unresolved_summary = [
        {k: v for k, v in event.items() if k != "source_doc_id"}
        for event in state.get("unresolved_events", [])
    ]

    # Expose non-new_task matched events so downstream agents can react to event types (blocker, deadline_change, etc.)
    # Strip source_doc_id - UUID reference not useful at the agent level
    event_signals = [
        {k: v for k, v in event.items() if k != "source_doc_id"}
        for event in state.get("matched_events", [])
        if event.get("event_type") not in ("new_task", None)
    ]

    # Prepare final output payload. This node runs BEFORE sync_reporting/meeting_coordinator
    # (it must - they need the real task_ids store_tasks() just assigned), so their fields
    # aren't known yet here; the finalize node appends them once those agents have run.
    state["final_output"] = {
        "tasks_count": len(formatted_tasks),
        "tasks": formatted_tasks,
        "event_signals": event_signals,
        "unresolved_events": unresolved_summary,
        "planning_trace": state.get("planning_trace", []),
        "status": "completed",
    }

    logger.info(f"Final output prepared with {len(formatted_tasks)} tasks, "
                f"{len(unresolved_summary)} unresolved events")

    return state


def normalize_text(text: str) -> str:
    # Normalize text for DB storage consistency
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return text


def _refresh_task(task_id, task_data: dict):
    # Re-apply priority on an existing task found via dedup.
    # Keeps the task's enriched fields current without creating a duplicate row.
    if task_data.get("priority"):
        update_task_field(task_id, "priority", task_data["priority"])


def store_tasks(state: AgentState):
    logger.info(f"store_tasks called. events_to_create count = {len(state.get('events_to_create', []))}")
    events_to_create = state.get("events_to_create", [])
    project_id = state.get("project_id", "default")

    # Build lookup from Planning agent's output by normalized task text
    priority_lookup = {
        normalize_text(item.get("task", "")): item
        for item in state.get("planned_tasks", [])
        if isinstance(item, dict) and item.get("task")
    }

    for event in events_to_create:
        if event.get("event_type") == "new_task" and not event.get("is_matched"):
            norm_text = normalize_text(event.get("task_text", ""))
            enriched = priority_lookup.get(norm_text, {})

            task_data = {
                "task_text": event.get("task_text"),
                "normalized_task_text": norm_text,
                "owner": event.get("owner"),
                "status": event.get("status") or "not_started",
                "deadline": event.get("deadline"),
                "blocker": event.get("blocker"),
                "priority": enriched.get("priority", "medium"),
            }

            # Deduplication guard - exact normalized text match (scoped to project)
            existing_task_id = fetch_task_by_normalized_text(norm_text, project_id)
            if existing_task_id:
                logger.info(f"Dedup (exact): task already exists (task_id={existing_task_id}), skipping insert for '{task_data['task_text']}'")
                event["matched_task_id"] = existing_task_id
                event["is_matched"] = True
                event["matched_task_text"] = event.get("task_text")
                event["match_method"] = "dedup"
                event["match_confidence"] = "high"
                _refresh_task(existing_task_id, task_data)
                continue

            # Deduplication guard - semantic similarity fallback (scoped to project).
            # Threshold is deliberately high (0.85, not match.py's 0.55 lower bound): this guard
            # exists only to catch a genuinely concurrent insert that match.py's earlier snapshot
            # missed (a race condition), not to re-litigate match.py's own low-confidence call -
            # match.py already treats 0.55-0.65 as "too weak to trust, call it new", and this guard
            # must not silently override that decision by re-matching the same case as a duplicate.
            semantic_match = fetch_semantic_task_match(task_data["task_text"], project_id)
            if semantic_match and semantic_match.get("similarity_score", 0) >= 0.85:
                existing_task_id = semantic_match["task_id"]
                score = semantic_match["similarity_score"]
                logger.info(f"Dedup (semantic {score:.2f}): task already exists (task_id={existing_task_id}), skipping insert for '{task_data['task_text']}'")
                event["matched_task_id"] = existing_task_id
                event["is_matched"] = True
                event["matched_task_text"] = semantic_match.get("task_text")
                event["match_method"] = "dedup_semantic"
                event["match_confidence"] = "high" if score >= 0.85 else "medium"
                _refresh_task(existing_task_id, task_data)
                continue

            logger.info(f"Attempting to insert new task: {task_data['task_text']}")
            try:
                task_id = insert_task(task_data, project_id)
                logger.info(f"Inserted task successfully: {task_id}")
            except Exception as e:
                logger.error(f"Task insert failed for '{task_data['task_text']}': {e}")
                continue

            event["matched_task_id"] = task_id
            event["is_matched"] = True
            event["matched_task_text"] = event.get("task_text")
            event["match_method"] = "created"  # explicitly mark as newly created task
            event["match_confidence"] = "high"  # new tasks are always high confidence


def store_events(state: AgentState):
    # Insert all matched events into task_events history table.
    # The source document is stored once in source_documents and all events
    # reference it via source_doc_id - not by duplicating the full text per row.
    matched_events = state.get("matched_events", [])
    raw_input = state.get("raw_input", "")

    if not matched_events:
        return

    doc_id = upsert_source_document(raw_input, state.get("document_type"), state.get("project_id"))

    for event in matched_events:
        event["source_doc_id"] = doc_id
        insert_event(event)


def apply_task_updates(state: AgentState):
    # Apply updates to existing matched tasks based on event type
    matched_events = state.get("matched_events", [])

    # Build lookup from Planning agent's output by normalized task text
    priority_lookup = {
        normalize_text(item.get("task", "")): item
        for item in state.get("planned_tasks", [])
        if isinstance(item, dict) and item.get("task")
    }

    for event in matched_events:
        task_id = event.get("matched_task_id")
        event_type = event.get("event_type")

        # Skip events that are not linked to a task record
        if not task_id:
            continue

        # Update status for status_change events
        if event_type == "status_change" and event.get("status"):
            update_task_field(task_id, "status", event.get("status"))

        # Update deadline for deadline_change events
        elif event_type == "deadline_change" and event.get("deadline"):
            update_task_field(task_id, "deadline", event.get("deadline"))

        # Update owner for owner_change events
        elif event_type == "owner_change" and event.get("owner"):
            update_task_field(task_id, "owner", event.get("owner"))

        # Update blocker and mark task status as blocked for blocker events
        elif event_type == "blocker":
            update_task_field(task_id, "blocker", event.get("blocker"))
            update_task_field(task_id, "status", event.get("status"))

        # Generic update: write any populated fields (owner, deadline, status, blocker)
        elif event_type == "task_update":
            for field in ("owner", "deadline", "status", "blocker"):
                if event.get(field):
                    update_task_field(task_id, field, event.get(field))

        # Always re-write priority for matched tasks (enriched field)
        norm_text = normalize_text(event.get("task_text", ""))
        enriched = priority_lookup.get(norm_text, {})

        if enriched.get("priority"):
            update_task_field(task_id, "priority", enriched["priority"])


def log_unresolved_events(state: AgentState):
    # Write unresolved events to the unresolved_events review queue - no task is created.
    # These events failed all 4 match stages and require human review before any action is taken.
    unresolved_events = state.get("unresolved_events", [])

    if not unresolved_events:
        return

    raw_input = state.get("raw_input", "")

    for event in unresolved_events:
        task_text = event.get("task_text", "").strip()
        if not task_text:
            continue

        try:
            log_unresolved_event(event, source_input=raw_input)
            logger.warning(f"Unresolved event logged for review: '{task_text}' (type={event.get('event_type')})")
        except Exception as e:
            logger.error(f"Failed to log unresolved event '{task_text}': {e}")


def record_same_batch_dependencies(state: AgentState):
    # Planning identifies dependencies via depends_on_task_text (free text) rather than a
    # task_id, because a dependency between two brand-new tasks can't be recorded with real
    # ids until store_tasks() above has actually persisted them. Now that it has, resolve
    # text -> task_id for both sides and write the task_dependencies edge. A dependency on
    # an already-existing task (not created this run) also resolves here via the DB lookup
    # fallback - Planning's own record_dependency tool call already handled the case where
    # it discovered that relationship mid-reasoning via search_tasks, this is the remaining
    # case: same-run text hints for both new and update events.
    project_id = state.get("project_id", "default")
    planned_tasks = state.get("planned_tasks", [])
    matched_events = state.get("matched_events", [])

    if not planned_tasks:
        return

    text_to_task_id = {
        normalize_text(e.get("task_text", "")): e.get("matched_task_id")
        for e in matched_events
        if e.get("matched_task_id")
    }

    for item in planned_tasks:
        depends_on_text = item.get("depends_on_task_text")
        if not depends_on_text:
            continue

        task_id = text_to_task_id.get(normalize_text(item.get("task", "")))
        if not task_id:
            continue

        depends_on_id = text_to_task_id.get(normalize_text(depends_on_text))
        if not depends_on_id:
            # Not in this batch - fall back to an existing DB task with matching text.
            depends_on_id = fetch_task_by_normalized_text(normalize_text(depends_on_text), project_id)

        if depends_on_id and depends_on_id != task_id:
            add_task_dependency(task_id, depends_on_id)
            logger.info(f"Recorded dependency: {task_id} depends_on {depends_on_id} ('{item.get('task')}' -> '{depends_on_text}')")