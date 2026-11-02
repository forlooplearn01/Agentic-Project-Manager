"""Automatic timesheet pickup: watch a folder per project and import any timesheet that lands in it.

    projects/<project-id>/timesheets/              drop a file here
    projects/<project-id>/timesheets/processed/    ... it moves here once its hours are saved
    projects/<project-id>/timesheets/needs-review/ ... or here, with the reason, when a person has to look first

The plan/ and meetings/ folders are read by apps/utils/project_folders.py, which this loop also runs.

The PROJECT is decided by the folder a file sits in, never by what is inside it, and the importer is only ever given that project's people
and tasks, so a file cannot touch another project. A file is applied by itself only when ALL of these hold (DECISIONS.md "Timesheet folder"):
  1. every person in it is on the project's people list;
  2. its file name starts with the project id (and not with another, longer project id);
  3. it is not clearly about different tasks (task-named lines exist but none match a task of this project);
  4. every row could be read.
Otherwise it is HELD in needs-review/ with the reason and nothing is written; a person can review and approve it in the Hours page.
Task names the importer only GUESSED are applied and listed as "check these matches" so one click can move them.
This is an automation, not an LLM agent: the model is only used inside the importer to match task names."""
import hashlib
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from apps.db.hours_db import (
    create_import_record, find_applied_import_by_hash, get_import, list_project_ids, update_import,
)
from apps.logger import get_logger
from apps.utils import hours_import, timesheet_service

logger = get_logger("TIMESHEET_WATCHER")

ROOT = Path(os.getenv("PROJECTS_DIR") or Path(__file__).resolve().parents[2] / "projects")
POLL_SECONDS = float(os.getenv("TIMESHEET_POLL_SECONDS", "10"))
SETTLE_SECONDS = float(os.getenv("TIMESHEET_SETTLE_SECONDS", "2"))  # a file must not have been touched for this long
PROCESSED, NEEDS_REVIEW, ARCHIVE = "processed", "needs-review", "_archive"
TIMESHEETS = "timesheets"
PROJECT_SUBFOLDERS = ("plan", "meetings", "timesheets")  # the folders every project gets
SUBFOLDER_TREE = ("plan/versions", "meetings/processed", "meetings/needs-review", "timesheets/processed", "timesheets/needs-review")

# Shown by the Hours page; the loop below runs in the API process, so this is the live state.
STATUS = {"enabled": False, "root": str(ROOT), "poll_seconds": POLL_SECONDS, "last_checked_at": None}
_started = False
_stuck: set = set()  # files that could not be moved away (permissions): skipped so one cannot be re-imported every cycle


# ---------------------------------------------------------------- folders

