"""What needs attention across all projects, for the Dashboard's tiles.

Every figure here is a COUNT of things that are wrong, with the projects (or people) that own them: never a sum or an average, because a healthy
project would offset a troubled one (total hours left can look fine while one project is over budget). Per-project numbers stay on the
per-project cards."""
from datetime import date, timedelta

from apps.db.db import get_connection, list_raid_items
from apps.db.hours_db import availability_report, fetch_hours_inputs, list_project_ids
from apps.utils import clock
from apps.utils.hours import _due_date

CAPACITY_WINDOW_DAYS = 28  # the Capacity page's default window: today and the next four weeks


def _high_priority_open() -> dict:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""SELECT project_id, COUNT(*) FROM tasks WHERE is_active AND project_id IS NOT NULL AND priority = 'high' AND status <> 'completed'
                       GROUP BY 1""")
        rows = dict(cur.fetchall())
    conn.close()
    return rows


def attention(today: date = None) -> dict:
    """-> {"overdue": {project: n}, "high_priority_open": {project: n}, "high_impact_risks": {project: n},
           "people_over_capacity": [{"name", "projects": [...]}]} - projects with nothing wrong are left out."""
    today = today or clock.today()
    overdue, risks, over_capacity = {}, {}, {}
    for pid in list_project_ids():
        settings, tasks, _entries = fetch_hours_inputs(pid)
        start_day = int((settings or {}).get("week_start_day") or 0)
        late = 0
        for t in tasks:
            if t.get("status") == "completed":
                continue
            due = _due_date(t, (settings or {}).get("kickoff_date"), start_day)  # the same due date the Capacity page works from
            late += bool(due and due < today)
        if late:
            overdue[pid] = late
        live = [r for r in list_raid_items(pid) if r["review_state"] == "confirmed" and r["item_type"] in ("risk", "issue")
                and r["status"] in ("open", "mitigating") and r["impact"] == "high"]
        if live:
            risks[pid] = len(live)
        for p in availability_report(pid, today, today + timedelta(days=CAPACITY_WINDOW_DAYS - 1))["people"]:
            if p["over_weeks"]:
                over_capacity.setdefault(p["name"], []).append(pid)  # a person in two projects is one person
    return {"overdue": overdue, "high_priority_open": _high_priority_open(), "high_impact_risks": risks,
            "people_over_capacity": [{"name": n, "projects": ps} for n, ps in sorted(over_capacity.items())]}
