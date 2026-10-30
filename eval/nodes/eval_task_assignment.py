"""Node-level eval for "which deliverable does this task belong to" (apps/utils/deliverable_ai.propose_assignments).

Answer key: the golden SOW's hierarchy - 11 top-level items (the SOW's deliverables) each with a Subtasks list, 72 subtasks
in total (golden_parser.parse_sow_hierarchy). The project is SEEDED directly (no pipeline): the 11 deliverables come from
the SOW's numbered section, the tasks are the golden subtasks plus the 11 top-level items restated as tasks, and the real
LLM then proposes an assignment for every task. Scored: coverage (how many got a deliverable at all), accuracy among those
that did, and overall accuracy; errors are listed.

Limits: the golden subtasks are clean, human-written lines (the pipeline's own extracted tasks are messier and at mixed
granularity), and the SOW's explicit numbering is not used here (no task cites a number), so this measures the semantic
path only. Run inside the api container:

    docker compose exec api python3 eval/nodes/eval_task_assignment.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from apps.db.db import delete_project, insert_task, list_deliverables, upsert_document_deliverables
from apps.nodes.match import cosine_sim
from apps.utils.deliverable_ai import propose_assignments
from apps.utils.deliverables import parse_deliverables_section
from apps.utils.embeddings import get_embeddings_batch
from eval.golden_parser import parse_sow_hierarchy

DATASET = Path(__file__).parent.parent.parent / "datasets" / "simulation-ai-x-vmware"
PROJECT = "eval-task-assignment"


def main():
    delete_project(PROJECT)
    upsert_document_deliverables(PROJECT, parse_deliverables_section((DATASET / "sow.txt").read_text()))
    deliverables = list_deliverables(PROJECT, states=("confirmed",))
    golden = parse_sow_hierarchy(str(DATASET / "expected-outputs" / "expected-output-sow.txt"))

    # golden top-level item -> deliverable, by title similarity (must be one-to-one)
    g_emb = get_embeddings_batch([g.title for g in golden])
    d_emb = get_embeddings_batch([d["title"] for d in deliverables])
    mapping, used = {}, set()
    for gi, g in enumerate(golden):
        score, di = max((cosine_sim(g_emb[gi], d_emb[di]), di) for di in range(len(deliverables)))
        mapping[gi] = di
        used.add(di)
        flag = "" if score >= 0.6 else "   <-- LOW, check"
        print(f"  golden '{g.title[:44]}' -> {deliverables[di]['number']} {deliverables[di]['title'][:40]} ({score:.2f}){flag}")
    assert len(used) == len(golden), "golden items do not map one-to-one onto the deliverables"

    expected = {}  # task_id -> (deliverable index, kind)
    for gi, g in enumerate(golden):
        for sub in g.subtasks:
            tid = insert_task({"task_text": sub, "normalized_task_text": sub.lower()}, PROJECT)
            expected[str(tid)] = (mapping[gi], "subtask")
        tid = insert_task({"task_text": g.title, "normalized_task_text": g.title.lower()}, PROJECT)
        expected[str(tid)] = (mapping[gi], "restatement")

    from apps.db.db import fetch_unassigned_tasks
    proposals = propose_assignments(deliverables, fetch_unassigned_tasks(PROJECT))
    delete_project(PROJECT)

    rows = []
    for p in proposals:
        exp_idx, kind = expected[p["task_id"]]
        got = next((i for i, d in enumerate(deliverables) if str(d["deliverable_id"]) == p["deliverable_id"]), None)
        rows.append({"kind": kind, "text": p["task_text"], "expected": exp_idx, "got": got, "confidence": p["confidence"]})

    def report(label, subset):
        assigned = [r for r in subset if r["got"] is not None]
        correct = [r for r in assigned if r["got"] == r["expected"]]
        pct = lambda a, b: f"{a}/{b} ({round(100 * a / b)}%)" if b else "n/a"
        print(f"  {label:34s} assigned {pct(len(assigned), len(subset)):>11s} | correct among assigned {pct(len(correct), len(assigned)):>11s} | overall correct {pct(len(correct), len(subset)):>11s}")

    print(f"\n{'=' * 100}\nTask -> deliverable assignment ({len(rows)} tasks, {len(deliverables)} deliverables)")
    report("subtasks (72)", [r for r in rows if r["kind"] == "subtask"])
    report("restated deliverable titles (11)", [r for r in rows if r["kind"] == "restatement"])
    report("all", rows)
    print("  by confidence:", dict(Counter((r["confidence"], r["got"] == r["expected"]) for r in rows if r["got"] is not None)))

    wrong = [r for r in rows if r["got"] is not None and r["got"] != r["expected"]]
    missed = [r for r in rows if r["got"] is None]
    print(f"\nWRONG assignment ({len(wrong)}):")
    for r in wrong:
        print(f"  [{r['confidence']}] {r['text'][:62]:62s} expected {deliverables[r['expected']]['number']}, got {deliverables[r['got']]['number']}")
    print(f"\nLEFT UNASSIGNED ({len(missed)}):")
    for r in missed:
        print(f"  {r['text'][:62]:62s} (belongs to {deliverables[r['expected']]['number']})")


if __name__ == "__main__":
    main()
