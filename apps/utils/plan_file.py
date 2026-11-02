"""The project plan as an Excel file: finding the current one and writing approved changes back into it.

The plan FILE is the source of truth (the team keeps it in Excel); the app reads it (apps/db/plan_sync.py) and, when the PMO approves a
suggestion from a meeting, puts the change into the same file so nobody has to merge it by hand. DECISIONS.md "Plan and meetings by folder".

Writing into someone else's spreadsheet must never lose their work, so this module only does what it can do safely and otherwise raises
Blocked with a reason the PMO can read:
  - a new task is appended after the last task row (rows below the table, such as a totals row, are moved down and a sum over the table is
    extended); nothing is ever inserted in the middle of the table;
  - a change to an existing task writes only the cell that changed (owner, status, finish week, ...); a missing Status column is added;
  - charts, images, pivot tables, merged cells or formulas below the table that cannot be moved safely block the write;
  - the original is copied to plan/versions/ first, and the new file replaces it in one step (no half-written file);
  - the caller checks first that the file is not open in Excel (a lock file) and has not changed since it was last read."""
import os
import re
import shutil
from copy import copy
from datetime import datetime
from pathlib import Path

from apps.utils.hours_import import _norm, _s, _to_hours, map_status

PLAN_SUFFIXES = (".xlsx", ".xlsm", ".csv")
WRITABLE_SUFFIX = ".xlsx"
VERSIONS = "versions"
STATUS_TEXT = {"not_started": "Not started", "in_progress": "In progress", "blocked": "Blocked", "on_hold": "On hold", "completed": "Completed"}
_TOTAL_WORDS = ("total", "subtotal", "grand total", "sum")


class Blocked(Exception):
    """The plan cannot be changed safely; the message says why and what to do."""


# ---------------------------------------------------------------- finding the plan

def ignorable(name: str) -> bool:
    low = name.lower()
    return name.startswith((".", "~$")) or low.endswith((".tmp", ".part", ".crdownload", ".download", ".swp"))


def plan_files(plan_dir: Path) -> list:
    """Plan files in the folder, oldest first. Lock files, temporary files and the versions/ folder are not plans."""
    if not plan_dir.is_dir():
        return []
    files = [p for p in plan_dir.iterdir() if p.is_file() and not p.is_symlink() and not ignorable(p.name) and p.suffix.lower() in PLAN_SUFFIXES]
    return sorted(files, key=lambda p: (p.stat().st_mtime_ns, p.name))


def current_plan_file(plan_dir: Path):
    """The newest plan file, which is the current plan (people either edit one file or drop a new version each time)."""
    files = plan_files(plan_dir)
    return files[-1] if files else None


def is_open_elsewhere(path: Path) -> bool:
    """Excel keeps a ~$name lock file beside an open workbook, LibreOffice a .~lock.name# one."""
    if path.with_name("~$" + path.name).exists() or path.with_name(".~lock." + path.name + "#").exists():
        return True
    # Excel shortens the lock file's name for long file names (it drops the first characters), so any ~$ file ending the same way counts
    return any(len(p.name) > 2 and path.name.endswith(p.name[2:]) and len(p.name[2:]) >= len(path.name) - 2 for p in path.parent.glob("~$*"))


# ---------------------------------------------------------------- references

def ref_key(v) -> str:
    """A task id as comparable text: '1.0', 1.0 and '1' are the same id; case and spaces are ignored."""
    text = _s(v).lower()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    return text


def next_ref(existing: list):
    """The id for a new task after the highest existing one, in the same style (22.0 -> 23.0, T-014 -> T-015, 7 -> 8); None if the ids are not numbered."""
    best = None
    for r in existing:
        m = re.fullmatch(r"(\D*?)(\d+)(\.0+)?", _s(r))
        if m and (best is None or int(m.group(2)) > best[1]):
            best = (m.group(1), int(m.group(2)), m.group(3) or "", len(m.group(2)))
    if not best:
        return None
    prefix, n, suffix, width = best
    return f"{prefix}{str(n + 1).zfill(width)}{suffix}"


# ---------------------------------------------------------------- formulas below the table

_RANGE = re.compile(r"(\$?[A-Z]{1,3}\$?)(\d+):(\$?[A-Z]{1,3}\$?)(\d+)")
_CELL = re.compile(r"\$?[A-Z]{1,3}\$?\d+")


