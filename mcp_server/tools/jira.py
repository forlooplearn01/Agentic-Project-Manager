import base64
import json
import os
from typing import Optional

import requests

from apps.db.db import get_project_jira_key, get_task_jira_key, set_task_jira_key


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_jira_auth():
    # Auth is one global connection for the whole deployment - a single Jira Cloud
    # site and integration user, regardless of how many task-manager projects exist.
    url = os.getenv("JIRA_BASE_URL")
    token = os.getenv("JIRA_API_TOKEN")
    email = os.getenv("JIRA_EMAIL")

    if not all([url, token, email]):
        return None

    return {"url": url, "token": token, "email": email}


def _auth_headers(auth: dict) -> dict:
    # Jira Cloud uses HTTP Basic auth: base64(email:api_token)
    credentials = base64.b64encode(f"{auth['email']}:{auth['token']}".encode()).decode()
    return {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# Priority labels in our system map to Jira's built-in priority names
_PRIORITY_MAP = {
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}


def _find_jira_account_id(auth: dict, query: str) -> Optional[str]:
    # Jira Cloud's issue-create API (v3) only accepts assignee.accountId - the old
    # assignee.name field from Jira Server/Data Center either gets silently ignored
    # or rejected depending on the site, so a display name has to be resolved to a
    # real accountId first via the user-search endpoint (matches displayName and
    # email substrings). Returns None - never raises - so a name that doesn't
    # resolve just leaves the issue unassigned instead of failing the whole create.
    try:
        response = requests.get(
            f"{auth['url']}/rest/api/3/user/search",
            params={"query": query},
            headers=_auth_headers(auth),
            timeout=10,
        )
        response.raise_for_status()
        results = response.json()
        return results[0]["accountId"] if results else None
    except requests.exceptions.RequestException:
        return None


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------

def sync_task_to_jira(
    task_id: str,
    task_text: str,
    project_id: str,
    priority: str = "medium",
    assignee: Optional[str] = None,
) -> str:
    """Push a task to Jira as a new issue. Requires JIRA_BASE_URL, JIRA_API_TOKEN,
    JIRA_EMAIL env vars (auth, global) AND a Jira project key mapped for this specific
    task-manager project (per-project, set via project_integrations - different
    task-manager projects can sync into different Jira projects)."""
    auth = _get_jira_auth()

    # Graceful fallback - return not_configured when auth is absent
    if not auth:
        return json.dumps({
            "status": "not_configured",
            "message": (
                "Jira integration is not configured. "
                "Set JIRA_BASE_URL, JIRA_API_TOKEN, and JIRA_EMAIL in .env to enable."
            ),
            "task_id": task_id,
        })

    jira_project_key = get_project_jira_key(project_id)
    if not jira_project_key:
        return json.dumps({
            "status": "not_configured",
            "message": (
                f"Jira is connected, but project '{project_id}' has no Jira project key "
                "mapped yet - set one in this project's settings to enable syncing."
            ),
            "task_id": task_id,
        })

    # Idempotency guard - a rerun of the pipeline against a still-live project (e.g.
    # the same MoM reprocessed) can re-match this exact task_id and call this tool
    # again. Without checking for a prior sync, that creates a second Jira issue for
    # the same task every time. This does NOT cover a deleted-and-recreated project -
    # those get fresh task_ids with no memory of anything, which is the deliberate
    # "start over" behavior of deleting a project, not a bug.
    existing_key = get_task_jira_key(task_id)
    if existing_key:
        return json.dumps({
            "status": "already_synced",
            "jira_issue_key": existing_key,
            "task_id": task_id,
        })

    # Map internal priority label to Jira's display name; default to Medium for unknown values
    jira_priority = _PRIORITY_MAP.get(priority.lower(), "Medium")

    # Build the Jira issue payload - issuetype Task maps to a standard Jira task
    payload = {
        "fields": {
            "project": {"key": jira_project_key},
            "summary": task_text,
            "issuetype": {"name": "Task"},
            "priority": {"name": jira_priority},
        }
    }

    # Resolve a display name/email to a real Jira accountId - Jira Cloud's v3 API
    # doesn't accept the old assignee.name field. A name that doesn't match any
    # Jira user leaves the issue unassigned rather than failing the whole create.
    assignee_warning = None
    if assignee:
        account_id = _find_jira_account_id(auth, assignee)
        if account_id:
            payload["fields"]["assignee"] = {"accountId": account_id}
        else:
            assignee_warning = f"No Jira user found matching '{assignee}' - issue created unassigned."

    try:
        response = requests.post(
            f"{auth['url']}/rest/api/3/issue",
            json=payload,
            headers=_auth_headers(auth),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        jira_issue_key = data.get("key")

        # Persist the key on the task row so a later sync of this same task_id is
        # recognized as already-synced instead of creating a duplicate issue.
        set_task_jira_key(task_id, jira_issue_key)

        # Return the Jira issue key (e.g. "PROJ-42") and internal task_id for cross-reference
        result = {
            "status": "created",
            "jira_issue_key": jira_issue_key,
            "jira_issue_id": data.get("id"),
            "task_id": task_id,
        }
        if assignee_warning:
            result["assignee_warning"] = assignee_warning
        return json.dumps(result)

    except requests.exceptions.HTTPError as e:
        # Include response body for easier debugging (e.g. field validation errors)
        body = e.response.text[:300] if e.response else ""
        return json.dumps({"error": f"Jira API error {e.response.status_code}: {body}", "task_id": task_id})

    except requests.exceptions.RequestException as e:
        return json.dumps({"error": f"Jira request failed: {e}", "task_id": task_id})
