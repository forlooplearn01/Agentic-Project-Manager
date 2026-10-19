from apps.logger import get_logger
from apps.state import AgentState
from apps.db.db import fetch_active_tasks
from apps.utils.embeddings import get_embeddings_batch
from apps.llm import complete
import json
import os
import re
import numpy as np

# Initialize logger
logger = get_logger("MATCH")

# Minimum cosine for an existing task to be offered to the gate as a candidate. 0.65 is the
# former effective floor (0.55 threshold + the "low" band below 0.65 discarded); see DECISIONS.md.
CANDIDATE_MIN_SCORE = 0.65
TOP_K_CANDIDATES = 3
# Also offer tasks whose text CONTAINS (or is contained in) the mention's text as gate candidates
# regardless of cosine - never as an automatic match. Shorter side needs >= MIN_WORDS words.
CONTAINMENT_CANDIDATES = True
# A gate rejection whose top candidate scored at least this is also flagged for human review.
POSSIBLE_DUPLICATE_MIN_SCORE = 0.85
# Model for the gate call only; None = the app-wide LLM_MODEL. Set MATCH_GATE_MODEL to override.
GATE_MODEL = os.getenv("MATCH_GATE_MODEL") or None
# 0 for determinism; None omits the parameter (needed by reasoning models that reject temperature=0).
GATE_TEMPERATURE = 0
CONTAINMENT_MIN_WORDS = 3
MAX_CONTAINMENT_CANDIDATES = 2


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return text


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


def _confidence(score: float) -> str:
    return "high" if score >= 0.85 else ("medium" if score >= 0.65 else "low")


def _contains(a: str, b: str) -> bool:
    """One normalized text contained in the other, shorter side at least CONTAINMENT_MIN_WORDS words."""
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter.split()) >= CONTAINMENT_MIN_WORDS and shorter in longer


def _describe(text, owner=None, status=None, deadline=None) -> str:
    extras = [f"{k}: {v}" for k, v in (("owner", owner), ("status", status), ("deadline", deadline)) if v]
    return f'"{text}"' + (f" ({', '.join(extras)})" if extras else "")


