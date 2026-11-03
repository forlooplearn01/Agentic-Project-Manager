"""The plan and meeting folders of every project, next to the timesheet one (apps/utils/timesheet_watcher.py runs all of them).

    projects/<id>/plan/                the project plan (Excel). It stays here and is edited in place; it is read again whenever it changes.
    projects/<id>/plan/versions/       a copy of the plan before the app wrote an approved change into it
    projects/<id>/meetings/            a meeting transcript is dropped here: it becomes suggestions for the PMO ...
    projects/<id>/meetings/processed/  ... and moves here, or to needs-review/ with the reason when it could not be read

The project is decided by the folder. Nothing a meeting says reaches the plan without the PMO's approval."""
import hashlib
from pathlib import Path

from apps.db.plan_sync import (
    apply_plan_version, find_applied_meeting_by_hash, latest_folder_file, record_folder_file, update_folder_file,
)
from apps.db.suggestions_db import project_ids_with_work
from apps.logger import get_logger
from apps.utils import hours_import, meeting_followup, meeting_suggest, plan_file as pf
from apps.utils import timesheet_watcher as tw

logger = get_logger("PROJECT_FOLDERS")
MEETINGS = "meetings"


def folder(project_id: str, sub: str, root=None) -> Path:
    return Path(root or tw.ROOT) / project_id / sub


# ---------------------------------------------------------------- the plan

def process_plan_file(project_id: str, path: Path, force: bool = False, root=None) -> dict:
    """Read a plan file if it changed since it was last looked at, and bring the project's tasks into line with it.
    -> {"status": "unchanged" | "applied" | "held" | "failed", ...}. A held file (unreadable, or one that drops most of the plan) is not applied
    until a person confirms it."""
    try:
        content = path.read_bytes()
    except OSError as e:
        return {"status": "skipped", "reason": str(e)}
    file_hash = hashlib.sha256(content).hexdigest()
    last = latest_folder_file(project_id, "plan", path.name)
    if last and last["file_hash"] == file_hash and not force:
        return {"status": "unchanged"}
    if len(content) > hours_import.MAX_BYTES:
        record_folder_file(project_id, "plan", path.name, file_hash, "held", "The file is larger than 5 MB.")
        return {"status": "held", "reason": "The file is larger than 5 MB."}
    try:
        parsed = hours_import.parse_plan(path.name, content)
    except hours_import.HoursImportError as e:
        record_folder_file(project_id, "plan", path.name, file_hash, "held", str(e))
        logger.info(f"{project_id}/{path.name}: plan not read ({e})")
        return {"status": "held", "reason": str(e)}
    try:
        result = apply_plan_version(project_id, parsed, path.name, force=force)
    except Exception as e:  # a bug or a bad file must never stop the loop
        logger.exception(f"{project_id}/{path.name}: unexpected error while applying the plan")
        record_folder_file(project_id, "plan", path.name, file_hash, "failed", f"Unexpected error while applying the plan: {e}")
        return {"status": "failed", "reason": str(e)}
    layout_payload = {"layout": parsed["layout"], "warnings": parsed["warnings"]}
    if result["status"] == "held":
        record_folder_file(project_id, "plan", path.name, file_hash, "held", result["reason"], payload=layout_payload)
        logger.info(f"{project_id}/{path.name}: held ({result['reason']})")
        return {"status": "held", "reason": result["reason"]}
    summary = {k: result[k] for k in ("tasks", "added", "changed", "removed", "baseline_set", "settings_changed")}
    record_folder_file(project_id, "plan", path.name, file_hash, "applied", summary=summary, payload=layout_payload)
    from apps.utils.plan_merge import confirm_merged
    confirm_merged(project_id)
    logger.info(f"{project_id}/{path.name}: plan read ({summary['tasks']} tasks, {summary['added']} added, {summary['changed']} changed, {summary['removed']} removed)")
    return {"status": "applied", **summary}


def apply_held_plan(project_id: str, file_id: str, root=None) -> dict:
    """The person confirmed a held plan version: apply it as it is now."""
    from apps.db.plan_sync import get_folder_file
    rec = get_folder_file(file_id)
    if not rec or rec["project_id"] != project_id or rec["kind"] != "plan" or rec["status"] != "held":
        raise LookupError("There is no held plan file with that id.")
    path = folder(project_id, "plan", root) / rec["filename"]
    if not path.is_file():
        raise LookupError("The file is no longer in the plan folder.")
    return process_plan_file(project_id, path, force=True, root=root)


