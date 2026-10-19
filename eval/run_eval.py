"""Real-LLM evaluation harness - runs the actual pipeline (real model calls, real cost,
real time) against the existing golden dataset (datasets/simulation-ai-x-vmware/) and
reports a scored comparison, not a pass/fail assertion. Deliberately NOT part of the
pytest suite (tests/) - see DECISIONS.md's testing entry for why the two are kept apart.

Must run inside the api container: compare.py imports apps.utils.embeddings, which needs
sentence_transformers (only installed there), not just the standard library this file
itself uses. Talks to the already-running API over HTTP on its in-container port:

    docker compose exec api python3 eval/run_eval.py [--api-base http://localhost:8000]

Deletes and recreates a fixed eval project at the start of every run (not a random one),
so results are comparable run to run and nothing accumulates across runs. Its final state
is left in place afterward (not torn down) so it can be inspected in the UI if useful -
unlike tests/, an eval run's whole point is being something a person might want to look at.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from compare import compare_tasks, check_false_positives
from golden_parser import parse_sow, parse_meeting

DATASET_DIR = Path(__file__).parent.parent / "datasets" / "simulation-ai-x-vmware"
EVAL_PROJECT_ID = "eval-vmware-sim"


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


def _fetch_tasks(api_base, project_id):
    result = _request(api_base, "GET", f"/projects/{project_id}/tasks")
    return result["tasks"] if result else []


def _check_matching_algorithm_divergence(label, expected, actual, divergence_log):
    """Verification-only: computes both the old golden-file-order assignment and the
    new optimal-greedy assignment on the SAME real data, and records any case where they
    disagree - this is how we find out whether the matching-order bug actually changed
    any real result, instead of just arguing it's theoretically possible."""
    old = compare_tasks(expected, actual, algorithm="order")
    new = compare_tasks(expected, actual, algorithm="optimal")
    if old["matched_count"] != new["matched_count"]:
        divergence_log.append({
            "document": label,
            "old_matched_count": old["matched_count"],
            "new_matched_count": new["matched_count"],
        })
        return
    # Same total count doesn't guarantee same assignment - check per-task too.
    for old_r, new_r in zip(old["results"], new["results"]):
        if old_r["matched"] != new_r["matched"] or old_r["actual_text"] != new_r["actual_text"]:
            divergence_log.append({
                "document": label,
                "expected_text": old_r["expected_text"],
                "old_assignment": old_r["actual_text"],
                "new_assignment": new_r["actual_text"],
            })


