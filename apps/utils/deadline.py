import re
from datetime import date, timedelta

from dateutil import parser as dateutil_parser
from apps.utils import clock

# Weekday name → weekday index (Monday=0 … Sunday=6)
_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}


_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
# A real calendar date: ISO, numeric d/m/y, "Month day" or "day Month". "may" alone (the verb) never matches because a day
# number must sit next to the month name.
_EXPLICIT_DATE = re.compile(
    rf"\b\d{{4}}-\d{{1,2}}-\d{{1,2}}\b"
    rf"|\b\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}\b"
    rf"|\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b",
    re.IGNORECASE,
)
# A position in a plan, not a date: "Week 2", "wk 3", "Sprint 4", "Phase 2", "Day 5", "Month 3", "Q2".
_PLAN_POSITION = re.compile(r"\b(?:week|wk|sprint|phase|milestone|day|month)\s*#?\d+\b|\bq[1-4]\b", re.IGNORECASE)


def resolve_deadline(text: str, today: date = None) -> str:
    """Convert a natural-language deadline string to an ISO date (YYYY-MM-DD).

    Handles:
    - Absolute dates embedded anywhere: "April 14, 2026", "May 12, 2026"
    - Dates inside parentheses: "Week 6 (May 12, 2026)" → "2026-05-12"
    - Relative: "today", "tomorrow", "next Monday", "this Friday"
    - Shorthand: "EOD", "EOW" / "end of week", "EOM" / "end of month"
    - Explicit calendar dates ("April 14", "14 April", "2026-05-12", "5/12/2026") via a dateutil parse
    - If nothing parses: return the original string unchanged

    Deliberately NOT resolved - the original wording is kept, because there is no real date in the text and inventing
    one is worse than showing the phrase: positions in a plan ("end of Week 2", "Sprint 4", "Phase 2", "Q2") and
    durations ("in 3 days", "within 10 business days"). A digit alone never makes something a date.
    """
    if not text or not isinstance(text, str):
        return text

    today = today or clock.today()
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

    # -- 2. Explicit calendar date anywhere in the text ------------------------
    # Wins over relative words: "next Monday, May 4" means May 4. The fuzzy parse is only ever reached when the text really
    # contains a date, so a stray digit ("Week 3", "in 2 weeks") can no longer be read as a day of the current month.
    explicit = _EXPLICIT_DATE.search(t)
    if explicit:
        try:
            # Parse from where the date starts, so a number before it ("Week 6, May 12, 2026") cannot leak into the date.
            parsed = dateutil_parser.parse(t[explicit.start():], fuzzy=True)
            return parsed.strftime("%Y-%m-%d")
        except Exception:
            pass

    # -- 3. A position in the plan ("end of Week 2", "Q2") is not a date -------
    # Checked before the relative keywords below: "end of Week 2" would otherwise match "end of week" and become this Friday.
    if _PLAN_POSITION.search(t):
        return t

    # -- 4. Relative keywords --------------------------------------------------
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

    # -- 5. "next <weekday>" or "<weekday>" with "next" nearby ----------------
    for name, idx in _WEEKDAY_INDEX.items():
        if re.search(rf'\b{name}\b', tl):
            days_ahead = (idx - today.weekday()) % 7 or 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # -- 6. Nothing worked - return original string unchanged ------------------
    return t
