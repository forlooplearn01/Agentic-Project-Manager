from apps.logger import get_logger
from apps.state import AgentState
from apps.db.db import fetch_active_tasks
from apps.utils.embeddings import get_embeddings_batch
from difflib import SequenceMatcher
import re
import numpy as np

# Initialize logger
logger = get_logger("MATCH")

FUZZY_THRESHOLD = 0.60
SEMANTIC_THRESHOLD = 0.55


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return text


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _as_float_array(x) -> np.ndarray:
    # psycopg2's pgvector adapter returns a pgvector.Vector wrapper for values read
    # back from a `vector` column, not a plain list - np.array(..., dtype=float) fails
    # directly on it. to_list() unwraps it; already-plain lists/arrays pass through.
    if hasattr(x, "to_list"):
        x = x.to_list()
    return np.array(x, dtype=float)


def cosine_sim(a, b) -> float:
    # Compute cosine similarity in Python using numpy - avoids a DB round-trip per event.
    a, b = _as_float_array(a), _as_float_array(b)
    norm_a, norm_b = np.linalg.norm(a), np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def infer_event_type(event: dict, matched: bool) -> str:
    """Infer event_type from populated fields and whether a DB match was found.
    Priority order ensures the most specific type is selected first."""
    if not matched:
        return "new_task"
    if event.get("status"):
        return "status_change"
    if event.get("blocker"):
        return "blocker"
    if event.get("dependency"):
        return "dependency"
    if event.get("deadline"):
        return "deadline_change"
    if event.get("owner"):
        return "owner_change"
    return "task_update"


def _apply_match(event: dict, matched_task: dict, method: str, score: float, confidence: str):
    """Write match result fields onto the event dict in place."""
    event["is_matched"] = True
    event["matched_task_id"] = matched_task.get("task_id")
    event["matched_task_text"] = matched_task.get("task_text")
    event["match_method"] = method
    event["match_confidence"] = confidence
    event["event_type"] = infer_event_type(event, matched=True)


def task_match_node(state: AgentState) -> AgentState:
    logger.info("=" * 60)
    logger.info("Running Match Node")

    normalized_events = state.get("normalized_events", [])
    project_id = state.get("project_id", "default")

    # Fetch once - includes task_embedding so we can do semantic similarity in Python
    existing_tasks = fetch_active_tasks(project_id)
    tasks_with_embeddings = [t for t in existing_tasks if t.get("task_embedding") is not None]

    matched_events = []
    # Track which events still need semantic matching after steps 1-3
    pending_semantic_indices = []

    # -- Pass 1: exact, partial, fuzzy for every event ------------------------
    for event in normalized_events:
        task_text = event.get("task_text", "")

        # Default match fields
        event["is_matched"] = False
        event["matched_task_id"] = None
        event["matched_task_text"] = None
        event["match_method"] = None
        event["match_confidence"] = "low"

        if not task_text:
            event["event_type"] = "new_task"
            matched_events.append(event)
            continue

        norm_event_text = normalize_text(task_text)
        best_match = None
        best_score = 0.0

        # Step 1: Partial/contains match - applied to all events.
        # Kept as its own step (not folded into fuzzy) because SequenceMatcher's ratio is
        # length-sensitive: a short string fully contained in a much longer one can score
        # well below the fuzzy threshold despite being a genuine containment match.
        for task in existing_tasks:
            norm_task_text = normalize_text((task.get("task_text") or "").strip())
            if not norm_task_text:
                continue
            if norm_event_text in norm_task_text or norm_task_text in norm_event_text:
                _apply_match(event, task, "partial", 0.90, "high")
                best_match = task
                break

        # Step 2: Fuzzy match - unified threshold for all events. Also catches exact
        # duplicates (ratio=1.0 >= 0.85 -> "high" confidence), so a dedicated exact-match
        # pass isn't needed: it would only relabel the method, never change the outcome.
        if not best_match:
            for task in existing_tasks:
                norm_task_text = normalize_text(task.get("task_text", "").strip())
                if not norm_task_text:
                    continue
                score = similarity(norm_event_text, norm_task_text)
                if score > best_score:
                    best_score = score
                    best_match = task

            if best_match and best_score >= FUZZY_THRESHOLD:
                confidence = "high" if best_score >= 0.85 else "medium"
                _apply_match(event, best_match, "fuzzy", best_score, confidence)
            else:
                best_match = None

        if best_match:
            logger.info(
                f"Matched '{task_text}' -> task_id={event['matched_task_id']}"
                f" via {event['match_method']} event_type={event['event_type']}"
            )
        else:
            # Needs semantic - defer to Pass 2
            pending_semantic_indices.append(len(matched_events))

        matched_events.append(event)

    # -- Pass 2: batch semantic for all unmatched events -----------------------
    if pending_semantic_indices and tasks_with_embeddings:
        # Embed all pending texts in one model.encode() call
        pending_texts = [matched_events[i].get("task_text", "") for i in pending_semantic_indices]
        query_embeddings = get_embeddings_batch(pending_texts)

        for idx, query_emb in zip(pending_semantic_indices, query_embeddings):
            event = matched_events[idx]
            if query_emb is None:
                event["event_type"] = "new_task"
                continue

            best_match = None
            best_score = 0.0

            for task in tasks_with_embeddings:
                score = cosine_sim(query_emb, task["task_embedding"])
                if score > best_score:
                    best_score = score
                    best_match = task

            if best_match and best_score >= SEMANTIC_THRESHOLD:
                confidence = "high" if best_score >= 0.85 else ("medium" if best_score >= 0.65 else "low")
                if confidence == "low":
                    # Score too weak to trust as a real match - treat as new task
                    event["event_type"] = "new_task"
                    logger.info(f"Semantic score too low ({best_score:.2f}) for '{event['task_text']}' -> new_task")
                else:
                    _apply_match(event, best_match, "semantic", best_score, confidence)
                    logger.info(
                        f"Semantic matched '{event['task_text']}' -> task_id={event['matched_task_id']}"
                        f" score={best_score:.2f} event_type={event['event_type']}"
                    )
            else:
                event["event_type"] = "new_task"
                logger.info(f"No match found for '{event['task_text']}' -> new_task")

    # Mark any remaining unmatched events that skipped semantic (no tasks with embeddings)
    for i in pending_semantic_indices:
        if matched_events[i].get("event_type") is None:
            matched_events[i]["event_type"] = "new_task"

    state["matched_events"] = matched_events
    state["events_to_update"] = [e for e in matched_events if e.get("is_matched")]
    state["events_to_create"] = [e for e in matched_events if not e.get("is_matched")]
    state["unresolved_events"] = []  # force-match concept removed; all unmatched become new tasks

    logger.info(
        f"Match summary: total={len(matched_events)}, "
        f"matched={len(state['events_to_update'])}, "
        f"new={len(state['events_to_create'])}, "
        f"semantic_batch_size={len(pending_semantic_indices)}"
    )

    return state
