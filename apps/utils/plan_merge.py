"""From approval to the plan file: what happens to a suggestion after the PMO says yes.

  approve -> merge_pending: write the change into the project's plan file (apps/utils/plan_file.py), then read the file again
          -> confirm_merged: the new read shows the change, so the suggestion is "confirmed" (and a new task is sent to Jira, if the project
             has a Jira key; that happens only here, never before approval).

Safeguards, in order (DECISIONS.md "Plan and meetings by folder"): the file must not be open in Excel (else "waiting", retried every scan);
the app must have read the file's current contents first (else it reads them now, so nobody's edits are overwritten); the file is copied to
plan/versions/ before it is written. A project with no plan file at all gets the approved changes straight into the app's task list."""
import json
import os
from pathlib import Path

from apps.db.db import ensure_person_placeholder, get_connection, insert_task, update_task_field
from apps.db.hours_db import set_task_hours
from apps.db.plan_sync import latest_folder_file
from apps.db.suggestions_db import get_suggestion, list_suggestions, set_state
from apps.logger import get_logger
from apps.utils import plan_file as pf

logger = get_logger("PLAN_MERGE")
PLAN = "plan"


def jira_sync(task_id: str, task_text: str, project_id: str, owner: str = None) -> dict:
    """Send an approved, merged new task to Jira (a no-op when Jira or the project's key is not set up). Returns the tool's own answer."""
    if os.getenv("PLAN_JIRA_SYNC", "1") == "0":  # e.g. a demo on a project that has a real Jira key
        return {"status": "disabled", "message": "PLAN_JIRA_SYNC=0: approved tasks are not sent to Jira."}
    from mcp_server.tools.jira import sync_task_to_jira
    try:
        return json.loads(sync_task_to_jira(task_id=task_id, task_text=task_text, project_id=project_id, assignee=owner))
    except Exception as e:  # approval must never fail because Jira is down
        logger.warning(f"Jira sync failed for '{task_text}': {e}")
        return {"status": "error", "message": str(e)}


def plan_dir(project_id: str, root=None) -> Path:
    from apps.utils import timesheet_watcher as tw
    return Path(root or tw.ROOT) / project_id / PLAN


# ---------------------------------------------------------------- approve / reject

def approve(project_id: str, suggestion_ids: list, root=None) -> dict:
    """Approve suggestions (those still waiting for a decision, or ones that went missing or were blocked and are being re-applied), then merge."""
    done = []
    for sid in suggestion_ids:
        s = get_suggestion(sid)
        if s and s["project_id"] == project_id and s["state"] in ("suggested", "missing", "blocked"):
            set_state(sid, "approved", decided_at=True)
            done.append(sid)
    result = merge_pending(project_id, root) if done else {"merged": 0}
    return {"approved": len(done), **result}


def reject(project_id: str, suggestion_ids: list) -> int:
    n = 0
    for sid in suggestion_ids:
        s = get_suggestion(sid)
        if s and s["project_id"] == project_id and s["state"] == "suggested":
            set_state(sid, "rejected", decided_at=True)
            n += 1
    return n


# ---------------------------------------------------------------- merging

def _task_ref(task_id):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT plan_ref FROM tasks WHERE task_id = %s", (task_id,))
        row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def _ops(items: list) -> list:
    ops = []
    for s in items:
        if s["kind"] == "new_task":
            f = s["fields"]
            ops.append({"op": "add", "task": s["task_text"], "owner": f.get("owner"), "status": f.get("status"), "finish_week": f.get("finish_week"),
                        "note": f"Added from {s['source_name'] or 'a meeting'} (approved by the PMO)"})
        else:
            ops.append({"op": "change", "ref": _task_ref(s["task_id"]), "task": s["task_text"], "changes": {k: v["to"] for k, v in s["changes"].items()}})
    return ops


def _wait(items, state, reason):
    for s in items:
        set_state(s["suggestion_id"], state, reason)