def _extend_formula(formula: str, last_old: int, amount: int, where: str) -> str:
    """A sum over the table keeps covering it after rows are added. Any other reference to a row that moves cannot be fixed safely."""
    def fix(m):
        start, end = int(m.group(2)), int(m.group(4))
        if end == last_old:
            return f"{m.group(1)}{start}:{m.group(3)}{end + amount}"
        if start > last_old:
            raise Blocked(f"The formula {formula} in {where} points below the task table, so the rows cannot be moved safely. Add the task to the plan by hand.")
        return m.group(0)
    extended = _RANGE.sub(fix, formula)
    if _CELL.search(_RANGE.sub("", formula)):
        raise Blocked(f"The formula {formula} in {where} refers to a single cell below the task table, so the rows cannot be moved safely. Add the task to the plan by hand.")
    return extended


# ---------------------------------------------------------------- the workbook

def _locate(ws, layout: dict):
    """-> (header row, {field: column}) found again by the header TEXT recorded when the plan was read, so a moved column or an added
    title row does not send a value to the wrong cell."""
    want = {f: _norm(t) for f, t in (layout.get("headers") or {}).items()}
    if not want.get("task"):
        raise Blocked("The plan's layout was not recorded; drop the plan file again so it is read afresh.")
    for r in range(1, min(ws.max_row, 80) + 1):
        texts = {c: _norm(ws.cell(r, c).value) for c in range(1, ws.max_column + 1)}
        cols = {f: c for f, t in want.items() for c, v in texts.items() if v and v == t}
        if "task" in cols and "estimate" in cols:
            return r, cols
    raise Blocked("The task header row in the plan file has moved or changed, so the app cannot tell which cell is which. Drop the plan file again so it is read afresh.")


def _is_total_row(ws, r: int) -> bool:
    return any(_norm(ws.cell(r, c).value).startswith(_TOTAL_WORDS) for c in range(1, min(ws.max_column, 4) + 1) if _s(ws.cell(r, c).value))


def _find_row(ws, first, last, cols, ref, task):
    if ref and "task_ref" in cols:
        for r in range(first, last + 1):
            if ref_key(ws.cell(r, cols["task_ref"]).value) == ref_key(ref):
                return r
    if task:
        for r in range(first, last + 1):
            if _norm(ws.cell(r, cols["task"]).value) == _norm(task):
                return r
    return None


def _status_vocabulary(ws, first, last, col):
    """The words the plan already uses for each status (a team's own 'Done' beats our 'Completed'), read before anything is changed."""
    vocab = {}
    if col:
        for r in range(first, last + 1):
            v = ws.cell(r, col).value
            if isinstance(v, str) and v.strip() and map_status(v):
                vocab.setdefault(map_status(v), v)
    return vocab


def _copy_style(src, dst):
    if src.has_style:
        dst._style = copy(src._style)


