"""Node-level eval for AI "suggest deliverables" (apps/utils/deliverable_ai.suggest_deliverables_from_text).

Measured part: the synthetic SOW with its numbered "4. DELIVERABLES" section REMOVED, so the assistant must work out the
deliverables from the scope text; the 11 items of the removed section are the answer key (matched by title similarity with
eval/compare.py's optimal one-to-one matching; its 0.6 cutoff sits above the 0.56 that distinct deliverable titles ever score
against each other). Run under three definitions of "deliverable" to show how much the definition matters - it is the
open product question (DECISIONS.md).

Limits, stated plainly: the SOW's other sections (3.1 In-Scope, the timeline) describe the same modules, so the removed
section is partly recoverable from them; and this is ONE synthetic document. The second part just PRINTS what the
default definition suggests for the real-style forloop SOW (no answer key exists) so a person can judge it. Real LLM calls,
nothing is written to the database.

    docker compose exec api python3 eval/nodes/eval_deliverable_suggest.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from apps.utils.deliverable_ai import DELIVERABLE_DEFINITION, suggest_deliverables_from_text
from apps.utils.deliverables import parse_deliverables_section
from eval.compare import compare_tasks
from eval.golden_parser import GoldenTask

DATASETS = Path(__file__).parent.parent.parent / "datasets"

# name -> (definition, exhaustive). The first two rows isolate the effect of the "be exhaustive" instruction.
VARIANTS = {
    "default definition, NOT told to be exhaustive": (DELIVERABLE_DEFINITION, False),
    "default definition (tangible outputs the client signs off)": (DELIVERABLE_DEFINITION, True),
    "strict: only documents, working features and the launched system": (
        "A deliverable is ONLY a tangible output the client receives or signs off on: a document or report, a working "
        "feature or module, or the deployed system. Exclude every internal activity and every sub-step.", True),
    "broad: anything a project manager would track as a deliverable": (
        "A deliverable is anything a project manager would track and report on as a distinct piece of work with an owner.", True),
}


def strip_section_4(text: str) -> str:
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if re.match(r"^\s*4\.\s+DELIVERABLES\s*$", l))
    end = next(i for i in range(start + 1, len(lines)) if re.match(r"^\s*5\.\s+\S", lines[i]))
    return "\n".join(lines[:start] + lines[end:])


def main():
    sow = (DATASETS / "simulation-ai-x-vmware" / "sow.txt").read_text()
    golden = [GoldenTask(text=d["title"]) for d in parse_deliverables_section(sow)]
    stripped = strip_section_4(sow)
    assert parse_deliverables_section(stripped) == [] and len(golden) == 11, "section 4 was not removed cleanly"

    print(f"Synthetic SOW with section 4 removed ({len(stripped)} chars); answer key = the 11 removed deliverables\n")
    for name, (definition, exhaustive) in VARIANTS.items():
        suggestions = suggest_deliverables_from_text(stripped, definition, exhaustive)
        report = compare_tasks(golden, [{"task_text": s["title"]} for s in suggestions])
        matched, n = report["matched_count"], len(suggestions)
        print(f"[{name}]")
        print(f"  {n} suggested | recall {matched}/11 ({round(100 * matched / 11)}%) | precision {matched}/{n} ({round(100 * matched / n) if n else 0}%)")
        print("  missed:", [r["expected_text"][:40] for r in report["results"] if not r["matched"]] or "none")
        matched_titles = {r["actual_text"] for r in report["results"] if r["matched"]}
        print("  extra:", [s["title"][:40] for s in suggestions if s["title"] not in matched_titles] or "none")
        quoted = sum(1 for s in suggestions if s["evidence"])
        print(f"  evidence: {quoted}/{n} suggestions carry a verified verbatim quote\n")

    print("=" * 100)
    forloop = (DATASETS / "inputs" / "input-sow-forloop.txt").read_text()
    print("forloop SOW (real-style, NO answer key) - suggestions for a person to judge:\n")
    for name, (definition, exhaustive) in VARIANTS.items():
        suggestions = suggest_deliverables_from_text(forloop, definition, exhaustive)
        print(f"[{name}] -> {len(suggestions)} suggested")
        if name.startswith("default definition ("):
            for s in suggestions:
                print(f"   - {s['title']}  | owner: {s['owner']} | due: {s['deadline']}")
                if s["evidence"]:
                    print(f"       evidence: \"{s['evidence'][:90]}\"")
        print()


if __name__ == "__main__":
    main()
