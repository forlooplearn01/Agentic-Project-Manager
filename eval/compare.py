"""Fuzzy comparison between golden expected tasks and what the real pipeline actually
produced. Uses the same embedding model apps/utils/embeddings.py already uses for real
task-dedup matching in production (all-MiniLM-L6-v2, via pgvector elsewhere, plain cosine
similarity here) - not difflib.SequenceMatcher, which was the original choice and turned
out to measure character overlap, not meaning: it credited "Deliver Integration Testing
Report" against "Have draft of integration test plan ready for review" (0.58, above the
old 0.5 threshold) purely because both contain "integration test," despite being
different tasks. Embeddings capture semantic similarity instead, which is what "is this
the same task" actually needs.

Must run inside the api container (the only place sentence-transformers is installed) -
see run_eval.py's docstring."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # so `apps.*` resolves regardless of cwd

from apps.utils.embeddings import get_embeddings_batch

# Provisional, not blindly carried over from the old character-based scale (cosine
# similarity has a different numeric distribution entirely) - calibrated against a known
# bad match (0.595, two different tasks sharing vocabulary) and known good matches (0.615+,
# genuine paraphrases) - see DECISIONS.md for the actual evidence behind this number. Set
# just above the bad match and just below the weakest confirmed good one.
MATCH_THRESHOLD = 0.6


def _cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _similarity_matrix(texts_a: list, texts_b: list) -> list:
    """Batch-embeds both lists ONCE (not per-pair - get_embeddings_batch does one model
    forward pass for all texts, versus calling get_embedding per comparison, which would
    mean hundreds of redundant model calls for a dozen golden tasks x dozens of actual
    ones). Returns a 2D list, matrix[i][j] = similarity(texts_a[i], texts_b[j])."""
    if not texts_a or not texts_b:
        return [[0.0] * len(texts_b) for _ in texts_a]
    embeddings_a = get_embeddings_batch(texts_a)
    embeddings_b = get_embeddings_batch(texts_b)
    return [[_cosine(ea, eb) for eb in embeddings_b] for ea in embeddings_a]


def _assign_by_golden_order(matrix: list) -> dict:
    """The original algorithm: processes golden tasks one at a time, in file order, each
    one claiming its own best-available match. Kept only for the side-by-side
    verification run that checks whether this ever actually produced a different result
    than the optimal assignment below - not used by default once that's confirmed."""
    assignment = {}
    claimed = set()
    for i, row in enumerate(matrix):
        best_j, best_score = None, -1.0
        for j, score in enumerate(row):
            if j not in claimed and score > best_score:
                best_j, best_score = j, score
        if best_j is not None and best_score >= MATCH_THRESHOLD:
            claimed.add(best_j)
            assignment[i] = (best_j, best_score)
        else:
            assignment[i] = None
    return assignment


def _assign_optimal_greedy(matrix: list) -> dict:
    """Scores every (golden, actual) pair up front, then assigns highest-scoring pairs
    first across the whole matrix - not per golden task in isolation. Fixes a real,
    confirmed bug in the order-based version (see DECISIONS.md): a golden task earlier in
    the file could claim an actual task that scores higher for a LATER golden task,
    leaving that later one unmatched even though a better global pairing existed. A
    greedy approximation of optimal bipartite matching, not the full Hungarian algorithm -
    sufficient at this scale (dozens of tasks, not thousands)."""
    pairs = []
    for i, row in enumerate(matrix):
        for j, score in enumerate(row):
            if score >= MATCH_THRESHOLD:
                pairs.append((score, i, j))
    pairs.sort(reverse=True)

    assignment = {i: None for i in range(len(matrix))}
    claimed = set()
    for score, i, j in pairs:
        if assignment[i] is not None or j in claimed:
            continue
        assignment[i] = (j, score)
        claimed.add(j)
    return assignment


def compare_tasks(expected: list, actual: list, algorithm: str = "optimal") -> dict:
    """expected: list of GoldenTask. actual: list of task dicts from the real API
    (task_text, owner, deadline, priority). Returns a per-task match report plus totals -
    a scored comparison, not a pass/fail assertion, since there's no single correct
    wording for the model to produce. algorithm="order" is only for the verification run
    that checks this against "optimal" on the same real data - never use it otherwise."""
    if not expected:
        return {"expected_count": 0, "matched_count": 0, "unmatched_extra_actual_count": len(actual), "results": []}

    matrix = _similarity_matrix([e.text for e in expected], [a.get("task_text", "") for a in actual])
    assign_fn = _assign_by_golden_order if algorithm == "order" else _assign_optimal_greedy
    assignment = assign_fn(matrix)
    claimed_j = {j for j in assignment.values() if j is not None}

    results = []
    for i, exp in enumerate(expected):
        pair = assignment[i]
        if pair is not None:
            j, score = pair
            matched_task = actual[j]
        else:
            matched_task, score = None, 0.0
        matched = matched_task is not None

        owner_ok = priority_ok = None
        if matched:
            if exp.owner:
                owner_ok = exp.owner.lower() in (matched_task.get("owner") or "").lower() or \
                           (matched_task.get("owner") or "").lower() in exp.owner.lower()
            if exp.priority:
                priority_ok = exp.priority == (matched_task.get("priority") or "").lower()

        results.append({
            "expected_text": exp.text,
            "matched": matched,
            "match_score": round(score, 2),
            "actual_text": matched_task.get("task_text") if matched_task else None,
            "owner_ok": owner_ok,
            "priority_ok": priority_ok,
        })

    unmatched_extra_count = len(actual) - len(claimed_j)

    return {
        "expected_count": len(expected),
        "matched_count": sum(1 for r in results if r["matched"]),
        "unmatched_extra_actual_count": unmatched_extra_count,
        "results": results,
    }


def check_false_positives(should_not_be_picked: list, actual_new_tasks: list) -> list:
    """Best-effort check: does any actually-created task look suspiciously similar to
    something the golden file explicitly says should NOT become a task? Flagged for
    human review, not a hard failure - this is inherently fuzzy prose matching."""
    if not should_not_be_picked or not actual_new_tasks:
        return []

    actual_texts = [t.get("task_text", "") for t in actual_new_tasks]
    matrix = _similarity_matrix(actual_texts, should_not_be_picked)

    flags = []
    for i, actual_task in enumerate(actual_new_tasks):
        for j, forbidden in enumerate(should_not_be_picked):
            score = matrix[i][j]
            if score >= 0.6:
                flags.append({
                    "actual_task": actual_task.get("task_text", ""),
                    "matched_forbidden_text": forbidden,
                    "score": round(score, 2),
                })
    return flags
