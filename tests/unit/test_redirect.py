"""The Extractor takes an SOW only: meeting minutes sent to it stop with a pointer to the meetings folder (apps/nodes/redirect_node.py)."""
from apps.nodes.redirect_node import route_after_supervisor, task_redirect_node


def test_minutes_from_the_extractor_are_redirected_and_everything_else_goes_on():
    assert route_after_supervisor({"sow_only": True, "document_type": "mom"}) == "redirect"
    assert route_after_supervisor({"sow_only": True, "document_type": "sow"}) == "extract"
    assert route_after_supervisor({"document_type": "mom"}) == "extract"  # tests, evals and other callers still run minutes


def test_the_redirect_creates_nothing_and_says_where_meetings_go():
    out = task_redirect_node({"project_id": "acme", "final_output": {}})["final_output"]
    assert out["status"] == "redirected" and out["tasks_count"] == 0 and out["tasks"] == []
    assert "projects/acme/meetings/" in out["message"] and "Suggestions" in out["message"]


def test_the_graph_stops_at_the_redirect_for_minutes(monkeypatch):
    from apps.graph import build_graph
    monkeypatch.setattr("apps.supervisor.count_tasks_for_project", lambda project_id: 5)  # a project with a plan: this text is minutes
    state = {"raw_input": "Standup: Ana will finish the search page by Friday.", "project_id": "acme", "document_type": "", "sow_only": True}
    result = build_graph().invoke(state)
    assert result["final_output"]["status"] == "redirected" and result["document_type"] == "mom" and not result.get("tasks")
