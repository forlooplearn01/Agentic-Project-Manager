"""REFERENCE reconstruction (used only by eval/nodes/eval_match_seeded.py as a comparison baseline) of the OLD 3-stage match.py (exact -> partial/containment -> fuzzy ->
semantic), LLM gate OFF. Rebuilt from DECISIONS.md "Production matching logic simplified" since
the original was deleted and there is no git history. NOT part of the live app and never imported by it.

Assumptions where DECISIONS.md is silent:
- partial: normalized-text containment in either direction, non-empty task text, NO length guard,
  first hit in existing_tasks order, hardcoded score 0.90, confidence "high".
- fuzzy: difflib.SequenceMatcher ratio on normalized text, best score across existing tasks,
  accepted at >= 0.60; "medium" for 0.60-0.84, "high" for >= 0.85 (only the 0.60-0.84 medium band
  is stated in DECISIONS.md; the >=0.85 "high" label is assumed, it doesn't change match/no-match).
- semantic: unchanged from the live file (0.55 threshold, high >=0.85 / medium >=0.65 / low discarded)
  applied directly, no LLM confirmation."""
from collections import Counter
from difflib import SequenceMatcher
import re

import numpy as np

from apps.logger import get_logger
from apps.state import AgentState
from apps.db.db import fetch_active_tasks
from apps.utils.embeddings import get_embeddings_batch

logger = get_logger("MATCH_OLD3STAGE")

SEMANTIC_THRESHOLD = 0.55
FUZZY_THRESHOLD = 0.60
PARTIAL_SCORE = 0.90

STATS = Counter()  # match_method -> count, across every task_match_node call in this process


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return text


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _as_float_array(x) -> np.ndarray:
    if hasattr(x, "to_list"):
        x = x.to_list()
    return np.array(x, dtype=float)


def cosine_sim(a, b) -> float:
    a, b = _as_float_array(a), _as_float_array(b)
    norm_a, norm_b = np.linalg.norm(a), np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def infer_event_type(event: dict, matched: bool) -> str:
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
    event["is_matched"] = True
    event["matched_task_id"] = matched_task.get("task_id")
    event["matched_task_text"] = matched_task.get("task_text")
    event["match_method"] = method
    event["match_confidence"] = confidence
    event["event_type"] = infer_event_type(event, matched=True)


def task_match_node(state: AgentState) -> AgentState:
    normalized_events = state.get("normalized_events", [])
    project_id = state.get("project_id", "default")

    existing_tasks = fetch_active_tasks(project_id)
    tasks_with_embeddings = [t for t in existing_tasks if t.get("task_embedding") is not None]

    matched_events = []
    pending_semantic_indices = []

    for event in normalized_events:
        task_text = event.get("task_text", "")

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

        # Stage 1: exact
        exact_match = None
        for task in existing_tasks:
            norm_task_text = normalize_text((task.get("task_text") or "").strip())
            if norm_task_text and norm_task_text == norm_event_text:
                exact_match = task
                break
        if exact_match:
            _apply_match(event, exact_match, "exact", 1.0, "high")
            matched_events.append(event)
            continue

        # Stage 2: partial / containment - no length guard, first hit wins, hardcoded 0.90
        partial_match = None
        for task in existing_tasks:
            norm_task_text = normalize_text((task.get("task_text") or "").strip())
            if norm_task_text and (norm_event_text in norm_task_text or norm_task_text in norm_event_text):
                partial_match = task
                break
        if partial_match:
            _apply_match(event, partial_match, "partial", PARTIAL_SCORE, "high")
            matched_events.append(event)
            continue

        # Stage 3: fuzzy (SequenceMatcher), best candidate, medium band 0.60-0.84
        best_fuzzy, best_fuzzy_score = None, 0.0
        for task in existing_tasks:
            norm_task_text = normalize_text((task.get("task_text") or "").strip())
            if not norm_task_text:
                continue
            score = similarity(norm_event_text, norm_task_text)
            if score > best_fuzzy_score:
                best_fuzzy, best_fuzzy_score = task, score
        if best_fuzzy and best_fuzzy_score >= FUZZY_THRESHOLD:
            confidence = "high" if best_fuzzy_score >= 0.85 else "medium"
            _apply_match(event, best_fuzzy, "fuzzy", best_fuzzy_score, confidence)
            matched_events.append(event)
            continue

        pending_semantic_indices.append(len(matched_events))
        matched_events.append(event)

    # Stage 4: semantic, applied directly (LLM gate OFF)
    if pending_semantic_indices and tasks_with_embeddings:
        pending_texts = [matched_events[i].get("task_text", "") for i in pending_semantic_indices]
        query_embeddings = get_embeddings_batch(pending_texts)

        for idx, query_emb in zip(pending_semantic_indices, query_embeddings):
            event = matched_events[idx]
            if query_emb is None:
                event["event_type"] = "new_task"
                continue

            best_match, best_score = None, 0.0
            for task in tasks_with_embeddings:
                score = cosine_sim(query_emb, task["task_embedding"])
                if score > best_score:
                    best_score, best_match = score, task

            if best_match and best_score >= SEMANTIC_THRESHOLD:
                confidence = "high" if best_score >= 0.85 else ("medium" if best_score >= 0.65 else "low")
                if confidence == "low":
                    event["event_type"] = "new_task"
                else:
                    _apply_match(event, best_match, "semantic", best_score, confidence)
            else:
                event["event_type"] = "new_task"

    for i in pending_semantic_indices:
        if matched_events[i].get("event_type") is None:
            matched_events[i]["event_type"] = "new_task"

    for e in matched_events:
        if e.get("is_matched"):
            STATS[e["match_method"]] += 1

    state["matched_events"] = matched_events
    state["events_to_update"] = [e for e in matched_events if e.get("is_matched")]
    state["events_to_create"] = [e for e in matched_events if not e.get("is_matched")]
    state["unresolved_events"] = []
    return state
