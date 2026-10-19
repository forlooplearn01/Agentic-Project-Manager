"""Node-level eval for apps.nodes.match - tests match.py's own matching decisions in
isolation, separate from run_eval.py (which only checks the pipeline's FINAL task list,
conflating extraction/normalization/match/planning quality into one number).

Ground truth reuses the existing golden dataset's UPDATE EVENTS section (text that refers
to an already-existing task and should be recognized as a continuation - match_node should
find it) alongside its NEW TASKS section (text that should NOT match anything existing).
Both are already present in every expected-output file; see golden_parser.py's
GoldenMeeting.update_events. No new golden data was authored for this eval.

Calls task_match_node() directly against real DB state + real embeddings (not mocked), so
a wrong result here is unambiguously match.py's own fault.

Limits: the DB state is built by the real, nondeterministic pipeline, and "recall" here counts a
match to ANY existing task as a hit (the pipeline's task wording differs from the golden wording,
so the correct target can't be checked automatically) - it is an upper bound that rewards wrong
merges. For comparing matchers use eval_match_seeded.py (fixed state, target-correct scoring);
use this one as the realism check, reading matched_pairs in the report.

Must run inside the api container (apps.nodes.match needs sentence-transformers and a
working DB_HOST) and reach the running API for the document-submission steps that build up
realistic existing-task state:

    docker compose exec api python3 eval/nodes/eval_match.py [--api-base http://localhost:8000]

Uses its own dedicated project (eval-match-node), separate from eval-vmware-sim
(run_eval.py's project), so the two never collide or depend on each other's run order."""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from apps.nodes.match import task_match_node
from apps.db.db import fetch_active_tasks
from eval.golden_parser import parse_meeting

DATASET_DIR = Path(__file__).parent.parent.parent / "datasets" / "simulation-ai-x-vmware"
EVAL_PROJECT_ID = "eval-match-node"


def _request(api_base, method, path, body=None):
    url = f"{api_base}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _submit_and_wait(api_base, text, project_id, timeout_s=180):
    result = _request(api_base, "POST", "/run", {"input_text": text, "project_id": project_id, "force_run": True})
    job_id = result["job_id"]
    start = time.time()
    while time.time() - start < timeout_s:
        job = _request(api_base, "GET", f"/jobs/{job_id}")
        if job["status"] != "running":
            return job
        time.sleep(3)
    raise TimeoutError(f"Job {job_id} did not complete within {timeout_s}s")


def _run_match(project_id, texts):
    """Calls task_match_node directly (not via the graph) against whatever tasks
    currently exist for project_id. Returns matched_events in the same order as texts."""
    events = [{"task_text": t} for t in texts]
    state = {"normalized_events": events, "project_id": project_id}
    return task_match_node(state)["matched_events"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://localhost:8000")
    args = parser.parse_args()
    api_base = args.api_base

    print(f"Resetting project '{EVAL_PROJECT_ID}'...")
    _request(api_base, "DELETE", f"/projects/{EVAL_PROJECT_ID}")

    print("Seeding existing tasks from sow.txt...")
    sow_text = (DATASET_DIR / "sow.txt").read_text()
    _submit_and_wait(api_base, sow_text, EVAL_PROJECT_ID)

    documents = []
    totals = {"should_match_correct": 0, "should_match_total": 0,
              "should_not_match_correct": 0, "should_not_match_total": 0}

    for n in range(1, 9):
        name = f"meeting-{n:02d}.txt"
        golden = parse_meeting(str(DATASET_DIR / "expected-outputs" / f"expected-output-{name}"))

        existing_before = fetch_active_tasks(EVAL_PROJECT_ID)
        print(f"\n[{name}] {len(existing_before)} existing task(s) in DB before this step")

        should_not_match_texts = [t.text for t in golden.new_tasks]
        should_match_texts = [e.text for e in golden.update_events]

        doc_result = {"document": name, "false_negatives": [], "false_positives": [], "matched_pairs": []}

        if should_match_texts:
            for text, event in zip(should_match_texts, _run_match(EVAL_PROJECT_ID, should_match_texts)):
                totals["should_match_total"] += 1
                if event.get("is_matched"):
                    totals["should_match_correct"] += 1
                    doc_result["matched_pairs"].append({"text": text, "matched_to": event.get("matched_task_text")})
                else:
                    doc_result["false_negatives"].append(text)

        if should_not_match_texts:
            for text, event in zip(should_not_match_texts, _run_match(EVAL_PROJECT_ID, should_not_match_texts)):
                totals["should_not_match_total"] += 1
                if not event.get("is_matched"):
                    totals["should_not_match_correct"] += 1
                else:
                    doc_result["false_positives"].append({
                        "text": text,
                        "wrongly_matched_to": event.get("matched_task_text"),
                        "method": event.get("match_method"),
                        "confidence": event.get("match_confidence"),
                    })

        fn, fp = len(doc_result["false_negatives"]), len(doc_result["false_positives"])
        if should_match_texts:
            print(f"  should-match: {len(should_match_texts) - fn}/{len(should_match_texts)} correctly matched")
        else:
            print("  should-match: (none)")
        if should_not_match_texts:
            print(f"  should-NOT-match: {len(should_not_match_texts) - fp}/{len(should_not_match_texts)} correctly left unmatched")
        else:
            print("  should-NOT-match: (none)")
        if fn:
            print(f"    MISSED (should have matched, didn't): {doc_result['false_negatives']}")
        if fp:
            print(f"    FALSE POSITIVE (wrongly matched): {[f['text'] for f in doc_result['false_positives']]}")

        documents.append(doc_result)

        print(f"  Advancing DB state: submitting {name}...")
        meeting_text = (DATASET_DIR / name).read_text()
        _submit_and_wait(api_base, meeting_text, EVAL_PROJECT_ID)

    print(f"\n{'=' * 60}")
    recall = round(100 * totals["should_match_correct"] / totals["should_match_total"]) if totals["should_match_total"] else 100
    specificity = round(100 * totals["should_not_match_correct"] / totals["should_not_match_total"]) if totals["should_not_match_total"] else 100
    print(f"Should-match recall, ANY-target upper bound: {totals['should_match_correct']}/{totals['should_match_total']} ({recall}%)")
    print("  (counts a match to ANY existing task as a hit, even a wrong one - the pairs are in the report under")
    print("   matched_pairs for review; use eval_match_seeded.py for target-correct scoring)")
    print(f"Should-NOT-match specificity: {totals['should_not_match_correct']}/{totals['should_not_match_total']} ({specificity}%)")

    report_path = Path(__file__).parent / "report_match.json"
    report_path.write_text(json.dumps({"totals": totals, "documents": documents}, indent=2))
    print(f"\nFull report: {report_path}")


if __name__ == "__main__":
    main()