def merge_into_workbook(path: Path, layout: dict, ops: list, backup_dir: Path = None) -> dict:
    """Apply approved operations to the plan file. ops:
        {"op": "add",    "task", "owner", "phase", "start_week", "finish_week", "estimate", "status", "note"}
        {"op": "change", "ref", "task", "changes": {"owner" | "status" | "finish_week" | "start_week" | "estimate" | "phase": value}}
    -> {"added": [{"index", "ref", "row"}], "changed": [index], "skipped": [{"index", "reason"}]}  (index = position in ops).
    Raises Blocked, and then the file is untouched."""
    from openpyxl import load_workbook
    path = Path(path)
    if path.suffix.lower() != WRITABLE_SUFFIX:
        raise Blocked(f"The plan is a {path.suffix or 'non-Excel'} file; only .xlsx plans can be updated automatically. Save it as .xlsx, or add the tasks by hand.")
    try:
        wb = load_workbook(path)
    except Exception as e:
        raise Blocked("The plan file could not be opened to update it.") from e
    for sh in wb.worksheets:
        if getattr(sh, "_charts", None) or getattr(sh, "_images", None) or getattr(sh, "_pivots", None):
            raise Blocked(f"Sheet '{sh.title}' has charts, images or pivot tables that the app cannot keep intact when saving. Add the tasks by hand.")
    if layout.get("sheet") not in wb.sheetnames:
        raise Blocked(f"The sheet '{layout.get('sheet')}' is no longer in the plan file.")
    ws = wb[layout["sheet"]]
    hdr, cols = _locate(ws, layout)
    first, last = hdr + 1, hdr
    for r in range(first, ws.max_row + 1):
        if _is_total_row(ws, r):
            break
        if _s(ws.cell(r, cols["task"]).value):
            last = r
    if last < first:
        raise Blocked("No task rows were found under the header row.")

    result = {"added": [], "changed": [], "skipped": []}
    vocab = _status_vocabulary(ws, first, last, cols.get("status"))
    status_ops = [o for o in ops if o["op"] == "add" and o.get("status") or o["op"] == "change" and "status" in o.get("changes", {})]
    if status_ops and "status" not in cols:
        col = ws.max_column + 1  # no Status column yet: add one beside the last header
        head = ws.cell(hdr, col)
        head.value = "Status"
        _copy_style(ws.cell(hdr, col - 1), head)
        cols["status"] = col

    # changes to existing tasks: only the cell that changed is written
    for i, op in enumerate(ops):
        if op["op"] != "change":
            continue
        row = _find_row(ws, first, last, cols, op.get("ref"), op.get("task"))
        if row is None:
            result["skipped"].append({"index": i, "reason": "The task is not in the plan file any more."})
            continue
        field_col = {"owner": "owner", "status": "status", "finish_week": "finish", "start_week": "start", "estimate": "estimate", "phase": "phase"}
        missing = []
        for field, value in op["changes"].items():
            col = cols.get(field_col.get(field, field))
            if col is None:
                missing.append(field)
                continue
            if field == "status":
                value = vocab.get(value, STATUS_TEXT.get(value, value))
            ws.cell(row, col).value = value
        if missing and len(missing) == len(op["changes"]):
            result["skipped"].append({"index": i, "reason": "The plan has no " + ", ".join(missing) + " column to put this in."})
        else:
            result["changed"].append(i)

    # new tasks: appended after the last task row
    adds = [(i, o) for i, o in enumerate(ops) if o["op"] == "add"]
    if adds:
        n = len(adds)
        tail = [r for r in range(last + 1, ws.max_row + 1) if any(_s(ws.cell(r, c).value) for c in range(1, ws.max_column + 1))]
        if tail:
            if any(m.min_row > last for m in ws.merged_cells.ranges):
                raise Blocked("There are merged cells below the task table, so rows cannot be added above them safely. Add the task to the plan by hand.")
            for r in tail:
                for c in range(1, ws.max_column + 1):
                    v = ws.cell(r, c).value
                    if isinstance(v, str) and v.startswith("="):
                        ws.cell(r, c).value = _extend_formula(v, last, n, f"{ws.cell(r, c).coordinate}")
            for sh in wb.worksheets:
                if sh is ws:
                    continue
                for row in sh.iter_rows():
                    for cell in row:
                        if isinstance(cell.value, str) and cell.value.startswith("=") and f"{ws.title}!" in cell.value:
                            for m in re.finditer(re.escape(ws.title) + r"'?!\$?[A-Z]{1,3}\$?(\d+)", cell.value):
                                if int(m.group(1)) > last:
                                    raise Blocked(f"The sheet '{sh.title}' refers to cells below the task table, so rows cannot be added safely. Add the task by hand.")
            old_sum = sum(_to_hours(ws.cell(r, cols["estimate"]).value) or 0 for r in range(first, last + 1))
            ws.insert_rows(last + 1, amount=n)
            added_hours = sum(float(o.get("estimate") or 0) for _, o in adds)
            for r in range(last + 1 + n, ws.max_row + 1):  # a totals cell that was a plain number equal to the old sum stays the sum
                v = ws.cell(r, cols["estimate"]).value
                if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v - old_sum) < 0.01 and added_hours:
                    ws.cell(r, cols["estimate"]).value = round(old_sum + added_hours, 2)
        refs = [ws.cell(r, cols["task_ref"]).value for r in range(first, last + 1)] if "task_ref" in cols else []
        numeric_refs = bool(refs) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in refs if v is not None)
        for k, (i, op) in enumerate(adds):
            row = last + 1 + k
            for c in range(1, ws.max_column + 1):
                _copy_style(ws.cell(last, c), ws.cell(row, c))
            ref = None
            if "task_ref" in cols:
                ref = next_ref([_s(ws.cell(r, cols["task_ref"]).value) for r in range(first, row)])
                if ref is not None:
                    ws.cell(row, cols["task_ref"]).value = float(ref) if numeric_refs and "." in ref else int(ref) if numeric_refs else ref
            values = {"task": op["task"], "owner": op.get("owner"), "phase": op.get("phase"), "start": op.get("start_week"),
                      "finish": op.get("finish_week"), "estimate": op.get("estimate"), "notes": op.get("note")}
            for field, value in values.items():
                if field in cols and value not in (None, ""):
                    ws.cell(row, cols[field]).value = value
            if op.get("status") and "status" in cols:
                ws.cell(row, cols["status"]).value = vocab.get(op["status"], STATUS_TEXT.get(op["status"], op["status"]))
            result["added"].append({"index": i, "ref": _s(ref) if ref is not None else None, "row": row})
        last += n

    if not result["added"] and not result["changed"]:
        return result
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
    tmp = path.with_name(f".{path.stem}.tmp-{os.getpid()}{path.suffix}")
    try:
        wb.save(tmp)
        os.replace(tmp, path)  # one step: the plan is either the old file or the new one, never half written
    finally:
        if tmp.exists():
            tmp.unlink()
    return result
