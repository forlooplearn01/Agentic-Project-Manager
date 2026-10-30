"""A 'blocker' event must not blank the task's status.

Regression: infer_event_type only returns 'blocker' when the event has NO status (status is checked first), but
apply_task_updates wrote event['status'] into the task for blocker events - so the status became empty. In the forloop
project 'Review VPN access resolution' had a blank status next to its blocker text."""
from apps.db.db import get_connection, insert_task
from apps.nodes.format import apply_task_updates


def _task_row(task_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT status, blocker FROM tasks WHERE task_id = %s", (task_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def _state(project_id, task_id, **event):
    return {"project_id": project_id, "planned_tasks": [], "matched_events": [
        {"matched_task_id": task_id, "event_type": "blocker", "task_text": "Review VPN access", **event}]}


def test_blocker_event_without_a_status_marks_the_task_blocked_not_blank(test_project):
    task_id = insert_task({"task_text": "Review VPN access", "normalized_task_text": "review vpn access", "status": "in_progress"}, test_project)

    apply_task_updates(_state(test_project, task_id, status=None, blocker="VPN access not resolved"))

    assert _task_row(task_id) == ("blocked", "VPN access not resolved")


def test_a_status_the_event_does_state_is_kept(test_project):
    task_id = insert_task({"task_text": "Review VPN access", "normalized_task_text": "review vpn access"}, test_project)

    apply_task_updates(_state(test_project, task_id, status="on_hold", blocker="waiting on IT"))

    assert _task_row(task_id) == ("on_hold", "waiting on IT")
