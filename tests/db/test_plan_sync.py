"""Applying versions of the plan file to the project's tasks (apps/db/plan_sync.py): ids, name matching, history from the files,
baseline on the first read, and holding a version that drops most of the plan."""
import pytest
from psycopg2.extras import RealDictCursor

from apps.db.db import get_connection, insert_task
from apps.db.plan_db import list_plan_changes, plan_summary
from apps.db.plan_sync import apply_plan_version


def row(label, ref, est, owner="Ana", status=None, start=1, finish=2, phase="Build", depends=None):
    return {"label": label, "task_ref": ref, "estimate": est, "owner": owner, "status": status, "start_wk": start, "finish_wk": finish,
            "phase": phase, "depends": depends, "notes": None, "source_ref": "", "row": 0}


def plan(*rows, **extra):
    return {"rows": list(rows), "budget_hours": None, "role_budgets": {}, "kickoff_date": None, "warnings": [], "layout": {}, **extra}


def tasks(project, active=True):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM tasks WHERE project_id = %s" + (" AND is_active" if active else "") + " ORDER BY created_at", (project,))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def by_text(project, text):
    return next(t for t in tasks(project, active=False) if t["task_text"] == text)


V1 = lambda: plan(row("Build the login page", "T-1", 12), row("Build search", "T-2", 20, owner="Bo"), row("Write the user guide", "T-3", 8, owner="Cy"))  # noqa: E731


def apply(project, parsed, **kw):
    return apply_plan_version(project, parsed, "plan.xlsx", use_llm=False, **kw)


def test_the_first_version_creates_the_tasks_sets_the_baseline_and_builds_the_roster(test_project):
    r = apply(test_project, V1())
    assert r["status"] == "applied" and r["added"] == 3 and r["baseline_set"] and r["removed"] == 0
    t = by_text(test_project, "Build search")
    assert t["plan_ref"] == "T-2" and float(t["estimate_hours"]) == 20 and t["owner"] == "Bo" and t["status"] == "not_started"
    assert plan_summary(test_project)["baseline_hours"] == 40
    history = list_plan_changes(test_project)
    assert sorted(h["field"] for h in history) == ["baseline_set", "plan_imported"]  # two lines for the whole plan, not one per task
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM project_people WHERE project_id = %s ORDER BY name", (test_project,))
        assert [r[0] for r in cur.fetchall()] == ["Ana", "Bo", "Cy"]
    conn.close()


def test_a_later_version_changes_adds_and_removes_and_the_history_comes_from_the_files(test_project):
    apply(test_project, V1())
    v2 = plan(row("Build the login page", "T-1", 16, status="in_progress", owner="Dee"),  # estimate, status, owner changed
              row("Build search", "T-2", 20, owner="Bo"),  # unchanged
              row("Add password reset", "T-4", 6))  # new; T-3 (the user guide) is gone
    r = apply(test_project, v2)
    assert (r["added"], r["changed"], r["removed"]) == (1, 1, 1) and not r["baseline_set"]
    login = by_text(test_project, "Build the login page")
    assert float(login["estimate_hours"]) == 16 and login["status"] == "in_progress" and login["owner"] == "Dee"
    assert by_text(test_project, "Write the user guide")["is_active"] is False
    changes = {(h["field"], h["task_text"], h["old_value"], h["new_value"]) for h in list_plan_changes(test_project)}
    assert ("estimate_hours", "Build the login page", "12", "16") in changes
    assert ("status", "Build the login page", "not_started", "in_progress") in changes
    assert ("owner", "Build the login page", "Ana", "Dee") in changes
    assert ("task_removed", "Write the user guide", "8", None) in changes
    assert any(f == "task_added" and t == "Add password reset" for f, t, _, _ in changes)
    assert any(h["field"] == "plan_file_read" and "1 added, 1 changed, 1 removed" in h["new_value"] for h in list_plan_changes(test_project))
    assert plan_summary(test_project)["baseline_hours"] == 40  # the baseline stays what the first version said


def test_reading_the_same_plan_again_changes_nothing(test_project):
    apply(test_project, V1())
    before = len(list_plan_changes(test_project))
    r = apply(test_project, V1())
    assert (r["added"], r["changed"], r["removed"]) == (0, 0, 0) and len(list_plan_changes(test_project)) == before and len(tasks(test_project)) == 3


