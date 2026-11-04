from datetime import date

from apps.agent import tool_registry as tr


def _report(weeks):
    return {"people": [{"name": "Bo", "weeks": weeks, "capacity_in_window": 60.0, "planned_load_in_window": 39.3, "available_hours": 20.7,
                        "logged_in_past_weeks": 0.0}]}


def test_totals_over_several_weeks_say_how_many_weeks_they_cover(monkeypatch):
    monkeypatch.setattr(tr, "availability_report", lambda *a, **k: _report([{"week": "2026-11-02"}, {"week": "2026-11-09"}]))
    person = tr._find_available_people("halvane", from_date="2026-11-02", to_date="2026-11-09")["people"][0]
    assert person["available_hours_across_all_2_weeks"] == 20.7 and "available_hours" not in person


def test_a_single_week_keeps_its_plain_figures(monkeypatch):
    monkeypatch.setattr(tr, "availability_report", lambda *a, **k: _report([{"week": "2026-11-02"}]))
    assert tr._find_available_people("halvane", from_date="2026-11-02", to_date="2026-11-08")["people"][0]["available_hours"] == 20.7
