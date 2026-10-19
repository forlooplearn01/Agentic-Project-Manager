"""Regression test: search_tasks(status='blocked') used to do a literal status='blocked'
equality check, which missed a task that's blocked in every sense the rest of the app uses
(has blocker text, counted as blocked by get_project_overview, shown as blocked on the
dashboard's own stat card) but whose status column was never flipped. Fixed to match the
same definition used everywhere else: status='blocked' OR blocker IS NOT NULL."""
import json

from apps.db.db import insert_task
from mcp_server.tools.postgres import search_tasks


def test_blocker_text_counts_as_blocked_even_with_a_different_status(test_project):
    insert_task({
        "task_text": "Review VPN access resolution",
        "normalized_task_text": "review vpn access resolution",
        "status": "not_started",
        "blocker": "VPN access not granted",
    }, test_project)

    results = json.loads(search_tasks(status="blocked", project_id=test_project))

    assert len(results) == 1
    assert results[0]["task_text"] == "Review VPN access resolution"


def test_literal_blocked_status_still_matches(test_project):
    insert_task({
        "task_text": "Explicitly blocked task",
        "normalized_task_text": "explicitly blocked task",
        "status": "blocked",
    }, test_project)

    results = json.loads(search_tasks(status="blocked", project_id=test_project))

    assert len(results) == 1


def test_task_with_no_blocker_and_normal_status_is_excluded(test_project):
    insert_task({
        "task_text": "Perfectly fine task",
        "normalized_task_text": "perfectly fine task",
        "status": "in_progress",
    }, test_project)

    results = json.loads(search_tasks(status="blocked", project_id=test_project))

    assert results == []
