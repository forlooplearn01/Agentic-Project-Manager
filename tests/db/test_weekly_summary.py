"""The PMO's weekly summary (apps/utils/weekly_summary.py): plain text built from numbers the app already has."""
from datetime import date

from fastapi.testclient import TestClient

from apps.api import app
from apps.db.db import insert_task, upsert_project_person
from apps.db.hours_db import save_timesheet_week, set_hours_settings, set_task_hours
from apps.db.suggestions_db import create_suggestion
from apps.utils.weekly_summary import build_weekly_summary

client = TestClient(app)


def test_the_summary_reports_status_hours_attention_capacity_and_waiting_suggestions(test_project):
    upsert_project_person(test_project, "Ana Lopez", "ana@example.com", "Engineer")
    from apps.db.hours_db import set_person_capacity
    set_person_capacity(test_project, "Ana Lopez", weekly_capacity_hours=10)
    set_hours_settings(test_project, budget_hours=100, kickoff_date=date(2026, 10, 12))
    t = str(insert_task({"task_text": "Build search", "normalized_task_text": "build search", "owner": "Ana Lopez", "status": "in_progress"}, test_project))
    set_task_hours(t, estimate_hours=20, plan_start_week=1, plan_finish_week=3)
    insert_task({"task_text": "Write the guide", "normalized_task_text": "write the guide", "owner": "Ana Lopez", "status": "not_started"}, test_project)
    save_timesheet_week(test_project, "Ana Lopez", date(2026, 10, 19), [{"task_id": t, "hours": 25}])
    create_suggestion(test_project, "new_task", "Prepare the runbook", {"owner": "Ana Lopez"})
    out = build_weekly_summary(test_project, today=date(2026, 10, 29))
    text = out["text"]
    assert out["subject"].startswith(f"{test_project}: weekly status") and text.startswith(f"{test_project}: weekly summary, 29 Oct 2026")
    assert "Hours: 25 of 100 budget used" in text and "Build search: 25 h logged of 20 h estimated, still open (Ana Lopez)" in text
    assert "Write the guide: no estimate, left out of the forecast" in text and "Waiting for your decision: 1 suggestion(s)" in text
    assert client.get(f"/projects/{test_project}/weekly-summary").json()["text"].count("\n") > 3


def test_a_project_with_nothing_yet_still_gives_a_summary(test_project):
    text = build_weekly_summary(test_project, today=date(2026, 10, 29))["text"]
    assert "no budget set" in text and "Waiting for your decision" not in text