def _print_task_result(label, report):
    pct = round(100 * report["matched_count"] / report["expected_count"]) if report["expected_count"] else 100
    marks = []
    for r in report["results"]:
        if not r["matched"]:
            marks.append(f"    MISS  {r['expected_text'][:70]}")
        else:
            flags = []
            if r["owner_ok"] is False:
                flags.append("owner mismatch")
            if r["priority_ok"] is False:
                flags.append("priority mismatch")
            flag_str = f"  ({', '.join(flags)})" if flags else ""
            symbol = "OK  " if not flags else "WARN"
            marks.append(f"    {symbol}  {r['expected_text'][:70]}{flag_str}")
    extra = f", {report['unmatched_extra_actual_count']} unmatched extra task(s) created" if report["unmatched_extra_actual_count"] else ""
    print(f"[{label}] {report['matched_count']}/{report['expected_count']} matched ({pct}%){extra}")
    for m in marks:
        print(m)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://localhost:8000")
    args = parser.parse_args()
    api_base = args.api_base

    print(f"Resetting eval project '{EVAL_PROJECT_ID}'...")
    _request(api_base, "DELETE", f"/projects/{EVAL_PROJECT_ID}")

    full_report = {"documents": []}
    algorithm_divergence = []

    # --- SOW ---
    sow_text = (DATASET_DIR / "sow.txt").read_text()
    golden_sow = parse_sow(str(DATASET_DIR / "expected-outputs" / "expected-output-sow.txt"))
    print(f"\nSubmitting sow.txt ({golden_sow.task_count} expected tasks)...")
    _submit_and_wait(api_base, sow_text, EVAL_PROJECT_ID)
    actual_tasks = _fetch_tasks(api_base, EVAL_PROJECT_ID)
    sow_report = compare_tasks(golden_sow.tasks, actual_tasks)
    _print_task_result("sow.txt", sow_report)
    full_report["documents"].append({"document": "sow.txt", **sow_report})
    _check_matching_algorithm_divergence("sow.txt", golden_sow.tasks, actual_tasks, algorithm_divergence)

    # --- Meetings 01-08, sequentially ---
    for n in range(1, 9):
        name = f"meeting-{n:02d}.txt"
        meeting_text = (DATASET_DIR / name).read_text()
        golden = parse_meeting(str(DATASET_DIR / "expected-outputs" / f"expected-output-{name}"))

        # Snapshot updated_at per task_id BEFORE this step, so a task that already
        # existed but was correctly matched-and-updated (not duplicated) by this step's
        # run can still be credited - diffing only task_id sets would miss it entirely,
        # since match.py's whole job is updating an existing task instead of creating a
        # new one when it recognizes continuity. That was the actual bug in the first
        # version of this harness: it silently scored correct matching behavior as MISS.
        before_snapshot = {t["task_id"]: t["updated_at"] for t in _fetch_tasks(api_base, EVAL_PROJECT_ID)}

        print(f"\nSubmitting {name} ({len(golden.new_tasks)} expected new tasks)...")
        _submit_and_wait(api_base, meeting_text, EVAL_PROJECT_ID)
        tasks_after = _fetch_tasks(api_base, EVAL_PROJECT_ID)

        newly_created = [t for t in tasks_after if t["task_id"] not in before_snapshot]
        touched = [
            t for t in tasks_after
            if t["task_id"] not in before_snapshot or t["updated_at"] != before_snapshot[t["task_id"]]
        ]

        # Recall is measured against everything touched (created OR updated) - a golden
        # "new task" mention is correctly satisfied either way. The false-positive check
        # stays scoped to genuinely new rows only - that check is specifically about
        # wrongly manufacturing a task, which an update to an existing one isn't.
        report = compare_tasks(golden.new_tasks, touched)
        _print_task_result(name, report)
        _check_matching_algorithm_divergence(name, golden.new_tasks, touched, algorithm_divergence)

        false_positives = check_false_positives(golden.should_not_be_picked, newly_created)
        if false_positives:
            print(f"  ⚠ {len(false_positives)} possible false positive(s) - created tasks resembling something the golden file says should NOT be picked:")
            for fp in false_positives:
                print(f"    - \"{fp['actual_task'][:70]}\" ~ \"{fp['matched_forbidden_text'][:70]}\" (score {fp['score']})")

        full_report["documents"].append({"document": name, "false_positives": false_positives, **report})

    total_expected = sum(d["expected_count"] for d in full_report["documents"])
    total_matched = sum(d["matched_count"] for d in full_report["documents"])
    overall_pct = round(100 * total_matched / total_expected) if total_expected else 100
    total_fps = sum(len(d.get("false_positives", [])) for d in full_report["documents"])

    print(f"\n{'=' * 60}")
    print(f"Overall: {total_matched}/{total_expected} tasks matched ({overall_pct}%), {total_fps} flagged false positive(s)")

    print(f"\nMatching-algorithm verification (golden-file-order vs. optimal-greedy):")
    if not algorithm_divergence:
        print("  No divergence on this run - the two algorithms produced identical assignments")
        print("  everywhere. The bug is real in principle but did not change this run's numbers.")
    else:
        print(f"  DIVERGED in {len(algorithm_divergence)} place(s) - the old algorithm's numbers")
        print("  from the previous run were wrong, not just theoretically flawed:")
        for d in algorithm_divergence:
            print(f"    [{d['document']}] {json.dumps(d)}")

    print(f"\nFull report: eval/report.json")
    print(f"Browse the run at: http://localhost:3010/products/task-manager?project={EVAL_PROJECT_ID}")

    full_report["algorithm_divergence"] = algorithm_divergence
    report_path = Path(__file__).parent / "report.json"
    report_path.write_text(json.dumps(full_report, indent=2, default=str))


if __name__ == "__main__":
    main()