def _safe_id(project_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+", project_id or "")) and not project_id.startswith(".")


def inbox_dir(project_id: str, root=None) -> Path:
    """Where a project's timesheets are dropped."""
    return Path(root or ROOT) / project_id / TIMESHEETS


def ensure_folders(project_ids: list, root=None) -> None:
    root = Path(root or ROOT)
    root.mkdir(parents=True, exist_ok=True)
    for pid in project_ids:
        if _safe_id(pid):
            for sub in SUBFOLDER_TREE:
                (root / pid / sub).mkdir(parents=True, exist_ok=True)


def _ignorable(name: str) -> bool:
    low = name.lower()
    return name.startswith((".", "~$")) or low.endswith((".tmp", ".part", ".crdownload", ".download", ".swp"))


def _move(path: Path, dest_dir: Path) -> str:
    """Move a file into dest_dir (never overwriting) and return where it went, relative to the project folder."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / path.name
    if target.exists():
        target = dest_dir / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}"
    shutil.move(str(path), str(target))
    return f"{dest_dir.name}/{target.name}"


def archive_project_folder(project_id: str, root=None) -> int:
    """Move every file under a project's plan, meetings and timesheets folders (inboxes, processed, needs-review, versions) into
    projects/_archive/<project>-<time>/<folder>/. Used by the demo reset so each run starts with empty folders. Returns how many files moved."""
    root = Path(root or ROOT)
    if not _safe_id(project_id):
        return 0
    dest = root / ARCHIVE / f"{project_id}-{datetime.now():%Y%m%d-%H%M%S}"
    moved = 0
    for sub in PROJECT_SUBFOLDERS:
        base = root / project_id / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.is_symlink() and not path.name.startswith("."):
                target_dir = dest / sub
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / path.name
                if target.exists():
                    target = target_dir / f"{path.parent.name}-{path.name}"
                shutil.move(str(path), str(target))
                moved += 1
    return moved


# ---------------------------------------------------------------- the rules

def _name_rule(project_id: str, filename: str, all_project_ids: list):
    name, pid = filename.lower(), project_id.lower()
    if not name.startswith(pid):
        return f"The file name must start with the project id '{project_id}'."
    longer = [p for p in all_project_ids if len(p) > len(project_id) and p.lower() != pid and name.startswith(p.lower())]
    if longer:
        return f"The file name starts with another project's id '{longer[0]}'."
    return None


def _data_rules(preview: dict) -> list:
    reasons = []
    unknown = preview["summary"]["unknown_people"]
    if unknown:
        reasons.append(f"Not on this project's people list: {', '.join(unknown)}.")
    skipped = [w for w in preview["warnings"] if "skipped" in w]
    if skipped:
        reasons.append(f"{len(skipped)} row(s) could not be read ({skipped[0]}).")
    named = [r for r in preview["rows"] if r["label"] and r["match"] != "other"]  # task-named lines (not plain meetings / admin)
    if len(named) >= 3 and not any(r["match"] in ("exact", "suggested", "llm") for r in named):
        reasons.append("None of the task names in this file match a task in this project.")
    return reasons


def _to_check(rows: list) -> list:
    """Lines whose task the importer only guessed (or could not find): applied, and listed so a person can move them with one click."""
    groups = {}
    for r in rows:
        if not r["label"] or not r["person"] or r["match"] not in ("suggested", "llm", "none"):
            continue
        g = groups.setdefault((r["label"], r["person"]), {"label": r["label"], "person": r["person"], "hours": 0.0, "task_id": r["task_id"],
                                                          "task_text": r["task_text"], "match": r["match"]})
        g["hours"] = round(g["hours"] + r["hours"], 2)
    return sorted(groups.values(), key=lambda g: (g["label"].lower(), g["person"].lower()))


# ---------------------------------------------------------------- one file

def process_file(project_id: str, path: Path, all_project_ids: list, root=None) -> dict:
    inbox = inbox_dir(project_id, root)
    name = path.name
    out = {"project": project_id, "file": name}
    try:
        size = path.stat().st_size
        content = b"" if size > hours_import.MAX_BYTES else path.read_bytes()
    except OSError as e:  # being written or locked: leave it, the next pass tries again
        logger.warning(f"{project_id}/{name}: cannot be read yet ({e})")
        return {**out, "status": "skipped", "reason": str(e)}
    file_hash = hashlib.sha256(content).hexdigest() if content else None

    earlier = find_applied_import_by_hash(project_id, file_hash) if file_hash else None
    if earlier:  # the same bytes were already applied: record it, never apply twice
        import_id = create_import_record(project_id, "timesheet", name, None, {}, source="folder", file_hash=file_hash, status="duplicate",
                                         reason=f"Same contents as '{earlier['filename']}', which was already imported.")
        update_import(import_id, payload_extra={"folder_file": _move(path, inbox / PROCESSED)})
        return {**out, "status": "duplicate", "import_id": import_id}

    reasons, preview = [], None
    issue = _name_rule(project_id, name, all_project_ids)
    if issue:
        reasons.append(issue)
    if size > hours_import.MAX_BYTES:
        reasons.append("The file is larger than 5 MB.")
    else:
        try:
            preview = timesheet_service.preview_timesheet(project_id, name, content, source="folder", file_hash=file_hash)
        except hours_import.HoursImportError as e:
            reasons.append(str(e))
        except Exception as e:  # a bug or a bad file must never stop the loop
            logger.exception(f"{project_id}/{name}: unexpected error while reading")
            import_id = create_import_record(project_id, "timesheet", name, None, {}, source="folder", file_hash=file_hash, status="failed",
                                             reason=f"Unexpected error while reading the file: {e}")
            update_import(import_id, payload_extra={"folder_file": _move(path, inbox / NEEDS_REVIEW)})
            return {**out, "status": "failed", "import_id": import_id, "reason": str(e)}
    if preview:
        reasons += _data_rules(preview)

    if reasons:
        reason = " ".join(reasons)
        if preview:
            import_id = preview["import_id"]
            update_import(import_id, status="held", reason=reason)
        else:
            import_id = create_import_record(project_id, "timesheet", name, None, {}, source="folder", file_hash=file_hash, status="held", reason=reason)
        update_import(import_id, payload_extra={"folder_file": _move(path, inbox / NEEDS_REVIEW)})
        logger.info(f"{project_id}/{name}: held for review ({reason})")
        return {**out, "status": "held", "import_id": import_id, "reason": reason}

    import_id = preview["import_id"]
    try:
        result = timesheet_service.confirm_timesheet(import_id)
    except Exception as e:
        logger.exception(f"{project_id}/{name}: unexpected error while applying")
        update_import(import_id, status="failed", reason=f"Unexpected error while applying the hours: {e}")
        update_import(import_id, payload_extra={"folder_file": _move(path, inbox / NEEDS_REVIEW)})
        return {**out, "status": "failed", "import_id": import_id, "reason": str(e)}
    to_check = _to_check(preview["rows"])
    update_import(import_id, summary_extra={"to_check": to_check, "result": result})
    update_import(import_id, payload_extra={"folder_file": _move(path, inbox / PROCESSED)})
    logger.info(f"{project_id}/{name}: applied {result['hours_total']} h in {result['entries_written']} entries ({len(to_check)} to check)")
    return {**out, "status": "applied", "import_id": import_id, "hours": result["hours_total"], "entries": result["entries_written"], "to_check": len(to_check)}


def finish_review(import_id: str, root=None) -> None:
    """After a person approved a held file in the review screen, move it from needs-review/ to processed/."""
    imp = get_import(import_id)
    if not imp or imp.get("source") != "folder":
        return
    folder_file = (imp["payload"] or {}).get("folder_file") or ""
    inbox = inbox_dir(imp["project_id"], root)
    src = inbox / folder_file
    if folder_file.startswith(NEEDS_REVIEW + "/") and src.is_file() and not src.is_symlink():
        update_import(import_id, payload_extra={"folder_file": _move(src, inbox / PROCESSED)})


# ---------------------------------------------------------------- scanning and the loop

def _settled(path: Path, settle_seconds: float, recheck_seconds: float) -> bool:
    """A file is ready when nothing has touched it for a moment and its size did not change over a short re-check (a copy in progress
    keeps changing both). ctime is used as well as mtime: a Finder copy keeps the old modification time but updates the change time."""
    try:
        first = path.stat()
        if time.time() - max(first.st_mtime, first.st_ctime) < settle_seconds:
            return False
        if recheck_seconds:
            time.sleep(recheck_seconds)
        second = path.stat()
    except OSError:
        return False
    return (first.st_size, first.st_mtime_ns, first.st_ctime_ns) == (second.st_size, second.st_mtime_ns, second.st_ctime_ns)


def scan_once(root=None, project_ids: list = None, settle_seconds: float = None, recheck_seconds: float = 0.5) -> list:
    root = Path(root or ROOT)
    known = project_ids if project_ids is not None else list_project_ids()
    settle = SETTLE_SECONDS if settle_seconds is None else settle_seconds
    ensure_folders(known, root)
    STATUS["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    results = []
    for pid in known:
        if not _safe_id(pid):
            continue
        inbox = inbox_dir(pid, root)
        try:
            files = sorted(p for p in inbox.iterdir() if p.is_file() and not p.is_symlink() and not _ignorable(p.name))
        except OSError:
            continue
        for path in files:
            key = (str(path), path.stat().st_mtime_ns if path.exists() else 0)
            if key in _stuck or not _settled(path, settle, recheck_seconds):
                continue
            try:
                results.append(process_file(pid, path, known, root))
            except Exception as e:  # e.g. the file could not be moved away
                logger.exception(f"{pid}/{path.name}: could not be processed")
                _stuck.add(key)
                results.append({"project": pid, "file": path.name, "status": "failed", "reason": str(e)})
    return results


PAUSED, SCANNING = ".paused", ".scanning"  # marker files in the projects folder: a reset pauses the watcher and waits for a running scan to end


def pause(root=None, timeout: float = 180) -> bool:
    """Stop the watcher from starting a scan and wait for one that is running to finish. True when it is safe to change data and folders."""
    root = Path(root or ROOT)
    root.mkdir(parents=True, exist_ok=True)
    (root / PAUSED).touch()
    deadline = time.time() + timeout
    marker = root / SCANNING
    while marker.exists() and time.time() < deadline:
        if time.time() - marker.stat().st_mtime > 900:  # a marker left behind by a crashed scan
            break
        time.sleep(0.5)
    return not marker.exists() or time.time() - marker.stat().st_mtime > 900


def resume(root=None) -> None:
    (Path(root or ROOT) / PAUSED).unlink(missing_ok=True)


def _loop():
    while True:
        if not (ROOT / PAUSED).exists():
            marker = ROOT / SCANNING
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
                try:
                    scan_once()
                except Exception:
                    logger.exception("Timesheet folder scan failed; trying again next time")
                try:
                    from apps.utils import project_folders
                    project_folders.scan_all()
                except Exception:
                    logger.exception("Plan and meeting folder scan failed; trying again next time")
            finally:
                marker.unlink(missing_ok=True)
        time.sleep(POLL_SECONDS)


def start_watcher() -> None:
    """Called once when the API starts. Off if TIMESHEET_WATCH=0."""
    global _started
    if _started:
        return
    if os.getenv("TIMESHEET_WATCH", "1") == "0":
        logger.info("Timesheet folder watching is switched off (TIMESHEET_WATCH=0)")
        return
    _started = True
    STATUS["enabled"] = True
    (ROOT / SCANNING).unlink(missing_ok=True)  # a marker from a scan the last process died in (a reload): no scan can be running in a new one
    threading.Thread(target=_loop, daemon=True, name="timesheet-watcher").start()
    logger.info(f"Watching {ROOT} every {POLL_SECONDS:g}s for timesheets")
