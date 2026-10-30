"""Finds the total hours budget stated in a SOW. It only PROPOSES: the number is kept only if the model's evidence is a verbatim
quote from the document and the number really appears in that quote, so a made-up figure is dropped (same rule as raid_ai.py).
A weekly availability ("20 hours per week") or a duration in days is not a budget and is never converted."""
import json
import re

from apps.llm import complete
from apps.logger import get_logger
from apps.utils.raid_ai import _norm, _quote_in_text

logger = get_logger("HOURS_BUDGET_AI")
MAX_CHARS = 60_000


def _numbers_in(text: str) -> set:
    out = set()
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            out.add(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def suggest_budget_hours(text: str) -> dict:
    """-> {budget_hours: float | None, evidence: str | None}"""
    prompt = f"""Read this Statement of Work and find the TOTAL effort budget for the whole project, stated in hours (for example
"a total of 1,200 hours", "capped at 800 person-hours", "not to exceed 640 hours").

Rules:
- Only a total for the whole engagement counts. A weekly availability ("20 hours per week"), a duration in weeks or days, a price,
  or hours for one part of the work is NOT the budget.
- If the document gives no total in hours, return null. Never calculate or convert.
- "evidence": a short VERBATIM quote from the document that states the number (copy it exactly).

Return only JSON: {{"budget_hours": 1200, "evidence": "..."}} or {{"budget_hours": null, "evidence": null}}

### Document
{text[:MAX_CHARS]}
"""
    try:
        resp = complete(messages=[{"role": "user", "content": prompt}], temperature=0, response_format={"type": "json_object"})
        data = json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.error(f"Budget extraction call failed: {e}")
        raise RuntimeError("The assistant could not read the document right now - try again.") from e
    value, quote = data.get("budget_hours"), str(data.get("evidence") or "").strip()
    try:
        value = float(value) if value is not None else None
    except (TypeError, ValueError):
        value = None
    if value is None or value <= 0 or not quote:
        return {"budget_hours": None, "evidence": None}
    if not _quote_in_text(quote, _norm(text)) or value not in _numbers_in(quote):
        logger.warning("Budget suggestion dropped: the quote is not in the document or does not contain the number.")
        return {"budget_hours": None, "evidence": None}
    return {"budget_hours": value, "evidence": quote[:400]}
