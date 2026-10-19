"""Seeded, deterministic node-level eval for the match node - a controlled experiment for
choosing between matchers, complementing (not replacing) eval_match.py.

eval_match.py builds its DB state by running the real pipeline, so the starting task list
differs every run (extraction is nondeterministic) and each matcher builds a different state -
two runs can't be fairly compared. This script instead seeds the DB straight from the golden
expected outputs: before meeting N, the project holds the SOW's golden tasks plus the golden
"new tasks" of meetings 1..N-1. match never writes to the DB, so ONE seeded state per meeting
is scored by every matcher variant - only the matcher differs between columns of the report.

Scored per meeting: golden UPDATE EVENTS should match the RIGHT existing task (a match to any
other task is a wrong merge, counted as an error - matching "something" is not enough); golden
NEW TASKS should match nothing (specificity). The right task for an update event is its
verbatim twin in the seeded state (identical normalized text - golden update events usually
restate a task from an earlier meeting word for word), or, for paraphrased ones, the target
hand-labeled in match_targets.json. An update event with neither makes the run stop. Because
verbatim twins are matched by the exact stage alone, recall is reported separately for
"exact-twin" and "paraphrase" items - only the latter says anything about semantic matching or
the gate. Items that can't be scored fairly are listed and excluded from the "scored" numbers
(raw numbers over everything are printed too):
  - contradictory: same text appears as both an update event and a new task in one meeting
  - no_target: labeled in match_targets.json as having no suitable task in the state yet
  - verbatim_dup: a new task whose normalized text equals an already-seeded task (exact match
    to it is correct matcher behaviour; the golden label is noise)
  - ambiguous_label: a new task listed under ambiguous_new_tasks in match_targets.json - the
    golden data already holds the same deliverable under a near-identical name

Limits, by design: seeded state is PERFECT extraction with clean golden wording, so absolute
numbers look better than production, and compounding (a wrong merge changing later state) is
invisible here. Use this to pick between matchers; confirm the winner with averaged runs of the
end-to-end evals. No API or pipeline needed, only the DB; run inside the api container:

    docker compose exec api python3 eval/nodes/eval_match_seeded.py [--floors 0.60,0.55] [--models gpt-4o,gpt-4.1]

--floors adds one gate variant per candidate floor (CANDIDATE_MIN_SCORE); --models adds one per gate
model (GATE_MODEL), each also reported with how many gate items got no usable verdict.
"""
import argparse
import json
import sys
import time
from collections import Counter
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import apps.nodes.match as live_match
import old3stage_match
from apps.db.db import get_connection, insert_task
from eval.golden_parser import parse_sow, parse_meeting

DATASET_DIR = Path(__file__).parent.parent.parent / "datasets" / "simulation-ai-x-vmware"
EVAL_PROJECT_ID = "eval-match-seeded"
TARGETS_PATH = Path(__file__).parent / "match_targets.json"