def test_a_renamed_task_keeps_its_id_and_its_logged_history(test_project):
    apply(test_project, V1())
    r = apply(test_project, plan(row("Build the sign-in page", "T-1", 12), row("Build search", "T-2", 20, owner="Bo"), row("Write the user guide", "T-3", 8, owner="Cy")))
    assert r["changed"] == 1 and r["added"] == 0 and len(tasks(test_project)) == 3
    assert any(h["field"] == "task_text" and h["old_value"] == "Build the login page" for h in list_plan_changes(test_project))


def test_tasks_that_exist_without_an_id_are_matched_by_name_and_take_the_id(test_project):
    existing = str(insert_task({"task_text": "Build search", "normalized_task_text": "build search", "owner": "Bo", "status": "in_progress"}, test_project))
    insert_task({"task_text": "Follow up with the client", "normalized_task_text": "follow up with the client", "owner": "Ana", "status": "not_started"}, test_project)
    r = apply(test_project, plan(row("Build search", "T-2", 20, owner="Bo")))
    assert r["added"] == 0 and r["removed"] == 0
    t = by_text(test_project, "Build search")
    assert str(t["task_id"]) == existing and t["plan_ref"] == "T-2" and t["status"] == "in_progress"  # no status in the file: left alone
    assert by_text(test_project, "Follow up with the client")["is_active"]  # never in a plan file: not removed by one


def test_a_version_that_drops_most_of_the_plan_is_held_until_confirmed(test_project):
    apply(test_project, plan(*[row(f"Task number {i}", f"T-{i}", 5) for i in range(1, 9)]))
    r = apply(test_project, plan(row("Task number 1", "T-1", 5)))
    assert r["status"] == "held" and "7 of the 8" in r["reason"] and len(tasks(test_project)) == 8  # nothing was written
    r = apply(test_project, plan(row("Task number 1", "T-1", 5)), force=True)
    assert r["status"] == "applied" and r["removed"] == 7 and len(tasks(test_project)) == 1


def test_a_plan_without_ids_matches_by_name_and_notices_removals(test_project):
    apply(test_project, plan(row("Alpha work item", None, 5), row("Beta work item", None, 6)))
    assert {t["plan_ref"] for t in tasks(test_project)} == {"name:alpha work item", "name:beta work item"}
    r = apply(test_project, plan(row("Alpha work item", None, 9)))
    assert (r["changed"], r["removed"]) == (1, 1) and [t["task_text"] for t in tasks(test_project)] == ["Alpha work item"]


def test_depends_on_links_tasks_by_id_or_name_and_the_budget_comes_from_the_file(test_project):
    from datetime import date
    apply(test_project, plan(row("Build search", "T-2", 20), row("Build the login page", "T-1", 12, depends="T-2"),
                             row("Write the user guide", "T-3", 8, depends="Build search; T-1"), budget_hours=100.0, kickoff_date=date(2026, 9, 28)))
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""SELECT a.task_text, b.task_text FROM task_dependencies d JOIN tasks a ON a.task_id = d.task_id
                       JOIN tasks b ON b.task_id = d.depends_on_task_id WHERE a.project_id = %s ORDER BY 1, 2""", (test_project,))
        links = cur.fetchall()
    conn.close()
    assert links == [("Build the login page", "Build search"), ("Write the user guide", "Build search"), ("Write the user guide", "Build the login page")]
    from apps.db.hours_db import get_hours_settings
    s = get_hours_settings(test_project)
    assert s["budget_hours"] == 100 and s["kickoff_date"] == date(2026, 9, 28)


def test_filling_in_a_blank_owner_or_phase_is_not_a_change(test_project):
    insert_task({"task_text": "Build search", "normalized_task_text": "build search", "owner": None, "status": "not_started"}, test_project)
    r = apply(test_project, plan(row("Build search", "T-2", None, owner="Bo", phase="Build", start=None, finish=None)))
    assert r["changed"] == 0 and by_text(test_project, "Build search")["owner"] == "Bo" and by_text(test_project, "Build search")["phase"] == "Build"
    r = apply(test_project, plan(row("Build search", "T-2", None, owner="Cy", phase="Build", start=None, finish=None)))  # a real change of owner is
    assert r["changed"] == 1 and by_text(test_project, "Build search")["owner"] == "Cy"
