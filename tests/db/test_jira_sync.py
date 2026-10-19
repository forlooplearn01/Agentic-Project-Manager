"""Regression tests for two real bugs found in sync_task_to_jira: (1) nothing tracked
whether a task was already synced, so a rerun against a still-live project could create
duplicate Jira issues; (2) the assignee field used Jira Cloud's deprecated `name` key
instead of resolving a real `accountId`, which either silently no-opped or 400'd
depending on the site.

requests is mocked here (not the LLM - this file has nothing to do with the LLM layer)
so these run without live Jira credentials and never create a real external issue."""
import json
from unittest.mock import patch, MagicMock

from apps.db.db import insert_task, set_task_jira_key, set_project_jira_key
from mcp_server.tools.jira import sync_task_to_jira, _find_jira_account_id


def test_already_synced_skips_the_http_call_entirely(test_project):
    # jira_project_key is checked before the idempotency check in the real code, so a
    # project with none configured would short-circuit to not_configured first - set
    # one so this test actually reaches the idempotency branch it's meant to verify.
    set_project_jira_key(test_project, "FLP")
    task_id = insert_task({"task_text": "Some task", "normalized_task_text": "some task"}, test_project)
    set_task_jira_key(task_id, "FLP-999")

    with patch("mcp_server.tools.jira.requests.post") as mock_post:
        raw = sync_task_to_jira(task_id=task_id, task_text="Some task", project_id=test_project)

    result = json.loads(raw)
    assert result == {"status": "already_synced", "jira_issue_key": "FLP-999", "task_id": task_id}
    mock_post.assert_not_called()


def test_accountid_resolves_when_a_matching_user_exists():
    fake_auth = {"url": "https://fake.atlassian.net", "token": "x", "email": "x@example.com"}
    mock_response = MagicMock()
    mock_response.json.return_value = [{"accountId": "712020:abc-123"}]
    mock_response.raise_for_status.return_value = None

    with patch("mcp_server.tools.jira.requests.get", return_value=mock_response) as mock_get:
        account_id = _find_jira_account_id(fake_auth, "Nirmal Babu")

    assert account_id == "712020:abc-123"
    mock_get.assert_called_once()


def test_accountid_returns_none_when_no_user_matches():
    fake_auth = {"url": "https://fake.atlassian.net", "token": "x", "email": "x@example.com"}
    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status.return_value = None

    with patch("mcp_server.tools.jira.requests.get", return_value=mock_response):
        account_id = _find_jira_account_id(fake_auth, "Nobody Special")

    assert account_id is None


def test_created_issue_is_unassigned_not_failed_when_no_user_matches(test_project):
    set_project_jira_key(test_project, "FLP")
    task_id = insert_task({"task_text": "Unassignable task", "normalized_task_text": "unassignable task"}, test_project)

    empty_search = MagicMock()
    empty_search.json.return_value = []
    empty_search.raise_for_status.return_value = None

    created = MagicMock()
    created.json.return_value = {"key": "FLP-1", "id": "10001"}
    created.raise_for_status.return_value = None

    fake_auth = {"url": "https://fake.atlassian.net", "token": "x", "email": "x@example.com"}

    # Patch _get_jira_auth directly rather than os.getenv - os is a shared module
    # singleton, so patching os.getenv would shadow every caller process-wide,
    # including get_connection()'s own DB_HOST/DB_PORT lookups (this broke the DB
    # connection entirely the first time this test was written).
    with patch("mcp_server.tools.jira._get_jira_auth", return_value=fake_auth), \
         patch("mcp_server.tools.jira.requests.get", return_value=empty_search), \
         patch("mcp_server.tools.jira.requests.post", return_value=created):
        raw = sync_task_to_jira(
            task_id=task_id, task_text="Unassignable task", project_id=test_project, assignee="Nobody Special",
        )

    result = json.loads(raw)
    assert result["status"] == "created"
    assert "assignee_warning" in result
