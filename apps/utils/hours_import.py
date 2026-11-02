"""Reads a project plan or a timesheet (Excel or CSV) into rows the Hours page can preview and a person can confirm.

Division of labour (DECISIONS.md "Hours"): CODE finds the header row, the layout and every number - an hours value always comes
from a cell, never from a model, and every row keeps its sheet and row (`source_ref`). An LLM is used only to PROPOSE which column
is which when the headers are not recognised, and that proposal is checked against the sheet and shown for confirmation.
Task names are matched to the project's tasks by exact text, then by meaning (embeddings); anything not clearly matched is left
for the user and counts as 'Other' so the project total stays right."""
import csv
import io
import json
import os
import re
from datetime import date, datetime, timedelta

from dateutil import parser as dateutil_parser

from apps.utils.abbreviations import expand_abbreviations
from apps.utils.hours import week_start

MAX_BYTES = 5 * 1024 * 1024
SUPPORTED = {"xlsx", "xlsm", "csv"}

PERSON_H = ["employee", "name", "person", "resource", "staff", "team member", "consultant", "user", "who", "engineer"]
DATE_H = ["week ending", "week end", "week commencing", "week starting", "week of", "week start", "w e", "date", "week", "period"]
TASK_H = ["task description", "task activity", "task", "activity", "work item", "ticket", "issue", "description", "details", "summary"]
HOURS_H = ["hours worked", "time spent", "hours", "hrs", "duration", "effort", "time", "logged"]
PLAN_TASK_H = ["task name", "task", "work item", "activity", "item", "description", "deliverable"]
OWNER_H = ["owner", "assignee", "assigned to", "responsible", "resource"]
EST_H = ["est hours", "estimated hours", "planned hours", "estimate", "est", "effort", "hours", "est effort"]
START_H = ["start wk", "start week", "start", "from week"]
FINISH_H = ["finish wk", "finish week", "end week", "end wk", "finish", "end", "to week"]
PHASE_H = ["phase", "stage", "workstream"]
REF_H = ["task id", "task no", "task number", "unique id", "uid", "wbs", "id", "ref", "reference"]
STATUS_H = ["status", "state", "percent complete", "complete", "progress"]
NOTES_H = ["notes", "note", "comments", "comment", "remarks"]
DEPENDS_H = ["depends on", "dependencies", "dependency", "predecessors", "predecessor", "depends"]
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TOTAL_WORDS = ("total", "subtotal", "grand total", "sum")
OTHER_PATTERNS = re.compile(r"\b(meeting|meetings|standup|stand-up|stand up|admin|administration|holiday|leave|pto|sick|vacation|"
                            r"1:1|one-on-one|email|emails|ramp-up|ramp up|onboarding|timesheet|general|misc|miscellaneous|"
                            r"internal|training session)\b", re.I)


_MONTH_WORD = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b", re.I)


class HoursImportError(Exception):
    """The file cannot be read as a plan or timesheet; the message says what to fix."""


# ---------------------------------------------------------------- reading files

def read_sheets(filename: str, content: bytes) -> list:
    """-> [{'name': str, 'rows': [[cell, ...], ...]}]. Cell values are as stored (numbers, dates, text)."""
    if len(content) > MAX_BYTES:
        raise HoursImportError("That file is larger than 5 MB.")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED:
        raise HoursImportError("Upload an Excel (.xlsx) or CSV file. Older .xls files need to be saved as .xlsx first.")
    if ext == "csv":
        for enc in ("utf-8-sig", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        rows = [list(r) for r in csv.reader(io.StringIO(text))]
        return [{"name": filename, "rows": _trim(rows)}]
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(content), data_only=True)
    except Exception as e:
        raise HoursImportError("That Excel file could not be opened.") from e
    return [{"name": ws.title, "rows": _trim([list(r) for r in ws.iter_rows(values_only=True)])} for ws in wb.worksheets]


def _trim(rows):
    rows = [[(c.strip() if isinstance(c, str) else c) for c in r] for r in rows]
    while rows and not any(c not in (None, "") for c in rows[-1]):
        rows.pop()
    return rows


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _s(text).lower()).strip()


