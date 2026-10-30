"""LLM extraction of RAID items (risks, assumptions, issues, dependencies) from a project document. It only PROPOSES: every
item is stored as 'suggested' and a person accepts or rejects it (nothing joins the log silently).

Honesty rules enforced in code, not just asked of the model:
  - evidence is kept only if it is a verbatim quote from the document (a made-up quote is dropped);
  - probability / impact are kept only when that verified quote backs the item - otherwise they stay blank (the user's
    decision: the assistant does not estimate them, DECISIONS.md "Probability and impact");
  - a title of an unknown type, or an empty title, is discarded.
The four definitions below are a DEFAULT written by Claude for the user to review; they are the main lever on quality."""
import json
import os
import re

from apps.llm import complete
from apps.logger import get_logger

logger = get_logger("RAID_AI")

ITEM_TYPES = ("risk", "assumption", "issue", "dependency")
LEVELS = ("low", "medium", "high")
MAX_CHARS = 60_000
# Model for this call only; None = the app-wide LLM_MODEL. Set RAID_AI_MODEL to override (measured in DECISIONS.md).
RAID_MODEL = os.getenv("RAID_AI_MODEL") or None

RAID_DEFINITIONS = """- risk: something that MIGHT happen and could hurt scope, schedule, cost or quality. It is uncertain and in the future.
- assumption: something the plan takes as true that is outside the team's control; if it turns out false, the project suffers.
- issue: a problem that is happening NOW and needs resolving (a blocker, a failure, an unresolved obstruction).
- dependency: something that must be provided or finished by another party or another piece of work before some work can proceed."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _quote_in_text(quote: str, haystack: str) -> bool:
    """True if the quote is really in the document. Models wrap quotes in "..." and mark cuts with "..." / "…", so those are
    stripped and each remaining stretch must appear verbatim (a made-up quote still fails)."""
    pieces = [_norm(p).strip(" \"'“”‘’") for p in re.split(r"\.{3}|…", quote or "")]
    pieces = [p for p in pieces if len(p) >= 8]
    return bool(pieces) and all(p in haystack for p in pieces)


def suggest_raid_items_from_text(text: str, doc_kind: str = "document", definitions: str = None) -> list:
    """-> [{item_type, title, description, owner, probability, impact, evidence}] for one document."""
    prompt = f"""Read this {doc_kind} and extract items for a RAID log: risks, assumptions, issues and dependencies.

Definitions:
{definitions or RAID_DEFINITIONS}

Rules:
- Only extract what the document states or clearly implies. Do not invent anything.
- Do NOT extract ordinary tasks or action items (work someone will simply do), design decisions, or agenda items. In particular
- "title": a short statement of the item (about 100 characters at most). "description": extra detail only if needed, else null.
- "owner": a PERSON the document names as responsible for tracking or resolving it, else null (never an organization).
- "probability" and "impact" (low | medium | high): ONLY if the document itself states or clearly signals them (for example
  "high risk", "unlikely", "would delay go-live by two weeks"). Otherwise null. Do NOT estimate them yourself.
- "evidence": a short VERBATIM quote from the document that supports the item (copy it exactly, without surrounding quote marks or "...").
- Be exhaustive: read the whole document, including scope, timeline, deliverable and acceptance sections, and extract EVERY
  distinct item - a long document usually has many. Each item is one separate statement; do not merge several into one.
- If there is nothing to extract, return an empty list.

Return only JSON: {{"items": [{{"item_type": "risk", "title": "...", "description": null, "owner": null, "probability": null, "impact": null, "evidence": "..."}}]}}

### Document
{text[:MAX_CHARS]}
"""
    try:
        response = complete(messages=[{"role": "user", "content": prompt}], temperature=0, response_format={"type": "json_object"}, model=RAID_MODEL)
        raw = json.loads(response.choices[0].message.content).get("items", [])
    except Exception as e:
        logger.error(f"RAID extraction call failed: {e}")
        raise RuntimeError("The assistant could not read the document right now - try again.") from e

    haystack = _norm(text)
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("item_type") not in ITEM_TYPES or not str(item.get("title") or "").strip():
            continue
        quote = str(item.get("evidence") or "").strip()
        verified = _quote_in_text(quote, haystack)
        out.append({
            "item_type": item["item_type"],
            "title": str(item["title"]).strip()[:200],
            "description": (str(item["description"]).strip() or None) if item.get("description") else None,
            "owner": (str(item["owner"]).strip() or None) if item.get("owner") else None,
            "evidence": quote[:400] if verified else None,
            # Only trusted when the verified quote is what backs them; otherwise blank (never estimated).
            "probability": item.get("probability") if verified and item.get("probability") in LEVELS else None,
            "impact": item.get("impact") if verified and item.get("impact") in LEVELS else None,
        })
    return out
