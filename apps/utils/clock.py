"""'Today' for everything the app works out from dates (hours as of, who is free, what is late, "by next Friday" in a transcript).

For demos the date can be frozen with DEMO_TODAY=YYYY-MM-DD, so the same questions give the same answers whatever day it really is. It is NOT
used for calendar invites or the meeting coordinator: an invite has to be in the real future. Unset DEMO_TODAY (or set it empty) for real use."""
import os
from datetime import date, datetime, timezone


def demo_today():
    raw = (os.getenv("DEMO_TODAY") or "").strip()
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def today() -> date:
    return demo_today() or date.today()


def now() -> datetime:
    """The moment 'now' means for look-backs such as 'the last 7 days': the end of the frozen day, or the real time."""
    frozen = demo_today()
    return datetime(frozen.year, frozen.month, frozen.day, 23, 59, 59, tzinfo=timezone.utc) if frozen else datetime.now(timezone.utc)
