from datetime import date

from apps.utils import dashboard

TODAY = date(2026, 10, 29)


def _patch(monkeypatch, tasks_by_project, raid, people):
    monkeypatch.setattr(dashboard, "list_project_ids", lambda: sorted(tasks_by_project))
    monkeypatch.setattr(dashboard, "fetch_hours_inputs", lambda pid: ({"kickoff_date": date(2026, 9, 14)}, tasks_by_project[pid], []))
    monkeypatch.setattr(dashboard, "list_raid_items", lambda pid: raid.get(pid, []))
    monkeypatch.setattr(dashboard, "availability_report", lambda pid, a, b: {"people": people.get(pid, [])})
    monkeypatch.setattr(dashboard, "_high_priority_open", lambda: {"a": 2})


def _risk(impact, status="open", item_type="risk", review_state="confirmed"):
    return {"review_state": review_state, "item_type": item_type, "status": status, "impact": impact}


def test_counts_per_project_and_leaves_out_projects_with_nothing_wrong(monkeypatch):
    tasks = {"a": [{"status": "in_progress", "deadline": "2026-10-01", "plan_finish_week": None},     # overdue
                   {"status": "completed", "deadline": "2026-10-01", "plan_finish_week": None},       # done: not overdue
                   {"status": "in_progress", "deadline": "2026-12-01", "plan_finish_week": None}],    # not due yet
             "b": [{"status": "in_progress", "deadline": None, "plan_finish_week": 3}]}               # the plan's own due date (week 3) has passed
    raid = {"a": [_risk("high"), _risk("high", "mitigating", "issue"), _risk("high", "closed"), _risk("low"), _risk("high", review_state="suggested"),
                  _risk("high", item_type="assumption")]}
    _patch(monkeypatch, tasks, raid, {})
    r = dashboard.attention(TODAY)
    assert r["overdue"] == {"a": 1, "b": 1}
    assert r["high_impact_risks"] == {"a": 2}  # open and mitigating count; closed, low impact, unreviewed and assumptions do not
    assert r["high_priority_open"] == {"a": 2}


def test_a_person_in_two_projects_is_one_person_over_capacity(monkeypatch):
    over = {"name": "Rucheek", "over_weeks": 2}
    fine = {"name": "Ana", "over_weeks": 0}
    _patch(monkeypatch, {"a": [], "b": []}, {}, {"a": [over, fine], "b": [dict(over)]})
    assert dashboard.attention(TODAY)["people_over_capacity"] == [{"name": "Rucheek", "projects": ["a", "b"]}]
