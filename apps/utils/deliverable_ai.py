"""LLM-backed helpers for the Deliverables register - both only PROPOSE; a person accepts or edits (nothing is applied
silently): (1) suggest deliverables from a project's SOW, (2) propose which deliverable each task contributes to.

What counts as a deliverable is a judgment call, not an extraction fact (the same SOW gave 3 or 13 items under two
one-line wordings - DECISIONS.md). DELIVERABLE_DEFINITION below is therefore a DEFAULT written by Claude, meant to be
reviewed and replaced by the user's own definition; suggest_deliverables_from_text takes an override for experiments."""
import json
import re

from apps.llm import complete
from apps.logger import get_logger

logger = get_logger("DELIVERABLE_AI")

DELIVERABLE_DEFINITION = (
    "A deliverable is a named, tangible output that the client receives or signs off on: a document or report, a "
    "working module / feature / view / integration, a deployed or launched system, a training session. It is NOT an "
    "internal activity (testing, reviewing, coordinating, attending meetings, setup work) and NOT one small step of "
    "building something: when several bullets build one thing, that one thing is the deliverable."
)

ASSIGNMENT_BATCH = 50


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _json_call(prompt: str) -> dict:
    response = complete(messages=[{"role": "user", "content": prompt}], temperature=0, response_format={"type": "json_object"})
    return json.loads(response.choices[0].message.content)


EXHAUSTIVE_INSTRUCTION = (
    "Go through the whole SOW section by section and list EVERY distinct output that fits the definition, even if it is "
    "mentioned only once or only inside a description of the scope. A person reviews your list, so prefer including a "
    "plausible item over leaving it out."
)


def suggest_deliverables_from_text(sow_text: str, definition: str = None, exhaustive: bool = True) -> list:
    """-> [{title, owner, deadline, evidence}]. The owner must be a person the SOW names as responsible for that item
    (never the client organization); evidence is kept only if it really is a verbatim quote from the SOW."""
    prompt = f"""Read this Statement of Work and list its DELIVERABLES.

{definition or DELIVERABLE_DEFINITION}
{EXHAUSTIVE_INSTRUCTION if exhaustive else ""}

For each deliverable give:
- "title": a short name for what is delivered
- "owner": ONLY a person the SOW names as responsible for producing it; null if no person is named (never the client's organization)
- "deadline": exactly as written in the SOW, or null
- "evidence": a short VERBATIM quote from the SOW that supports it (copy it exactly)

Do not invent items. If the SOW has no clear deliverables, return an empty list.
Return only JSON: {{"deliverables": [{{"title": "...", "owner": null, "deadline": null, "evidence": "..."}}]}}

### SOW
{sow_text}
"""
    try:
        raw = _json_call(prompt).get("deliverables", [])
    except Exception as e:
        logger.error(f"Deliverable suggestion call failed: {e}")
        raise RuntimeError("The assistant could not read the SOW right now - try again.") from e
    haystack = _norm(sow_text)
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        quote = str(item.get("evidence") or "").strip()
        out.append({
            "title": str(item["title"]).strip(),
            "owner": (str(item["owner"]).strip() or None) if item.get("owner") else None,
            "deadline": (str(item["deadline"]).strip() or None) if item.get("deadline") else None,
            "evidence": quote[:400] if quote and _norm(quote) in haystack else None,  # a made-up quote is never shown
        })
    return out


_NUMBER = re.compile(r"\b(\d+\.\d+)\b")


def propose_assignments(deliverables: list, tasks: list) -> list:
    """deliverables: [{deliverable_id, number, title, owner}], tasks: [{task_id, task_text, owner}] (unassigned only).
    -> [{task_id, task_text, deliverable_id|None, number, title, method: 'number'|'llm', confidence}].
    A task that cites exactly one known deliverable number ("... (Deliverable 4.6)") is assigned deterministically; the
    rest go to the LLM in batches, which may answer 'none' for cross-cutting or unclear tasks."""
    by_number = {d["number"]: d for d in deliverables if d.get("number")}
    proposals, pending = [], []
    for t in tasks:
        cited = {n for n in _NUMBER.findall(t["task_text"]) if n in by_number}
        if len(cited) == 1:
            d = by_number[next(iter(cited))]
            proposals.append(_proposal(t, d, "number", "high"))
        else:
            pending.append(t)

    for start in range(0, len(pending), ASSIGNMENT_BATCH):
        batch = pending[start:start + ASSIGNMENT_BATCH]
        d_lines = "\n".join(f'D{i + 1}: {d.get("number") or ""} {d["title"]}' + (f' (owner: {d["owner"]})' if d.get("owner") else "")
                            for i, d in enumerate(deliverables))
        t_lines = "\n".join(f'T{i + 1}: {t["task_text"]}' + (f' (owner: {t["owner"]})' if t.get("owner") else "")
                            for i, t in enumerate(batch))
        prompt = f"""Below are a project's deliverables (D1, D2, ...) and some tasks (T1, T2, ...).
For each task decide which ONE deliverable it contributes to or restates. Use null when the task is cross-cutting, spans
several deliverables, or you cannot tell - a wrong assignment is worse than none. Several tasks may share a deliverable.

Return only JSON: {{"assignments": [{{"task": "T1", "deliverable": "D3" or null, "confidence": "high" | "medium" | "low"}}]}}

### Deliverables
{d_lines}

### Tasks
{t_lines}
"""
        try:
            answers = {a.get("task"): a for a in _json_call(prompt).get("assignments", []) if isinstance(a, dict)}
        except Exception as e:
            logger.error(f"Assignment call failed for a batch of {len(batch)} tasks: {e}")
            answers = {}
        for i, t in enumerate(batch):
            a = answers.get(f"T{i + 1}") or {}
            ref = str(a.get("deliverable") or "")
            m = re.fullmatch(r"D(\d+)", ref.strip().upper())
            d = deliverables[int(m.group(1)) - 1] if m and 0 < int(m.group(1)) <= len(deliverables) else None
            confidence = a.get("confidence") if a.get("confidence") in ("high", "medium", "low") else "low"
            proposals.append(_proposal(t, d, "llm", confidence if d else None))
    order = {t["task_id"]: i for i, t in enumerate(tasks)}
    return sorted(proposals, key=lambda p: order[p["task_id"]])


def _proposal(task: dict, deliverable, method: str, confidence) -> dict:
    return {
        "task_id": str(task["task_id"]), "task_text": task["task_text"],
        "deliverable_id": str(deliverable["deliverable_id"]) if deliverable else None,
        "number": deliverable.get("number") if deliverable else None,
        "title": deliverable["title"] if deliverable else None,
        "method": method, "confidence": confidence,
    }
