"""Node-level eval for the Hours timesheet import (apps/utils/hours_import.py): how often does the importer attach an entry to
the right task?

Answer key: datasets/hours/forloop/forloop-hours-expected.json (every label in the flat CSV and the weekly grids, mapped to the
task it really was). The people and tasks are the forloop ones. Real LLM calls (the batched check on labels the embeddings could
not settle); nothing is written to the database. Reported per label (35 distinct labels) and per hour (of 363 h), with the
embeddings-only result beside it, so the contribution of the model call is visible.

Limits, stated plainly: ONE synthetic project written by the same author as the answer key; three labels are genuinely
ambiguous ("Delivery management / leadership check-ins" could be read as the final-delivery task; the VPN follow-up could be
VPN setup or Review VPN access), so the key is a judgement and a person reviews every non-exact match in the import preview.

    docker compose exec api python3 eval/nodes/eval_hours_import.py
"""
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from apps.utils.hours_import import propose_task_matches

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "datasets" / "hours" / "forloop"


def main():
    exp = json.loads((DATA / "forloop-hours-expected.json").read_text())
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "generate_forloop_hours_dataset.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    tasks = [{"task_id": t["key"], "task_text": t["db_task_text"], "owner": t["owner"]} for t in exp["tasks"] if t["db_task_text"]]
    # (label, person) -> (true task key, hours). The plan-only project-management task has no stored task to match yet, so it is 'other'.
    truth = {}
    for person, _, key, hours, label in gen.HOURS:
        key = "other" if key == "pm" else key
        entry = truth.setdefault((label, person), [key, 0.0])
        entry[1] += hours
    pairs = list(truth)
    items = [{"label": l, "person": p} for l, p in pairs]
    total_hours = sum(h for _, h in truth.values())

    for use_llm in (False, True):
        out = propose_task_matches(items, tasks, use_llm=use_llm)
        right = right_h = 0.0
        wrong = []
        for (label, person), o in zip(pairs, out):
            key, hours = truth[(label, person)]
            got = o["task_id"] or "other"
            if got == key:
                right += 1
                right_h += hours
            else:
                wrong.append((label, person, o["match"], got, key, hours))
        print(f"\nembeddings{' + LLM check' if use_llm else ' only'}: {int(right)}/{len(pairs)} labels right, "
              f"{right_h:g} of {total_hours:g} hours on the right task ({100 * right_h / total_hours:.0f}%)")
        if use_llm:
            for label, person, how, got, key, hours in wrong:
                print(f"   wrong ({hours:g} h, {how}): '{label}' ({person}) -> {got}, should be {key}")


if __name__ == "__main__":
    main()
