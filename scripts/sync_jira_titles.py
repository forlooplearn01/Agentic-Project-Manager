"""Makes the titles of already-created Jira issues match the app's task names (used after a rename, e.g. the Meridian -> Calderwick change).
Only issues whose current title differs from the app's are edited; each edit is printed. Needs Jira to be reachable.

    docker compose exec api python -m scripts.sync_jira_titles forloop"""
import sys

import requests

from apps.db.db import get_connection
from mcp_server.tools.jira import _auth_headers, _get_jira_auth

project = sys.argv[1] if len(sys.argv) > 1 else "forloop"
auth = _get_jira_auth()
if not auth:
    raise SystemExit("Jira is not configured in this container.")
conn = get_connection()
cur = conn.cursor()
cur.execute("SELECT jira_issue_key, task_text FROM tasks WHERE project_id = %s AND jira_issue_key IS NOT NULL AND is_active ORDER BY 1", (project,))
changed = 0
for key, title in cur.fetchall():
    url = f"{auth['url']}/rest/api/3/issue/{key}"
    got = requests.get(url, headers=_auth_headers(auth), params={"fields": "summary"}, timeout=20)
    if got.status_code != 200:
        raise SystemExit(f"{key}: cannot read it ({got.status_code} {got.text[:160]})")
    current = got.json()["fields"]["summary"]
    if current == title:
        continue
    put = requests.put(url, json={"fields": {"summary": title}}, headers=_auth_headers(auth), timeout=20)
    print(f"{key} {put.status_code}: {current!r} -> {title!r}")
    changed += put.status_code == 204
print(f"{changed} title(s) updated")