# ---------------------------------------------------------------- meetings

def process_meeting_file(project_id: str, path: Path, root=None) -> dict:
    inbox = folder(project_id, MEETINGS, root)
    name = path.name
    try:
        size = path.stat().st_size
        content = b"" if size > meeting_suggest.MAX_TRANSCRIPT_BYTES else path.read_bytes()
    except OSError as e:
        return {"project": project_id, "file": name, "status": "skipped", "reason": str(e)}
    file_hash = hashlib.sha256(content).hexdigest() if content else None
    earlier = find_applied_meeting_by_hash(project_id, file_hash) if file_hash else None
    if earlier:
        record_folder_file(project_id, "meeting", name, file_hash, "duplicate", f"Same contents as '{earlier['filename']}', which was already read.",
                           payload={"folder_file": tw._move(path, inbox / tw.PROCESSED)})
        return {"project": project_id, "file": name, "status": "duplicate"}
    try:
        if not content:
            raise hours_import.HoursImportError("The transcript is larger than 2 MB.")
        text = meeting_suggest.read_transcript(name, content)
    except hours_import.HoursImportError as e:
        record_folder_file(project_id, "meeting", name, file_hash, "held", str(e), payload={"folder_file": tw._move(path, inbox / tw.NEEDS_REVIEW)})
        return {"project": project_id, "file": name, "status": "held", "reason": str(e)}
    file_id = record_folder_file(project_id, "meeting", name, file_hash, "applied", summary={"state": "reading"})
    try:
        result = meeting_suggest.suggest_from_text(project_id, text, name, file_id)
    except Exception as e:
        logger.exception(f"{project_id}/{name}: unexpected error while reading the meeting")
        update_folder_file(file_id, status="failed", reason=f"Unexpected error while reading the transcript: {e}",
                           summary_extra={"folder_file": tw._move(path, inbox / tw.NEEDS_REVIEW)})
        return {"project": project_id, "file": name, "status": "failed", "reason": str(e)}
    followup = meeting_followup.propose_followup(project_id, text, name)  # a meeting the transcript asks for is proposed too, and waits for approval
    update_folder_file(file_id, summary_extra={**result, **followup, "state": "done", "folder_file": tw._move(path, inbox / tw.PROCESSED)})
    return {"project": project_id, "file": name, "status": "applied", **result, **followup}


# ---------------------------------------------------------------- the loop

def scan_all(root=None, project_ids: list = None, settle_seconds: float = None, recheck_seconds: float = 0.5) -> list:
    """One pass over the plan and meeting folders, then the merge of anything approved. The timesheet scan does the folder creation."""
    from apps.db.hours_db import list_project_ids
    from apps.utils.plan_merge import merge_pending
    root = Path(root or tw.ROOT)
    known = project_ids if project_ids is not None else list_project_ids()
    settle = tw.SETTLE_SECONDS if settle_seconds is None else settle_seconds
    results = []
    for pid in known:
        if not tw._safe_id(pid):
            continue
        for path in pf.plan_files(folder(pid, "plan", root)):  # oldest first, so the newest is applied last
            if tw._settled(path, settle, recheck_seconds):
                try:
                    r = process_plan_file(pid, path, root=root)
                    if r["status"] != "unchanged":
                        results.append({"project": pid, "file": path.name, "kind": "plan", **r})
                except Exception:
                    logger.exception(f"{pid}/{path.name}: plan could not be processed")
        inbox = folder(pid, MEETINGS, root)
        try:
            files = sorted(p for p in inbox.iterdir() if p.is_file() and not p.is_symlink() and not tw._ignorable(p.name))
        except OSError:
            files = []
        for path in files:
            key = (str(path), path.stat().st_mtime_ns if path.exists() else 0)
            if key in tw._stuck or not tw._settled(path, settle, recheck_seconds):
                continue
            try:
                results.append({"kind": "meeting", **process_meeting_file(pid, path, root=root)})
            except Exception as e:
                logger.exception(f"{pid}/{path.name}: meeting could not be processed")
                tw._stuck.add(key)
                results.append({"kind": "meeting", "project": pid, "file": path.name, "status": "failed", "reason": str(e)})
    for pid in project_ids_with_work():  # approved changes that were waiting (the file was open) or not yet written
        if pid in known:
            try:
                merge_pending(pid, root)
            except Exception:
                logger.exception(f"{pid}: merging approved changes failed; trying again next time")
    return results
