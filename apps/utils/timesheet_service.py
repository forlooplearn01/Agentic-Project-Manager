"""Reading a timesheet file into a reviewable import, and applying one. Used by the Hours API (upload and review) and by the folder
watcher (apps/utils/timesheet_watcher.py), so both go through exactly the same steps: parse, match people and tasks, then apply with
replace-not-add semantics. No HTTP in here."""
import json
from datetime import date
from typing import Optional

from apps.db.db import list_project_people
from apps.db.hours_db import (
    apply_timesheet_import, create_import_record, fetch_active_tasks_for_hours, fetch_time_entries, get_hours_settings, get_import,
)
from apps.utils import hours_import

MAX_IMPORT_ROWS = 5000


class BadChoice(ValueError):
    """A correction the user made that cannot be applied (a task that is not in this project)."""


def preview_timesheet(project_id: str, filename: str, content: bytes, mapping_json: Optional[str] = None, source: str = "upload",
                      file_hash: str = None) -> dict:
    """Parse and match a timesheet and STORE it as an import awaiting a decision; nothing is written to the hours yet."""
    people = [p["name"] for p in list_project_people(project_id)]
    settings = get_hours_settings(project_id)
    mapping = json.loads(mapping_json) if mapping_json else None
    parsed = hours_import.parse_timesheet(filename, content, settings["kickoff_date"] or date.today(), mapping,
                                          llm_mapper=hours_import.propose_mapping_with_llm, week_start_day=settings["week_start_day"])
    rows = parsed["rows"]
    if len(rows) > MAX_IMPORT_ROWS:
        raise hours_import.HoursImportError(f"That file has more than {MAX_IMPORT_ROWS} rows of hours. Split it by month.")
    tasks = fetch_active_tasks_for_hours(project_id)
    for r in rows:
        r["person"] = hours_import.resolve_person(r["person_raw"], people)
    pairs = sorted({(r["label"], r["person"]) for r in rows if r["label"]}, key=lambda x: (x[0], x[1] or ""))
    matches = hours_import.propose_task_matches([{"label": l, "person": p} for l, p in pairs], tasks)
    by_pair = dict(zip(pairs, matches))
    for idx, r in enumerate(rows):
        m = by_pair.get((r["label"], r["person"])) or {"task_id": None, "task_text": None, "score": 0.0, "match": "other"}
        r.update(idx=idx, task_id=m["task_id"], task_text=m["task_text"], match=m["match"], score=m["score"],
                 week_start=r["week_start"].isoformat())
    existing = {(e["person"].lower(), e["week_start"].isoformat(), e["task_id"]) for e in fetch_time_entries(project_id)}
    new_keys = {(r["person"].lower(), r["week_start"], r["task_id"]) for r in rows if r["person"]}
    by_match = {}
    for r in rows:
        by_match[r["match"]] = by_match.get(r["match"], 0) + 1
    summary = {"rows": len(rows), "hours": round(sum(r["hours"] for r in rows), 2),
               "unknown_people": sorted({r["person_raw"] for r in rows if not r["person"]}), "by_match": by_match,
               "will_replace": len(existing & new_keys),
               "weeks": [min(r["week_start"] for r in rows), max(r["week_start"] for r in rows)]}
    stored = {"rows": rows, "summary": summary, "mapping": parsed["mapping"], "warnings": parsed["warnings"], "layout": parsed["layout"]}
    import_id = create_import_record(project_id, "timesheet", filename, parsed["layout"], stored, source=source, file_hash=file_hash)
    return {"import_id": import_id, "kind": "timesheet", "layout": parsed["layout"], "mapping": parsed["mapping"],
            "warnings": parsed["warnings"], "rows": rows, "summary": summary, "people": people,
            "tasks": [{"task_id": t["task_id"], "task_text": t["task_text"]} for t in tasks]}


def review_payload(import_id: str) -> dict:
    """The stored preview of a timesheet import, in the shape the review screen takes, with the CURRENT people and tasks."""
    imp = get_import(import_id)
    if not imp or imp["kind"] != "timesheet":
        raise LookupError("No such timesheet import.")
    p = imp["payload"] or {}
    if not p.get("rows"):
        raise LookupError("This file could not be read, so there is nothing to review. See the reason and fix the file.")
    project_id = imp["project_id"]
    return {"import_id": import_id, "kind": "timesheet", "layout": imp["layout"], "mapping": p.get("mapping"), "warnings": p.get("warnings", []),
            "rows": p["rows"], "summary": p["summary"], "reason": imp.get("reason"), "status": imp["status"],
            "people": [x["name"] for x in list_project_people(project_id)],
            "tasks": [{"task_id": t["task_id"], "task_text": t["task_text"]} for t in fetch_active_tasks_for_hours(project_id)]}


def confirm_timesheet(import_id: str, decisions: dict = None) -> dict:
    """Apply a stored timesheet import. decisions: {row idx: {"skip": bool, "person": str|None, "has_task": bool, "task_id": str|None}}
    overrides the proposed person / task for that row (none = apply it exactly as proposed). Same-key entries are REPLACED."""
    imp = get_import(import_id)
    if not imp:
        raise LookupError("No such import.")
    decisions = decisions or {}
    project_id, payload = imp["project_id"], imp["payload"]
    people = [p["name"] for p in list_project_people(project_id)]
    valid_tasks = {t["task_id"] for t in fetch_active_tasks_for_hours(project_id)}
    entries, skipped = [], 0
    for r in payload["rows"]:
        d = decisions.get(r["idx"]) or {}
        if d.get("skip"):
            skipped += 1
            continue
        person = hours_import.resolve_person(d["person"], people) if d.get("person") else r["person"]
        if not person:
            skipped += 1
            continue
        task_id = d["task_id"] if d.get("has_task") else r["task_id"]
        if task_id and task_id not in valid_tasks:
            raise BadChoice("One of the chosen tasks is not in this project.")
        entries.append({"person": person, "week_start": date.fromisoformat(r["week_start"]), "task_id": task_id,
                        "hours": r["hours"], "source_ref": r["source_ref"]})
    result = apply_timesheet_import(import_id, entries)
    result["skipped_rows"] = skipped
    return result
