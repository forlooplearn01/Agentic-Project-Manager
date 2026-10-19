"""HTTP layer for possible-duplicate handling (status codes and payloads); the merge logic itself
is covered in test_duplicate_handling.py."""
from fastapi.testclient import TestClient

from apps.api import app
from apps.db.db import insert_task, set_possible_duplicate

client = TestClient(app)  # no `with`: startup (warmup, migrations) is not needed here


def _seed(project_id, text, owner=None):
    return insert_task({"task_text": text, "normalized_task_text": text.lower(), "owner": owner}, project_id)


def test_board_endpoint_exposes_the_flag_and_merge_resolves_it(test_project):
    existing = _seed(test_project, "Write unit tests for CSV module")
    flagged = _seed(test_project, "Complete CSV module coding and unit tests", owner="Jordan Mills")
    set_possible_duplicate(flagged, existing, "llm_unsure")

    tasks = client.get(f"/projects/{test_project}/tasks").json()["tasks"]
    by_text = {t["task_text"]: t for t in tasks}
    assert by_text["Complete CSV module coding and unit tests"]["possible_duplicate_of"]["task_id"] == existing

    resp = client.post(f"/tasks/{flagged}/merge", json={"into_task_id": existing})
    assert resp.status_code == 200
    assert resp.json()["filled_fields"] == {"owner": "Jordan Mills"}
    remaining = client.get(f"/projects/{test_project}/tasks").json()["tasks"]
    assert [t["task_text"] for t in remaining] == ["Write unit tests for CSV module"]


def test_not_duplicate_endpoint(test_project):
    existing = _seed(test_project, "Write unit tests for CSV module")
    flagged = _seed(test_project, "Complete CSV module coding and unit tests")
    set_possible_duplicate(flagged, existing, "rejected_high_similarity")
    assert client.post(f"/tasks/{flagged}/not-duplicate").status_code == 200
    assert client.post(f"/tasks/{flagged}/not-duplicate").status_code == 400  # already resolved


def test_error_statuses(test_project):
    a = _seed(test_project, "Task A")
    assert client.post(f"/tasks/{a}/merge", json={"into_task_id": a}).status_code == 400
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/tasks/{a}/merge", json={"into_task_id": missing}).status_code == 404
    assert client.post(f"/tasks/{missing}/not-duplicate").status_code == 404