def _llm_confirm_matches(items: list) -> dict:
    """One batched LLM call deciding, per new mention, which of its top candidate existing
    tasks (if any) it refers to. Cosine similarity is a single scalar and can't separate
    "same task reworded" from "different action on the same feature" (measured false
    positives scored 0.66-0.90), and the single closest candidate is sometimes the wrong
    one even when the right one is rank 2-3 - so the model sees up to TOP_K_CANDIDATES
    candidates plus owner/status/deadline hints.

    items: [{"id": int, "new": {text, owner, status, deadline}, "candidates": [{text, owner,
    status, deadline}, ...]}]. Returns {id: (decision, candidate_index)} with decision in
    "same" (candidate_index set), "different", "unsure". An id absent from the result means
    no verdict was obtained (API error / malformed output) - the caller treats that as a new
    task, since a duplicate is cheaper than silently merging two different tasks."""
    if not items:
        return {}
    blocks = []
    for it in items:
        n = it["new"]
        lines = [f'{it["id"]}. NEW: {_describe(n["text"], n.get("owner"), n.get("status"), n.get("deadline"))}']
        for ci, c in enumerate(it["candidates"]):
            lines.append(f'   {chr(97 + ci)}) {_describe(c["text"], c.get("owner"), c.get("status"), c.get("deadline"))}')
        blocks.append("\n".join(lines))
    prompt = f"""Each numbered item is a task NEW mentioned in a meeting, followed by lettered CANDIDATES: existing tasks it might refer to. Decide which candidate, if any, NEW refers to.

"same": NEW is one work item being mentioned again - a rewording, a status update, a follow-up/check-in on it, or its next lifecycle step (draft -> review -> finalize -> deliver of the SAME deliverable). Give the letter of that ONE candidate.
"different": NEW is a separate work item that merely shares a topic with the candidates - a different deliverable (a test document vs. the tests themselves), a different object (access package vs. API credentials), or a distinct sub-task of a larger feature (file locking vs. the file format).
"unsure": you genuinely cannot tell from the text. Use sparingly - only when both readings are reasonable.

Ask: would a project manager track NEW and the candidate as one line item or two? Owner, status and deadline are hints only - a different owner makes "same" less likely, but ownership and deadlines legitimately change between meetings.

Return only JSON: {{"verdicts": [{{"id": <number>, "decision": "same" | "different" | "unsure", "candidate": "<letter, only when same>"}}]}}

### Items
{chr(10).join(blocks)}
"""
    try:
        response = complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=GATE_TEMPERATURE,
            model=GATE_MODEL,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response.choices[0].message.content).get("verdicts", [])
        by_id = {it["id"]: it for it in items}
        verdicts = {}
        for v in raw:
            vid, decision = int(v["id"]), v.get("decision")
            if vid not in by_id or decision not in ("same", "different", "unsure"):
                continue
            if decision == "same":
                letter = str(v.get("candidate", "")).strip().lower()
                ci = ord(letter) - 97 if len(letter) == 1 else -1
                if not 0 <= ci < len(by_id[vid]["candidates"]):
                    decision, ci = "unsure", None
                verdicts[vid] = (decision, ci)
            else:
                verdicts[vid] = (decision, None)
        return verdicts
    except Exception as e:
        logger.error(f"Match LLM confirmation failed: {e} - treating all semantic candidates as new tasks")
        return {}


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
    # Track which events still need semantic matching after the exact-match pass
    pending_semantic_indices = []

    # ------------------------ Pass 1: exact match only ------------------------
    # Partial/containment and fuzzy (SequenceMatcher) were removed - both could accept a
    # wrong match (containment had no length floor and a hardcoded 0.90 confidence never
    # actually measured; fuzzy's medium band (0.60-0.84) wasn't guaranteed to be the best
    # candidate available) and, critically, both would short-circuit before semantic ever
    # ran - so a wrong but "good enough" textual match could silently preempt a better
    # semantic one. Exact match doesn't have this problem: for identical normalized text,
    # cosine similarity between the two embeddings is 1.0, the maximum possible score -
    # semantic could never find a better candidate than an exact textual duplicate, so
    # this is a pure performance shortcut (skip an embedding call for text we already know
    # is identical), never a different outcome than letting semantic handle it too.
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
        exact_match = None
        for task in existing_tasks:
            norm_task_text = normalize_text((task.get("task_text") or "").strip())
            if norm_task_text and norm_task_text == norm_event_text:
                exact_match = task
                break

        if exact_match:
            _apply_match(event, exact_match, "exact", 1.0, "high")
            logger.info(
                f"Matched '{task_text}' -> task_id={event['matched_task_id']}"
                f" via {event['match_method']} event_type={event['event_type']}"
            )
        else:
            # Needs semantic - defer to Pass 2
            pending_semantic_indices.append(len(matched_events))

        matched_events.append(event)

    # ------------------------ Pass 2: batch semantic for all unmatched events ------------------------
    if pending_semantic_indices and tasks_with_embeddings:
        # Embed all pending texts in one model.encode() call
        pending_texts = [matched_events[i].get("task_text", "") for i in pending_semantic_indices]
        query_embeddings = get_embeddings_batch(pending_texts)
        gate_items = []  # (idx, [(score, task), ...]) for events that have at least one usable candidate

        for idx, query_emb in zip(pending_semantic_indices, query_embeddings):
            event = matched_events[idx]
            if query_emb is None:
                event["event_type"] = "new_task"
                continue

            scored = sorted(
                ((cosine_sim(query_emb, t["task_embedding"]), t) for t in tasks_with_embeddings),
                key=lambda pair: pair[0], reverse=True,
            )
            candidates = [(sc, t) for sc, t in scored[:TOP_K_CANDIDATES] if sc >= CANDIDATE_MIN_SCORE]
            if CONTAINMENT_CANDIDATES:
                offered = {t.get("task_id") for _, t in candidates}
                norm_event = normalize_text(event["task_text"])
                extra = [
                    (sc, t) for sc, t in scored
                    if t.get("task_id") not in offered and _contains(norm_event, normalize_text(t.get("task_text") or ""))
                ]
                candidates += extra[:MAX_CONTAINMENT_CANDIDATES]
            if not candidates:
                event["event_type"] = "new_task"
                logger.info(f"No usable semantic candidate for '{event['task_text']}' -> new_task")
                continue
            gate_items.append((idx, candidates))

        # Embedding similarity only proposes candidates; the LLM decides (see _llm_confirm_matches).
        verdicts = _llm_confirm_matches([
            {
                "id": i,
                "new": {"text": matched_events[idx]["task_text"], "owner": matched_events[idx].get("owner"),
                        "status": matched_events[idx].get("status"), "deadline": matched_events[idx].get("deadline")},
                "candidates": [
                    {"text": t.get("task_text"), "owner": t.get("owner"), "status": t.get("status"),
                     "deadline": t.get("deadline")} for _, t in cands
                ],
            }
            for i, (idx, cands) in enumerate(gate_items)
        ])
        for i, (idx, cands) in enumerate(gate_items):
            event = matched_events[idx]
            decision, ci = verdicts.get(i, (None, None))
            if decision == "same":
                score, task = cands[ci]
                _apply_match(event, task, "semantic", score, _confidence(score))
                logger.info(
                    f"Semantic matched '{event['task_text']}' -> task_id={event['matched_task_id']}"
                    f" score={score:.2f} event_type={event['event_type']}"
                )
                continue
            event["event_type"] = "new_task"
            if decision is None:
                continue  # no verdict obtained - not adjudicated, downstream dedup guard behaves as before
            # The gate ruled on this mention, so format's looser (0.85, no-LLM) semantic dedup
            # guard must not silently re-merge it with the candidate the gate just rejected.
            event["llm_adjudicated"] = True
            top_score, top_task = cands[0]
            if decision == "unsure" or top_score >= POSSIBLE_DUPLICATE_MIN_SCORE:
                # A person should look at these: the gate itself doubted, or it overruled a strong
                # embedding signal. The new task is still created (a visible duplicate beats a silent
                # wrong merge) and format.py stores which existing task it may duplicate.
                event["possible_duplicate_of"] = top_task.get("task_id")
                event["possible_duplicate_reason"] = (
                    "llm_unsure" if decision == "unsure" else "rejected_high_similarity"
                )
            if decision == "unsure":
                event["match_method"] = "llm_unsure"
                event["match_confidence"] = "low"
                logger.warning(
                    f"LLM unsure ('{event['task_text']}' vs '{cands[0][1].get('task_text')}' "
                    f"{cands[0][0]:.2f}) -> created as new task, flagged llm_unsure for review"
                )
            else:
                logger.info(f"LLM rejected semantic candidate(s) for '{event['task_text']}' -> new_task")

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
