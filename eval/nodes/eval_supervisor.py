"""Node-level eval for apps.supervisor's document_type classification - tests the
deterministic count-based branches directly against real DB state:
  - existing_task_count == 0                      -> "sow"
  - existing_task_count > 0 and no SOW markers     -> "mom"

Ground truth is the dataset's own file split: sow.txt is the project's first document
(should classify "sow"), every meeting-NN.txt comes after it (should classify "mom"). No
new golden data was authored - the filenames already are the label.

Does NOT exercise the LLM tie-break branch (_llm_tiebreak, existing_task_count > 0 AND SOW
markers present): confirmed via grep that none of meeting-01..08.txt contain SOW markers
("statement of work" / "scope of work" / "deliverables:" / "this agreement") - only sow.txt
does, and it only ever reaches the task-count==0 branch. That branch is real code but
genuinely untested by this dataset - flagged here rather than silently claiming full
coverage.

Needs a working DB_HOST (real Postgres) - supervisor_node makes no LLM call on the paths
this eval exercises, so run it the same way as the other node evals for consistency:

    docker compose exec api python3 eval/nodes/eval_supervisor.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from apps.supervisor import supervisor_node
from apps.db.db import insert_task, get_connection

DATASET_DIR = Path(__file__).parent.parent.parent / "datasets" / "simulation-ai-x-vmware"
EVAL_PROJECT_ID = "eval-supervisor-node"


def _reset_project(project_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM tasks WHERE project_id = %s", (project_id,))
    conn.commit()
    cur.close()
    conn.close()


def _classify(text, project_id):
    return supervisor_node({"raw_input": text, "project_id": project_id})["document_type"]


def main():
    _reset_project(EVAL_PROJECT_ID)
    results = []

    sow_text = (DATASET_DIR / "sow.txt").read_text()
    predicted = _classify(sow_text, EVAL_PROJECT_ID)
    results.append({"case": "sow.txt (empty project)", "expected": "sow", "actual": predicted})

    # Seed one existing task so every call after this hits existing_task_count > 0 -
    # insert_task() is the real production path (generates a real embedding), not a
    # hand-written INSERT, so the seeded row looks exactly like a real task would.
    insert_task({
        "task_text": "Seed task for supervisor eval",
        "normalized_task_text": "seed task for supervisor eval",
        "priority": "medium",
    }, EVAL_PROJECT_ID)

    for n in range(1, 9):
        name = f"meeting-{n:02d}.txt"
        meeting_text = (DATASET_DIR / name).read_text()
        predicted = _classify(meeting_text, EVAL_PROJECT_ID)
        results.append({"case": f"{name} (project has tasks)", "expected": "mom", "actual": predicted})

    correct = sum(1 for r in results if r["expected"] == r["actual"])
    print(f"Supervisor classification: {correct}/{len(results)} correct\n")
    for r in results:
        mark = "OK  " if r["expected"] == r["actual"] else "FAIL"
        print(f"  {mark}  {r['case']}: expected={r['expected']} actual={r['actual']}")

    print("\nNote: the LLM tie-break branch (_llm_tiebreak) is not exercised by this dataset -")
    print("no meeting file contains SOW markers alongside an existing task.")

    _reset_project(EVAL_PROJECT_ID)

    report_path = Path(__file__).parent / "report_supervisor.json"
    report_path.write_text(json.dumps({"correct": correct, "total": len(results), "results": results}, indent=2))
    print(f"\nFull report: {report_path}")


if __name__ == "__main__":
    main()
