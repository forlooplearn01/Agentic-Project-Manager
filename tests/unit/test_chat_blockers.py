import json

from apps.agent import tool_registry as tr

BLOCKED = [{"task_id": "1", "task_text": "Integrate KYC", "status": "blocked"}, {"task_id": "2", "task_text": "Other", "status": "in_progress"}]
ISSUES = [{"review_state": "confirmed", "status": "mitigating", "title": "Vendor has not granted access", "description": "d", "owner": "Ram",
           "probability": "high", "impact": "high"},
          {"review_state": "confirmed", "status": "closed", "title": "Old", "description": "x", "owner": "n", "probability": "low", "impact": "low"},
          {"review_state": "suggested", "status": "open", "title": "Not reviewed", "description": "x", "owner": "n", "probability": "low", "impact": "low"}]


def _patch(monkeypatch, tasks):
    monkeypatch.setattr(tr, "search_tasks", lambda **kw: json.dumps(tasks))
    monkeypatch.setattr(tr, "_known_project_ids", lambda: ["halvane"])
    monkeypatch.setattr(tr, "list_raid_items", lambda pid, item_type=None, status=None: ISSUES)


def test_blocked_tasks_come_with_the_live_issues_that_explain_them(monkeypatch):
    _patch(monkeypatch, BLOCKED)
    r = tr._search_tasks_with_blockers(status="blocked", project_id="Halvane")  # the model's capital H still finds the project
    assert r["tasks"] == BLOCKED
    assert [i["title"] for i in r["live_issues_in_the_risk_register"]] == ["Vendor has not granted access"]  # mitigating counts; closed and unreviewed do not


def test_a_search_without_blocked_tasks_is_returned_as_it_was(monkeypatch):
    _patch(monkeypatch, [BLOCKED[1]])
    assert json.loads(tr._search_tasks_with_blockers(query="Other", project_id="halvane")) == [BLOCKED[1]]