def merge_pending(project_id: str, root=None) -> dict:
    """Write every approved (or waiting) suggestion into the plan file. Safe to call at any time; each call does what it can."""
    from apps.utils import project_folders
    items = list_suggestions(project_id, states=("approved", "waiting"))
    if not items:
        return {"merged": 0}
    # a change to a task that was never in the plan file has no row to change: it is made in the app's task list
    local = [s for s in items if s["kind"] == "change" and not _task_ref(s["task_id"])]
    items = [s for s in items if s not in local]
    applied = _apply_to_app(project_id, local, "This task is not in the plan file (it only exists in the app), so the change was made in the app's task list.") if local else 0
    if not items:
        return {"merged": 0, "applied_to_app": applied}
    directory = plan_dir(project_id, root)
    path = pf.current_plan_file(directory)
    if path is None:
        return {"merged": 0, "applied_to_app": applied + _apply_to_app(project_id, items, "There is no plan file for this project, so it went into the app's task list.")}
    if pf.is_open_elsewhere(path):
        _wait(items, "waiting", f"The plan ({path.name}) is open in Excel. It will be updated when it is closed.")
        return {"merged": 0, "waiting": len(items), "applied_to_app": applied}
    status = project_folders.process_plan_file(project_id, path, root=root)  # the file's current contents, read before anything is written
    if status.get("status") == "held":
        _wait(items, "waiting", f"The plan file needs attention first: {status.get('reason')}")
        return {"merged": 0, "waiting": len(items), "applied_to_app": applied}
    record = latest_folder_file(project_id, "plan", path.name)
    layout = ((record or {}).get("payload") or {}).get("layout") or {}
    if not record or record["status"] != "applied":
        _wait(items, "waiting", "The plan file has not been read yet; it will be updated on the next check.")
        return {"merged": 0, "waiting": len(items), "applied_to_app": applied}
    try:
        result = pf.merge_into_workbook(path, layout, _ops(items), backup_dir=directory / pf.VERSIONS)
    except pf.Blocked as e:
        _wait(items, "blocked", str(e))
        logger.warning(f"{project_id}: plan not updated: {e}")
        return {"merged": 0, "blocked": len(items), "applied_to_app": applied}
    for entry in result["added"]:
        set_state(items[entry["index"]]["suggestion_id"], "merged", None, merged_at=True, plan_ref=entry["ref"])
    for i in result["changed"]:
        set_state(items[i]["suggestion_id"], "merged", None, merged_at=True)
    for entry in result["skipped"]:
        set_state(items[entry["index"]]["suggestion_id"], "blocked", entry["reason"])
    if result["added"] or result["changed"]:
        project_folders.process_plan_file(project_id, path, root=root)  # read it back at once: that confirms what was written
    return {"merged": len(result["added"]) + len(result["changed"]), "blocked": len(result["skipped"]), "applied_to_app": applied}


def _apply_to_app(project_id: str, items: list, why: str) -> int:
    """Approved changes that go straight into the app's task list (a project with no plan file, or a task that was never in it)."""
    n = 0
    for s in items:
        if s["kind"] == "new_task":
            f = s["fields"]
            task_id = str(insert_task({"task_text": s["task_text"], "normalized_task_text": "".join(c for c in s["task_text"].lower() if c.isalnum() or c.isspace()),
                                       "owner": f.get("owner"), "status": f.get("status") or "not_started", "deadline": f.get("deadline")}, project_id))
            if f.get("owner"):
                ensure_person_placeholder(project_id, f["owner"])
            if f.get("finish_week"):
                set_task_hours(task_id, _log=False, plan_finish_week=f["finish_week"])
            jira = jira_sync(task_id, s["task_text"], project_id, f.get("owner"))
            set_state(s["suggestion_id"], "applied", f"{why} The task was added to the app's task list.",
                      task_id=task_id, confirmed_at=True, jira_key=jira.get("jira_issue_key"))
        else:
            for field, ch in s["changes"].items():
                if field == "finish_week":
                    set_task_hours(s["task_id"], _source="suggestion", plan_finish_week=ch["to"])
                elif field in ("owner", "status"):
                    update_task_field(s["task_id"], field, ch["to"])
                    if field == "owner":
                        ensure_person_placeholder(project_id, ch["to"])
            set_state(s["suggestion_id"], "applied", why, confirmed_at=True)
        n += 1
    return n


# ---------------------------------------------------------------- confirming

def _task_by_ref_or_name(project_id: str, ref, name: str):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""SELECT task_id, owner, status, plan_finish_week, task_text FROM tasks WHERE project_id = %s AND is_active
                       AND ((%s::text IS NOT NULL AND lower(plan_ref) = lower(%s)) OR lower(task_text) = lower(%s))
                       ORDER BY (lower(plan_ref) = lower(%s)) DESC NULLS LAST LIMIT 1""", (project_id, ref, ref, name, ref))
        row = cur.fetchone()
    conn.close()
    return dict(zip(("task_id", "owner", "status", "plan_finish_week", "task_text"), row)) if row else None


def confirm_merged(project_id: str) -> dict:
    """After a plan file has been read: suggestions that were written into it are confirmed when the plan now shows them, and a new task is
    sent to Jira; ones the plan does not show become 'missing'."""
    confirmed = missing = 0
    for s in list_suggestions(project_id, states=("merged",)):
        if s["kind"] == "new_task":
            task = _task_by_ref_or_name(project_id, s["plan_ref"], s["task_text"])
            ok = task is not None
        else:
            task = _task_by_ref_or_name(project_id, _task_ref(s["task_id"]), s["task_text"])
            have = {"owner": (task or {}).get("owner"), "status": (task or {}).get("status"), "finish_week": (task or {}).get("plan_finish_week")}
            ok = task is not None and all(str(have.get(k) or "").lower() == str(v["to"]).lower() for k, v in s["changes"].items())
        if not ok:
            set_state(s["suggestion_id"], "missing", "The change is not in the latest version of the plan file. Approve it again to re-apply.")
            missing += 1
            continue
        jira = {}
        if s["kind"] == "new_task":
            jira = jira_sync(str(task["task_id"]), task["task_text"], project_id, task.get("owner"))
        set_state(s["suggestion_id"], "confirmed", None, task_id=str(task["task_id"]), confirmed_at=True, jira_key=jira.get("jira_issue_key") or jira.get("jira_key"))
        confirmed += 1
    return {"confirmed": confirmed, "missing": missing}
