"""Pure unit tests for _parse_deadline_window - no DB, no LLM, just date math."""
from mcp_server.tools.calendar import _parse_deadline_window


def test_valid_iso_date_produces_a_full_day_window():
    time_min, time_max = _parse_deadline_window("2026-10-15")

    assert time_min == "2026-10-15T00:00:00Z"
    assert time_max == "2026-10-15T23:59:59Z"


def test_no_deadline_falls_back_to_a_24_hour_window_from_now():
    time_min, time_max = _parse_deadline_window(None)

    assert time_min < time_max
    # a 24h window, not some other arbitrary span
    from datetime import datetime
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    delta = datetime.strptime(time_max, fmt) - datetime.strptime(time_min, fmt)
    assert delta.total_seconds() == 86400


def test_malformed_date_falls_back_instead_of_raising():
    # "tomorrow EOD" isn't a strict YYYY-MM-DD - must not raise, must fall back.
    time_min, time_max = _parse_deadline_window("tomorrow EOD")

    assert time_min < time_max