def _to_hours(v):
    if isinstance(v, bool) or v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:h|hr|hrs|hours?)?\s*", str(v), re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def _to_date(v, dayfirst=True):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    text = _s(v)
    # Only text that looks like a date: three numeric parts ("2026-10-05", "5/10/26") or a month name ("5 Oct 2026").
    # Anything looser would read "2.5 hrs" as a date.
    if not text or not (re.fullmatch(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}", text) or _MONTH_WORD.search(text)):
        return None
    try:
        return dateutil_parser.parse(text, dayfirst=dayfirst, fuzzy=False).date()
    except (ValueError, OverflowError):
        return None


def _header_match(cell, synonyms) -> bool:
    n = _norm(cell)
    if not n:
        return False
    words = set(n.split())
    return any(n == s or set(s.split()) <= words for s in synonyms)


def _find_cols(header, spec: dict) -> dict:
    """spec: field -> synonyms. Each column is claimed by the first field (in spec order) that matches it, once."""
    cols = {}
    for field, syns in spec.items():
        for i, cell in enumerate(header):
            if i not in cols.values() and _header_match(cell, syns):
                cols[field] = i
                break
    return cols


def _is_total(*cells) -> bool:
    return any(_norm(c).startswith(TOTAL_WORDS) or _norm(c) in TOTAL_WORDS for c in cells if _s(c))


# ---------------------------------------------------------------- week headers (person-by-week matrices)

def _header_week(cell, reference: date, start_day: int = 0):
    """A column header that names a week: a real date, 'Wk 1 (28 Sep)', 'Week 2 - 5 Oct 2026', or 'Week 3' (needs kickoff)."""
    d = _to_date(cell)
    if d:
        return week_start(d, start_day)
    text = _s(cell)
    m = re.search(r"\((\d{1,2}\s+[A-Za-z]{3,9})(?:\s+(\d{4}))?\)", text) or re.search(r"(\d{1,2}\s+[A-Za-z]{3,9})(?:\s+(\d{4}))?", text)
    if m and re.match(r"(?i)\s*(wk|week|w)\b", text):
        year = int(m.group(2)) if m.group(2) else None
        for y in ([year] if year else [reference.year - 1, reference.year, reference.year + 1]):
            try:
                d = dateutil_parser.parse(f"{m.group(1)} {y}").date()
            except (ValueError, OverflowError):
                continue
            if year or abs((d - reference).days) <= 183:
                return week_start(d, start_day)
    m = re.fullmatch(r"(?i)\s*(?:wk|week|w)\s*\.?\s*(\d{1,2})\s*", text)
    if m:
        return week_start(reference, start_day) + timedelta(days=7 * (int(m.group(1)) - 1))
    return None


# ---------------------------------------------------------------- timesheet layouts

def parse_timesheet(filename: str, content: bytes, reference: date, mapping: dict = None, llm_mapper=None, week_start_day: int = 0) -> dict:
    """-> {layout, rows: [{person_raw, week_start, label, hours, source_ref}], warnings, mapping}.
    reference: a date near the work (kickoff or today), used to complete headers like 'Wk 1 (28 Sep)'.
    mapping: optional {field: header text} chosen by the user to override column detection (flat layout).
    week_start_day: the project's first day of the week (0 = Monday); every date is filed under the week that contains it."""
    sheets = read_sheets(filename, content)
    rows, warnings, layouts = [], [], []
    used_mapping = None
    for sh in sheets:
        found = (_parse_grid_sheet(sh, warnings, week_start_day) or _parse_matrix_sheet(sh, reference, warnings, week_start_day)
                 or _parse_flat_sheet(sh, warnings, mapping, llm_mapper, week_start_day))
        if not found:
            continue
        layout, parsed, m = found
        layouts.append(layout)
        rows.extend(parsed)
        used_mapping = m or used_mapping
    if not rows:
        raise HoursImportError("No hours were found in that file. Expected one row per entry (employee, date, hours), a weekly "
                               "grid per person, or a table of people by week. Pick the columns yourself if the headers are unusual.")
    return {"layout": layouts[0] if len(set(layouts)) == 1 else "mixed", "rows": rows, "warnings": warnings, "mapping": used_mapping}


def _parse_flat_sheet(sh, warnings, mapping, llm_mapper, start_day=0):
    rows = sh["rows"]
    spec = {"person": PERSON_H, "task": TASK_H, "hours": HOURS_H, "date": DATE_H}
    for hi, header in enumerate(rows[:30]):
        cols = _find_cols(header, spec)
        if mapping:
            by_text = {_norm(c): i for i, c in enumerate(header)}
            forced = {f: by_text[_norm(t)] for f, t in mapping.items() if _norm(t) in by_text}
            if {"person", "hours", "date"} <= set(forced) and forced:
                cols = {**cols, **forced}
        if {"person", "hours", "date"} <= set(cols):
            break
        if llm_mapper and len([c for c in header if _s(c)]) >= 3 and ("hours" in cols or "date" in cols or "person" in cols):
            guess = llm_mapper(header, rows[hi + 1:hi + 11]) or {}
            guess = {f: i for f, i in guess.items() if f in spec and isinstance(i, int) and 0 <= i < len(header)}
            if {"person", "hours", "date"} <= set(guess):
                cols = guess
                warnings.append("The column headers were not recognised, so the assistant proposed which column is which. Check them.")
                break
    else:
        return None
    out = []
    for ri in range(hi + 1, len(rows)):
        r = rows[ri] + [None] * (len(header) - len(rows[ri]))
        person, hours_cell = _s(r[cols["person"]]), r[cols["hours"]]
        if not person or _is_total(person, r[cols["task"]] if "task" in cols else None):
            continue
        hours = _to_hours(hours_cell)
        d = _to_date(r[cols["date"]])
        ref = f"{sh['name']}, row {ri + 1}"
        if hours is None or hours == 0:
            if _s(hours_cell) and hours is None:
                warnings.append(f"{ref}: hours '{_s(hours_cell)}' is not a number, row skipped.")
            continue
        if hours < 0 or d is None:
            warnings.append(f"{ref}: {'negative hours' if hours < 0 else 'no readable date'}, row skipped.")
            continue
        out.append({"person_raw": person, "week_start": week_start(d, start_day), "label": _s(r[cols["task"]]) if "task" in cols else None,
                    "hours": hours, "source_ref": ref})
    mapping_used = {f: _s(header[i]) for f, i in cols.items()}
    return "flat", out, mapping_used


def _parse_grid_sheet(sh, warnings, start_day=0):
    """A weekly grid: 'Week commencing: <date>' then a header of weekday names, task rows, a Total row; repeated per week.
    The person comes from an 'Employee:' cell near the top, else from the sheet name."""
    rows = sh["rows"]
    person = None
    for r in rows[:12]:
        for i, c in enumerate(r[:-1]):
            if re.fullmatch(r"(?i)(employee|name|resource|person)\s*:?", _s(c)) and _s(r[i + 1]):
                person = _s(r[i + 1])
    out, week, day_cols, total_col = [], None, None, None
    in_block = False
    for ri, r in enumerate(rows):
        first = _s(r[0]) if r else ""
        wk = re.match(r"(?i)week\s*(commencing|starting|beginning|of)\s*:?", first)
        if wk:
            d = next((_to_date(c) for c in r[1:] if _to_date(c)), None) or _to_date(re.sub(r"(?i)^week\s*\w+\s*:?", "", first))
            week, day_cols, in_block = (week_start(d, start_day) if d else None), None, False
            continue
        if week and day_cols is None:
            idx = [i for i, c in enumerate(r) if _norm(c)[:3] in DAY_NAMES and _norm(c)]
            if len(idx) >= 3:
                day_cols = idx
                total_col = next((i for i, c in enumerate(r) if _norm(c) in ("total", "hours", "total hours")), None)
                in_block = True
            continue
        if not (week and in_block):
            continue
        if not first or _is_total(first):
            in_block = False if _is_total(first) else in_block
            if not first:
                in_block = False
            continue
        vals = [_to_hours(r[i]) if i < len(r) else None for i in day_cols]
        hours = sum(v for v in vals if v)
        if total_col is not None and total_col < len(r) and _to_hours(r[total_col]) is not None and abs(_to_hours(r[total_col]) - hours) > 0.01:
            warnings.append(f"{sh['name']}, row {ri + 1}: the days add up to {hours:g} but the Total column says "
                            f"{_to_hours(r[total_col]):g}. The days were used.")
        if hours > 0:
            out.append({"person_raw": person or sh["name"], "week_start": week, "label": first, "hours": hours,
                        "source_ref": f"{sh['name']}, row {ri + 1}"})
    return ("grid", out, None) if out else None


def _parse_matrix_sheet(sh, reference, warnings, start_day=0):
    """People down the side, one column per week, no task detail."""
    rows = sh["rows"]
    for hi, header in enumerate(rows[:30]):
        week_cols = {i: _header_week(c, reference, start_day) for i, c in enumerate(header)}
        week_cols = {i: w for i, w in week_cols.items() if w}
        if len(week_cols) < 2 or _find_cols(header, {"hours": ["hours worked", "hrs"], "task": TASK_H}).get("task") is not None:
            continue
        name_col = next((i for i, c in enumerate(header) if _header_match(c, PERSON_H)), 0)
        out = []
        for ri in range(hi + 1, len(rows)):
            r = rows[ri] + [None] * (len(header) - len(rows[ri]))
            person = _s(r[name_col])
            if not person or _is_total(person):
                continue
            for i, w in week_cols.items():
                h = _to_hours(r[i])
                if h:
                    out.append({"person_raw": person, "week_start": w, "label": None, "hours": h,
                                "source_ref": f"{sh['name']}, row {ri + 1}"})
        if out:
            warnings.append("This file has hours per person and week but no tasks, so they are saved as 'Other' (they still count "
                            "toward the project total).")
            return "matrix", out, None
    return None


# ---------------------------------------------------------------- plan

STATUS_WORDS = {
    "not_started": ("not started", "to do", "todo", "open", "new", "planned", "backlog", "not begun", "pending"),
    "in_progress": ("in progress", "started", "wip", "ongoing", "underway", "in flight", "active", "working"),
    "blocked": ("blocked", "stuck", "impediment"),
    "on_hold": ("on hold", "hold", "paused", "deferred"),
    "completed": ("completed", "complete", "done", "finished", "closed", "delivered"),
}


def map_status(raw):
    """A plan file's status cell -> not_started | in_progress | blocked | on_hold | completed, or None when it says nothing usable.
    Words ("Done", "In progress") and percentages (0 / 40% / 100%, or 0.4 as a fraction) are both understood."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        pct = raw * 100 if 0 < raw <= 1 else raw  # Excel stores 40% as 0.4 and 100% as 1
        return "completed" if pct >= 100 else "in_progress" if pct > 0 else "not_started"
    text = _norm(raw)
    if not text:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:percent)?", text)
    if m:
        return map_status(float(m.group(1)))
    for status, words in STATUS_WORDS.items():
        if text in words:
            return status
    for status in ("blocked", "on_hold", "completed", "in_progress", "not_started"):  # a phrase: the most specific word wins
        if any(re.search(rf"\b{re.escape(w)}\b", text) for w in STATUS_WORDS[status]):
            return status
    return None


def parse_plan(filename: str, content: bytes) -> dict:
    """-> {rows: [{label, owner, phase, start_wk, finish_wk, estimate, source_ref, task_ref, status, notes, depends, row}],
    budget_hours, role_budgets, kickoff_date, warnings, layout}.
    `layout` says where the tasks sit in the file ({sheet, header_row, cols: field -> column index, headers: field -> header text,
    last_row}); row numbers are 0-based positions in the sheet. The plan writer uses it to put approved changes back in the right cells."""
    sheets = read_sheets(filename, content)
    # task_ref goes first so a "Task ID" column is claimed as the id and not mistaken for the task-name column
    spec = {"task_ref": REF_H, "task": PLAN_TASK_H, "estimate": EST_H, "owner": OWNER_H, "start": START_H, "finish": FINISH_H,
            "phase": PHASE_H, "status": STATUS_H, "notes": NOTES_H, "depends": DEPENDS_H}
    rows, warnings, budget, role_budgets, kickoff, layout = [], [], None, {}, None, None
    for sh in sheets:
        for r in sh["rows"][:8]:
            m = re.search(r"(?i)kick[- ]?off\s*:?\s*([^\s].*?)(?:\s{2,}|$)", " ".join(_s(c) for c in r if _s(c)).replace(" Duration", "  Duration"))
            if m and not kickoff:
                kickoff = _to_date(m.group(1))
        for hi, header in enumerate(sh["rows"][:30]):
            cols = _find_cols(header, spec)
            if {"task", "estimate"} <= set(cols):
                last_row = hi
                for ri in range(hi + 1, len(sh["rows"])):
                    r = sh["rows"][ri] + [None] * (len(header) - len(sh["rows"][ri]))
                    label = _s(r[cols["task"]])
                    if not label or _is_total(label):
                        continue
                    est = _to_hours(r[cols["estimate"]])
                    get = lambda f: r[cols[f]] if f in cols else None  # noqa: E731
                    ref = _s(get("task_ref")) or None
                    rows.append({"label": label, "owner": _s(get("owner")) or None, "phase": _s(get("phase")) or None,
                                 "start_wk": _to_week_no(get("start")), "finish_wk": _to_week_no(get("finish")),
                                 "estimate": est, "source_ref": f"{sh['name']}, row {ri + 1}", "task_ref": ref,
                                 "status": map_status(get("status")), "notes": _s(get("notes")) or None,
                                 "depends": _s(get("depends")) or None, "row": ri})
                    last_row = ri
                if layout is None:
                    layout = {"sheet": sh["name"], "header_row": hi, "cols": cols, "last_row": last_row,
                              "headers": {f: _s(header[i]) for f, i in cols.items()}}
                break
        for ri, r in enumerate(sh["rows"]):
            label = _norm(r[0]) if r else ""
            if "budget" in label and ("hours" in label or "total" in label):
                val = next((_to_hours(c) for c in r[1:] if _to_hours(c) is not None), None)
                if val is not None and budget is None:
                    budget = val
            hdr = [_norm(c) for c in r]
            if "role" in hdr and any("hours" in h for h in hdr):
                hcol = next(i for i, h in enumerate(hdr) if "hours" in h)
                for rr in sh["rows"][ri + 1:]:
                    if rr and _s(rr[0]) and len(rr) > hcol and _to_hours(rr[hcol]) is not None and not _is_total(rr[0]):
                        role_budgets[_s(rr[0])] = _to_hours(rr[hcol])
    if not rows:
        raise HoursImportError("No tasks with estimated hours were found. The plan needs a header row with a task column and an "
                               "estimated-hours column.")
    if sum(1 for r in rows if r["estimate"] is None):
        warnings.append(f"{sum(1 for r in rows if r['estimate'] is None)} plan row(s) have no estimate and will not get one.")
    return {"rows": rows, "budget_hours": budget, "role_budgets": role_budgets, "kickoff_date": kickoff, "warnings": warnings, "layout": layout}


def _to_week_no(v):
    h = _to_hours(v)
    return int(h) if h is not None and h >= 1 else None


# ---------------------------------------------------------------- people

def _name_parts(name: str):
    text = _s(name)
    if "," in text:
        last, _, first = text.partition(",")
        text = f"{first.strip()} {last.strip()}"
    parts = [p.strip(".") for p in re.split(r"\s+", re.sub(r"[^\w.\s'-]", " ", text)) if p.strip(".")]
    return [p.lower() for p in parts]


def resolve_person(raw: str, known_names: list):
    """The project person this name means, or None when it is unknown or ambiguous. Handles 'Last, First', 'M. Chen', casing."""
    raw_parts = _name_parts(raw)
    if not raw_parts:
        return None
    exact = [n for n in known_names if _name_parts(n) == raw_parts]
    if len(exact) == 1:
        return exact[0]
    matches = []
    for n in known_names:
        parts = _name_parts(n)
        if not parts:
            continue
        if len(raw_parts) == 1:
            ok = raw_parts[0] in (parts[0], parts[-1])
        else:
            first_ok = parts[0] == raw_parts[0] or (len(raw_parts[0]) == 1 and parts[0].startswith(raw_parts[0]))
            ok = first_ok and parts[-1] == raw_parts[-1]
        if ok:
            matches.append(n)
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------- matching labels to tasks

EXACT_SCORE, SUGGEST_MIN, SUGGEST_MARGIN, SUGGEST_SURE, OWNER_BOOST = 1.0, 0.55, 0.06, 0.8, 0.05
CANDIDATES_PER_ITEM = 5
PLAN_LLM_FLOOR = 0.35  # below this a plan row has nothing like it among the tasks, so the model is not asked


def propose_task_matches(items: list, tasks: list, use_llm: bool = True, mode: str = "timesheet") -> list:
    """items [{label, person}], tasks [{task_id, task_text, owner}] -> one dict per item:
    {task_id, task_text, score, match} with match 'exact' | 'suggested' | 'llm' | 'other' | 'none'. Nothing here is final: the
    user confirms in the preview, and 'other' / 'none' rows are stored as time on no listed task.
    Order: exact text (after expanding abbreviations), then meaning (embeddings, a little weight to the person's own tasks), then
    one batched LLM check of everything not exact, which only picks among the closest tasks or says none.
    mode 'plan' is stricter: a plan row only matches a task that is the SAME piece of work (not merely related or by the same
    owner), and one task is claimed by at most one plan row (the closest)."""
    import numpy as np
    from apps.utils.embeddings import get_embeddings_batch

    out = [{"task_id": None, "task_text": None, "score": 0.0, "match": "none"} for _ in items]
    if not tasks:
        return out
    task_norm = [_norm(expand_abbreviations(t["task_text"])) for t in tasks]
    pending = []
    for i, it in enumerate(items):
        label = _s(it.get("label"))
        if not label:
            continue
        n = _norm(expand_abbreviations(label))
        if n in task_norm:
            j = task_norm.index(n)
            out[i] = {"task_id": tasks[j]["task_id"], "task_text": tasks[j]["task_text"], "score": EXACT_SCORE, "match": "exact"}
        else:
            pending.append(i)
    if not pending:
        return out
    label_vecs = np.array(get_embeddings_batch([expand_abbreviations(items[i]["label"]) for i in pending]))
    task_vecs = np.array(get_embeddings_batch([expand_abbreviations(t["task_text"]) for t in tasks]))
    label_vecs /= np.linalg.norm(label_vecs, axis=1, keepdims=True) + 1e-9
    task_vecs /= np.linalg.norm(task_vecs, axis=1, keepdims=True) + 1e-9
    sims = label_vecs @ task_vecs.T
    candidates = {}
    for row, i in enumerate(pending):
        person = (items[i].get("person") or "").lower()
        scores = sims[row] + np.array([OWNER_BOOST if person and (t.get("owner") or "").lower() == person else 0.0 for t in tasks])
        order = np.argsort(-scores)
        candidates[i] = [int(j) for j in order[:CANDIDATES_PER_ITEM]]
        best, second = float(scores[order[0]]), float(scores[order[1]]) if len(order) > 1 else 0.0
        if best >= SUGGEST_MIN and (best - second >= SUGGEST_MARGIN or best >= SUGGEST_SURE):
            j = int(order[0])
            out[i] = {"task_id": tasks[j]["task_id"], "task_text": tasks[j]["task_text"], "score": round(best, 3), "match": "suggested"}
        elif OTHER_PATTERNS.search(items[i]["label"]):
            out[i] = {"task_id": None, "task_text": None, "score": round(best, 3), "match": "other"}
        else:
            out[i] = {"task_id": None, "task_text": None, "score": round(best, 3), "match": "none"}
    if use_llm:
        floor = PLAN_LLM_FLOOR if mode == "plan" else 0.0
        ask = [i for i in pending if out[i]["match"] in ("suggested", "none") and floor <= out[i]["score"] < SUGGEST_SURE]
        for i, j in _llm_pick(items, ask, candidates, tasks, mode).items():
            if j is None:
                out[i] = {"task_id": None, "task_text": None, "score": out[i]["score"], "match": "none"}
            else:
                out[i] = {"task_id": tasks[j]["task_id"], "task_text": tasks[j]["task_text"], "score": out[i]["score"], "match": "llm"}
    if mode == "plan":  # one task per plan row: the closest claim wins, the rest become 'no match' (a new task)
        best_for = {}
        for i, o in enumerate(out):
            if o["task_id"] is not None and (o["task_id"] not in best_for or o["score"] > out[best_for[o["task_id"]]]["score"]):
                best_for[o["task_id"]] = i
        for i, o in enumerate(out):
            if o["task_id"] is not None and best_for[o["task_id"]] != i:
                out[i] = {"task_id": None, "task_text": None, "score": o["score"], "match": "none"}
        for i, o in enumerate(out):  # an embedding-only suggestion is not enough to overwrite a task's estimate
            if o["match"] == "suggested" and o["score"] < SUGGEST_SURE and use_llm:
                out[i] = {"task_id": None, "task_text": None, "score": o["score"], "match": "none"}
    return out


def _llm_pick(items, ask, candidates, tasks, mode="timesheet") -> dict:
    """One batched call: for each entry, which of its closest tasks (if any) is it work on? -> {item index: task index | None}.
    A failed call or an answer outside the offered candidates leaves that entry as the embeddings decided."""
    if not ask:
        return {}
    from apps.llm import complete
    lines = []
    for n, i in enumerate(ask):
        opts = "; ".join(f"[{k}] {tasks[j]['task_text']} (owner: {tasks[j].get('owner') or 'none'})" for k, j in enumerate(candidates[i], start=1))
        lines.append(f"{n}. person: {items[i].get('person') or 'unknown'} | entry: \"{items[i]['label']}\" | candidates: {opts}")
    if mode == "plan":
        rules = ("Each entry is a row from a project plan. Decide whether it describes the SAME piece of work as ONE candidate task "
                 "(the plan may word it differently), or none.\n"
                 "- Related, overlapping or same-owner work is NOT the same task. Ongoing activities such as project management or "
                 "status reporting are their own task unless a candidate is exactly that.\n- If unsure, answer none.\n")
    else:
        rules = ("People log time against free-text labels. For each entry decide which ONE candidate task it is work on, or none.\n"
                 "- Choose a task only if the label clearly describes work on it; people usually log time on their own tasks.\n"
                 "- General overhead (meetings, admin, reviews of no listed task) and anything unclear is none. Do not guess.\n")
    prompt = (rules + 'Return only JSON: {"matches": [{"i": 0, "candidate": 2}, {"i": 1, "candidate": null}]}\n\n' + "\n".join(lines))
    try:
        resp = complete(messages=[{"role": "user", "content": prompt}], temperature=0, response_format={"type": "json_object"},
                        model=os.getenv("HOURS_MATCH_MODEL") or None)
        data = json.loads(resp.choices[0].message.content).get("matches", [])
    except Exception:
        return {}
    picked = {}
    for m in data if isinstance(data, list) else []:
        n, c = m.get("i"), m.get("candidate")
        if not isinstance(n, int) or not 0 <= n < len(ask):
            continue
        i = ask[n]
        if c is None:
            picked[i] = None
        elif isinstance(c, int) and 1 <= c <= len(candidates[i]):
            picked[i] = candidates[i][c - 1]
    return picked


# ---------------------------------------------------------------- LLM column proposal (only when headers are unrecognised)

def propose_mapping_with_llm(header: list, sample_rows: list) -> dict:
    """Asks the model which column is the person, date, hours and (optionally) task. Returns {field: column index} or {}.
    It only proposes: the caller checks the indexes and the user confirms in the preview."""
    from apps.llm import complete
    prompt = ("This is the header row and first rows of a timesheet. Say which column index (0-based) holds: person (who worked), "
              "date (the date or week), hours, task (what they worked on, optional). Return only JSON like "
              '{"person": 0, "date": 1, "hours": 3, "task": 2}; omit a field you cannot find.\n\n'
              f"Header: {json.dumps(header, default=str)}\nRows: {json.dumps(sample_rows[:10], default=str)}")
    try:
        resp = complete(messages=[{"role": "user", "content": prompt}], temperature=0, response_format={"type": "json_object"})
        data = json.loads(resp.choices[0].message.content)
    except Exception:
        return {}
    return {k: v for k, v in data.items() if k in ("person", "date", "hours", "task") and isinstance(v, int)}