def _reset_project(project_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM tasks WHERE project_id = %s", (project_id,))
    conn.commit()
    cur.close()
    conn.close()


def _seed(project_id, golden_tasks):
    for t in golden_tasks:
        row = {
            "task_text": t.text,
            "normalized_task_text": live_match.normalize_text(t.text),
            "owner": t.owner,
        }
        if getattr(t, "priority", None):
            row["priority"] = t.priority
        insert_task(row, project_id)


def _approve_all(items):
    # Gate off = always accept the closest candidate (index 0 = highest cosine).
    return {it["id"]: ("same", 0) for it in items}


@contextmanager
def _gate_off():
    # Pure exact+semantic baseline: no gate, and no containment candidates (which would otherwise
    # let "approve the first candidate" auto-accept a containment-only hit).
    with patch.object(live_match, "_llm_confirm_matches", _approve_all), \
         patch.object(live_match, "CONTAINMENT_CANDIDATES", False):
        yield


def _gate_with_floor(floor):
    @contextmanager
    def ctx():
        with patch.object(live_match, "CANDIDATE_MIN_SCORE", floor):
            yield
    return ctx


@contextmanager
def _containment_off():
    with patch.object(live_match, "CONTAINMENT_CANDIDATES", False):
        yield


REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")  # reject temperature=0
GATE_STATS = {}  # variant label -> Counter(calls, items, no_verdict) and seconds


def _gate_model(model, label):
    """Patch the gate's model, and count gate items that came back with no verdict (API error,
    unsupported parameter, malformed JSON...) - without this a broken model would silently look
    like a model that rejects everything."""
    real = live_match._llm_confirm_matches

    def counting(items):
        out = real(items)
        c = GATE_STATS.setdefault(label, Counter())
        c["calls"] += 1
        c["items"] += len(items)
        c["no_verdict"] += sum(1 for it in items if it["id"] not in out)
        return out

    @contextmanager
    def ctx():
        temperature = None if model.startswith(REASONING_PREFIXES) else 0
        with patch.object(live_match, "GATE_MODEL", model), \
             patch.object(live_match, "GATE_TEMPERATURE", temperature), \
             patch.object(live_match, "_llm_confirm_matches", counting):
            yield
    return ctx


def _matchers(floors=(), models=()):
    m = {}
    if hasattr(live_match, "_llm_confirm_matches"):
        m["exact+semantic (gate off)"] = (live_match.task_match_node, _gate_off)
        m["gate, no containment candidates"] = (live_match.task_match_node, _containment_off)
        m["live: gate + containment candidates"] = (live_match.task_match_node, nullcontext)
    else:
        m["exact+semantic (live, no gate present)"] = (live_match.task_match_node, nullcontext)
    for model in models:
        label = f"gate model {model}"
        m[label] = (live_match.task_match_node, _gate_model(model, label))
    for f in floors:
        m[f"gate, candidate floor {f}"] = (live_match.task_match_node, _gate_with_floor(f))
    m["old 3-stage (reconstruction)"] = (old3stage_match.task_match_node, nullcontext)
    return m


def _run(fn, ctx, project_id, texts, owners, label=None):
    if not texts:
        return []
    started = time.time()
    with ctx():
        events = [{"task_text": t, "owner": owners.get(t)} for t in texts]
        out = fn({"normalized_events": events, "project_id": project_id})["matched_events"]
    if label in GATE_STATS:
        GATE_STATS[label]["ms"] += int((time.time() - started) * 1000)
    return out


def _load_labels():
    data = json.loads(TARGETS_PATH.read_text())
    return data["labels"], {k for k in data.get("ambiguous_new_tasks", {}) if not k.startswith("_")}


def _expected_targets(n, text, seeded_norm, labels, norm):
    """Normalized texts that count as the correct target, [] if none exists yet, or None if
    unlabeled (caller must stop - never guess a ground truth)."""
    entry = labels.get(f"{n}|{text}")
    if entry is not None:
        return [norm(t) for t in entry["targets"] + entry.get("also_accept", [])]
    if norm(text) in seeded_norm:
        return [norm(text)]
    return None


def _pct(a, b):
    return f"{a}/{b} ({round(100 * a / b)}%)" if b else "n/a"


def main():
    norm = live_match.normalize_text
    labels, ambiguous_new = _load_labels()
    unlabeled = []
    ap = argparse.ArgumentParser()
    ap.add_argument("--floors", default="", help="comma-separated candidate-floor variants, e.g. 0.60,0.55")
    ap.add_argument("--models", default="", help="comma-separated gate-model variants, e.g. gpt-4o,gpt-4.1")
    args = ap.parse_args()
    floors = [float(x) for x in args.floors.split(",") if x]
    models = [x for x in args.models.split(",") if x]
    matchers = _matchers(floors, models)
    items = []  # one dict per (meeting, group, text) with exclusion reason + per-matcher outcome

    _reset_project(EVAL_PROJECT_ID)
    sow = parse_sow(str(DATASET_DIR / "expected-outputs" / "expected-output-sow.txt"))
    seeded = list(sow.tasks)
    _seed(EVAL_PROJECT_ID, seeded)

    for n in range(1, 9):
        name = f"meeting-{n:02d}.txt"
        golden = parse_meeting(str(DATASET_DIR / "expected-outputs" / f"expected-output-{name}"))
        should_match = [e.text for e in golden.update_events]
        should_not = [t.text for t in golden.new_tasks]
        owners = {e.text: e.owner for e in golden.update_events}
        owners.update({t.text: t.owner for t in golden.new_tasks})

        seeded_norm = {norm(t.text) for t in seeded}
        contradictory = {norm(x) for x in should_match} & {norm(x) for x in should_not}

        print(f"[{name}] state = {len(seeded)} seeded tasks; {len(should_match)} update events, {len(should_not)} new tasks")

        for group, texts in (("should_match", should_match), ("should_not_match", should_not)):
            for text in texts:
                reason, targets, kind = None, None, None
                if group == "should_match":
                    targets = _expected_targets(n, text, seeded_norm, labels, norm)
                    if targets is None:
                        unlabeled.append(f"m{n}: {text}")
                    kind = "exact_twin" if f"{n}|{text}" not in labels else "paraphrase"
                if norm(text) in contradictory:
                    reason = "contradictory"
                elif group == "should_match" and targets == []:
                    reason = "no_target"
                elif group == "should_not_match" and norm(text) in seeded_norm:
                    reason = "verbatim_dup"
                elif group == "should_not_match" and f"{n}|{text}" in ambiguous_new:
                    reason = "ambiguous_label"
                items.append({"meeting": n, "group": group, "text": text, "excluded": reason,
                              "kind": kind, "targets": targets, "results": {}})

        for label, (fn, ctx) in matchers.items():
            for group, texts in (("should_match", should_match), ("should_not_match", should_not)):
                events = _run(fn, ctx, EVAL_PROJECT_ID, texts, owners, label)
                group_items = [i for i in items if i["meeting"] == n and i["group"] == group]
                for item, ev in zip(group_items, events):
                    item["results"][label] = {
                        "matched": bool(ev.get("is_matched")),
                        "method": ev.get("match_method"),
                        "confidence": ev.get("match_confidence"),
                        "matched_to": ev.get("matched_task_text"),
                    }

        _seed(EVAL_PROJECT_ID, golden.new_tasks)
        seeded.extend(golden.new_tasks)

    _reset_project(EVAL_PROJECT_ID)

    if unlabeled:
        raise SystemExit("Unlabeled update events (no verbatim twin and no entry in match_targets.json):\n  "
                         + "\n  ".join(unlabeled))

    excluded = [i for i in items if i["excluded"]]
    print(f"\n{'=' * 70}\nExcluded from the 'scored' numbers ({len(excluded)} of {len(items)} items):")
    for i in excluded:
        print(f"  m{i['meeting']} {i['excluded']:13s} [{i['group']}] {i['text'][:70]}")

    def outcome(item, label):
        """should_match -> correct / wrong_target / missed; should_not_match -> correct / false_positive."""
        r = item["results"][label]
        if item["group"] == "should_not_match":
            return "false_positive" if r["matched"] else "correct"
        if not r["matched"]:
            return "missed"
        return "correct" if norm(r["matched_to"] or "") in item["targets"] else "wrong_target"

    summary = {}
    print(f"\n{'=' * 70}")
    print("Update events (should match the RIGHT task) - scored items only; 'wrong target' = matched, but to another task")
    print(f"{'matcher':38s} {'exact-twin':>12s} {'paraphrase':>12s} {'wrong target':>13s} {'missed':>8s}   | {'specificity':>14s}")
    for label in matchers:
        def count(group, kind=None, what="correct"):
            rows = [i for i in items if i["group"] == group and not i["excluded"] and (kind is None or i["kind"] == kind)]
            return sum(1 for r in rows if outcome(r, label) == what), len(rows)
        twin, para = count("should_match", "exact_twin"), count("should_match", "paraphrase")
        wrong = count("should_match", None, "wrong_target")[0]
        missed = count("should_match", None, "missed")[0]
        spec = count("should_not_match")
        errors = (twin[1] - twin[0]) + (para[1] - para[0]) + (spec[1] - spec[0])
        summary[label] = {"exact_twin": twin, "paraphrase": para, "wrong_target": wrong, "missed": missed,
                          "specificity": spec, "total_errors": errors}
        print(f"{label:38s} {_pct(*twin):>12s} {_pct(*para):>12s} {wrong:>13d} {missed:>8d}   | {_pct(*spec):>14s}   total errors: {errors}")

    if GATE_STATS:
        print("\nGate model health (no_verdict = items the model returned nothing usable for; "
              "a high number means the model failed, not that it judged badly):")
        for label, c in GATE_STATS.items():
            print(f"  {label:32s} calls {c['calls']:3d}  items {c['items']:3d}  no_verdict {c['no_verdict']:3d}  "
                  f"gate+embedding time {c['ms'] / 1000:.0f}s")

    print("\nllm_unsure verdicts (flagged for review; created as new tasks) per matcher:")
    for label in matchers:
        u = [i for i in items if i["results"][label]["method"] == "llm_unsure"]
        print(f"  {label:38s} on should-match: {sum(1 for i in u if i['group']=='should_match')}"
              f"  on should-NOT-match: {sum(1 for i in u if i['group']=='should_not_match')}")

    print(f"\nScored errors per matcher:")
    for label in matchers:
        print(f"\n  {label}")
        for i in items:
            if i["excluded"]:
                continue
            r = i["results"][label]
            o = outcome(i, label)
            if o == "missed":
                print(f"    MISS   m{i['meeting']} [{i['kind']}] {i['text'][:75]}")
            elif o == "wrong_target":
                print(f"    WRONG  m{i['meeting']} [{r['method']}/{r['confidence']}] {i['text'][:50]} -> {(r['matched_to'] or '')[:55]}")
            elif o == "false_positive":
                print(f"    FP     m{i['meeting']} [{r['method']}/{r['confidence']}] {i['text'][:50]} -> {(r['matched_to'] or '')[:55]}")

    report_path = Path(__file__).parent / "report_match_seeded.json"
    report_path.write_text(json.dumps({"summary": summary, "items": items}, indent=2))
    print(f"\nFull report: {report_path}")


if __name__ == "__main__":
    main()
