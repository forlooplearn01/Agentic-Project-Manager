"""Node-level eval for AI RAID suggestions (apps/utils/raid_ai.suggest_raid_items_from_text).

Measured part: the synthetic SOW with its numbered "7. ASSUMPTIONS" and "8. DEPENDENCIES" sections REMOVED; the 9 assumptions
and 6 dependencies in those sections are the answer key (apps/utils/sow_sections.parse_raid_sections on the original). The
assistant's items of the matching type are matched to the key by text similarity with eval/compare.py's optimal one-to-one
matching. Before trusting a cutoff the script PRINTS the evidence for it: how similar are two DISTINCT key items (the ceiling
for "different") and how similar is each key item to its best extraction (the floor for "same").

Limits, stated plainly: the removed lists are partly recoverable from the rest of the SOW (timeline, scope) and it is ONE
synthetic document; there is no answer key for RISKS or ISSUES at all, so the second part only PRINTS what the assistant
extracts from the meeting minutes and the forloop SOW for a person to judge. Real LLM calls; nothing is written to the database.

    docker compose exec api python3 eval/nodes/eval_raid_suggest.py
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from apps.utils.raid_ai import suggest_raid_items_from_text
from apps.utils.sow_sections import parse_raid_sections, title_and_description
from eval import compare
from eval.compare import _similarity_matrix, compare_tasks
from eval.golden_parser import GoldenTask

DATASETS = Path(__file__).parent.parent.parent / "datasets"


def strip_sections_7_and_8(text: str) -> str:
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if re.match(r"^\s*7\.\s+ASSUMPTIONS\s*$", l))
    end = next(i for i in range(start + 1, len(lines)) if re.match(r"^\s*9\.\s+\S", lines[i]))
    return "\n".join(lines[:start] + lines[end:])


def main():
    sow = (DATASETS / "simulation-ai-x-vmware" / "sow.txt").read_text()
    key = parse_raid_sections(sow)
    stripped = strip_sections_7_and_8(sow)
    assert parse_raid_sections(stripped) == [] and len(key) == 15, "sections 7/8 were not removed cleanly"
    extracted = suggest_raid_items_from_text(stripped, "Statement of Work")

    print(f"Synthetic SOW with sections 7 and 8 removed ({len(stripped)} chars); key = 9 assumptions + 6 dependencies\n")
    print("Extracted by type:", dict(Counter(i["item_type"] for i in extracted)), "| with a verified quote:", sum(1 for i in extracted if i["evidence"]), "of", len(extracted))
    print(f"(compare.py similarity cutoff in use: {compare.MATCH_THRESHOLD})\n")

    for item_type in ("assumption", "dependency"):
        key_items = [k for k in key if k["item_type"] == item_type]
        mine = [i for i in extracted if i["item_type"] == item_type]
        key_titles = [title_and_description(k["text"])[0] for k in key_items]
        # evidence for the cutoff
        kk = _similarity_matrix(key_titles, key_titles)
        distinct_max = max(kk[i][j] for i in range(len(kk)) for j in range(len(kk)) if i != j)
        km = _similarity_matrix(key_titles, [m["title"] for m in mine]) if mine else []
        best = sorted((max(row) for row in km), reverse=True) if km else []
        report = compare_tasks([GoldenTask(text=t) for t in key_titles], [{"task_text": m["title"]} for m in mine])
        matched = report["matched_count"]
        print(f"[{item_type}s] key {len(key_items)} | extracted {len(mine)} | recall {matched}/{len(key_items)} ({round(100 * matched / len(key_items))}%)"
              f" | precision {matched}/{len(mine)} ({round(100 * matched / len(mine)) if mine else 0}%)")
        print(f"   evidence for the cutoff: most similar pair of DISTINCT key items = {distinct_max:.2f}; "
              f"each key item's best extraction scores: {[round(b, 2) for b in best]}")
        print("   missed:", [r["expected_text"][:46] for r in report["results"] if not r["matched"]] or "none")
        matched_titles = {r["actual_text"] for r in report["results"] if r["matched"]}
        print("   extra:", [m["title"][:46] for m in mine if m["title"] not in matched_titles] or "none", "\n")

    # Assumption vs dependency is a judgement call even for people ("NovaTech will provide credentials" is both), so also score
    # the two types POOLED: did the assistant surface the item at all, whichever of the two it filed it under?
    pool_key = [GoldenTask(text=title_and_description(k["text"])[0]) for k in key]
    pool_mine = [{"task_text": i["title"]} for i in extracted if i["item_type"] in ("assumption", "dependency")]
    pooled = compare_tasks(pool_key, pool_mine)
    m = pooled["matched_count"]
    print(f"[assumptions + dependencies pooled] key {len(key)} | extracted {len(pool_mine)} | recall {m}/{len(key)} ({round(100 * m / len(key))}%)"
          f" | precision {m}/{len(pool_mine)} ({round(100 * m / len(pool_mine)) if pool_mine else 0}%)\n")

    print("=" * 100)
    print("Risks and issues have NO answer key - what the assistant extracts, for a person to judge:\n")
    docs = [("forloop SOW", DATASETS / "inputs" / "input-sow-forloop.txt", "Statement of Work")]
    docs += [(f"meeting-{n:02d}", DATASETS / "simulation-ai-x-vmware" / f"meeting-{n:02d}.txt", "set of meeting minutes") for n in (3, 5, 7)]
    for name, path, kind in docs:
        items = suggest_raid_items_from_text(path.read_text(), kind)
        print(f"[{name}] -> {len(items)} items {dict(Counter(i['item_type'] for i in items))}")
        for i in items:
            tail = f" | owner {i['owner']}" if i["owner"] else ""
            tail += f" | prob {i['probability']}/impact {i['impact']}" if i["probability"] or i["impact"] else ""
            print(f"   - {i['item_type']:10s} {i['title'][:88]}{tail}")
        print()


if __name__ == "__main__":
    main()
