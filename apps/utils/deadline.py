import re
from datetime import date, timedelta

from dateutil import parser as dateutil_parser

# Weekday name → weekday index (Monday=0 … Sunday=6)
_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}


def resolve_deadline(text: str, today: date = None) -> str:
    """Convert a natural-language deadline string to an ISO date (YYYY-MM-DD).

    Handles:
    - Absolute dates embedded anywhere: "April 14, 2026", "May 12, 2026"
    - Dates inside parentheses: "Week 6 (May 12, 2026)" → "2026-05-12"
    - Relative: "today", "tomorrow", "next Monday", "this Friday"
    - Shorthand: "EOD", "EOW" / "end of week", "EOM" / "end of month"
    - Fallback: dateutil fuzzy parse on the full string
    - If nothing parses: return the original string unchanged
    """
    if not text or not isinstance(text, str):
        return text

    today = today or date.today()
    t = text.strip()
    tl = t.lower()

    # -- 1. Extract date from parentheses first --------------------------------
    # e.g. "Week 6 (May 12, 2026)" → try parsing "May 12, 2026"
    paren = re.search(r'\(([^)]+)\)', t)
    if paren:
        try:
            parsed = dateutil_parser.parse(paren.group(1), fuzzy=False)
            return parsed.strftime("%Y-%m-%d")
        except Exception:
            pass

    # -- 2. Relative keywords --------------------------------------------------
    if re.search(r'\btoday\b', tl) or re.search(r'\beod\s*today\b', tl):
        return today.strftime("%Y-%m-%d")

    if re.search(r'\btomorrow\b', tl):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    if re.search(r'\beow\b', tl) or re.search(r'\bend\s+of\s+week\b', tl):
        # Next Friday (or this Friday if today is not Friday)
        days_until_friday = (4 - today.weekday()) % 7 or 7
        return (today + timedelta(days=days_until_friday)).strftime("%Y-%m-%d")

    if re.search(r'\beom\b', tl) or re.search(r'\bend\s+of\s+month\b', tl):
        # Last day of current month
        first_next = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_day = first_next - timedelta(days=1)
        return last_day.strftime("%Y-%m-%d")

    # -- 3. "next <weekday>" or "<weekday>" with "next" nearby ----------------
    for name, idx in _WEEKDAY_INDEX.items():
        if re.search(rf'\b{name}\b', tl):
            days_ahead = (idx - today.weekday()) % 7 or 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # -- 4. Dateutil fuzzy parse as final fallback -----------------------------
    # Only trust the result when the input contains a digit (avoids spurious parses
    # of pure-word strings like "Week 6" that dateutil may silently mis-parse).
    if re.search(r'\d', t):
        try:
            parsed = dateutil_parser.parse(t, fuzzy=True)
            return parsed.strftime("%Y-%m-%d")
        except Exception:
            pass

    # -- 5. Nothing worked - return original string unchanged ------------------
    return t
