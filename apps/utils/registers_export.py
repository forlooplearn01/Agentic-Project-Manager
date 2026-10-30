"""Excel export of a project's registers: RAID log, RACI (by deliverable and by task) and the deliverables list.

Built in memory (nothing is written to disk). The UI is the source of truth; this is a read-only snapshot for people who
do not use the tool (sponsors, the client) - there is deliberately no import, so edits made in the file never flow back.

Security: openpyxl turns any text that starts with "=" into a FORMULA, and these cells hold text from documents and from the
assistant. Every text cell goes through `_text`, which forces a plain string, so "=HYPERLINK(...)" is shown, never run."""
import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.db.db import fetch_raci_matrix, list_deliverables, list_raid_items

_HEADER_FILL = PatternFill("solid", fgColor="E5E7EB")
_HEADER_FONT = Font(bold=True, color="111827")
_WRAP = Alignment(wrap_text=True, vertical="top")
_WARNING_TEXT = {
    "multiple_accountable": "More than one Accountable",
    "no_responsible": "Nobody Responsible",
    "no_accountable": "No Accountable yet",
}
_TYPE_LABEL = {"risk": "Risk", "assumption": "Assumption", "issue": "Issue", "dependency": "Dependency"}
_ORIGIN_LABEL = {"manual": "Added by hand", "document": "From the SOW", "agent": "Suggested, accepted", "task": "From a task"}
_STATUS_LABEL = {"not_started": "Not started", "in_progress": "In progress", "completed": "Completed", "blocked": "Blocked",
                 "on_hold": "On hold"}


def _text(cell, value) -> None:
    """Write a value as plain text: a string beginning with '=' must never become a formula."""
    cell.value = "" if value is None else value
    if isinstance(cell.value, str):
        cell.data_type = "s"


def _sheet(wb: Workbook, title: str, headers: list, rows: list, widths: list) -> None:
    ws = wb.create_sheet(title)
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=c)
        _text(cell, h)
        cell.fill, cell.font = _HEADER_FILL, _HEADER_FONT
    for r, row in enumerate(rows, start=2):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c)
            _text(cell, value)
            cell.alignment = _WRAP
    for c, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"


def _raci_rows(matrix: dict) -> tuple:
    """(headers, rows): one row per subject, one column per person; an inferred Responsible is shown as '(R)'."""
    people = matrix["people"]
    headers = ["#", "Title", "Owner"] + people + ["Check"]
    rows = []
    for r in matrix["rows"]:
        by_person = {a["person"]: a for a in r["assignments"]}
        cells = []
        for p in people:
            a = by_person.get(p)
            cells.append("" if not a else (f"({'/'.join(a['roles'])})" if a["implied"] else "/".join(a["roles"])))
        rows.append([r["number"] or "", r["title"], r["owner"] or ""] + cells + ["; ".join(_WARNING_TEXT[w] for w in r["warnings"])])
    return headers, rows


def build_registers_workbook(project_id: str) -> bytes:
    raid_all = list_raid_items(project_id)
    raid = [i for i in raid_all if i["review_state"] == "confirmed"]
    waiting_raid = sum(1 for i in raid_all if i["review_state"] == "suggested")
    deliverables_all = list_deliverables(project_id)
    deliverables = [d for d in deliverables_all if d["review_state"] == "confirmed"]
    waiting_deliverables = len(deliverables_all) - len(deliverables)
    raci_deliverables = fetch_raci_matrix(project_id, "deliverable")
    raci_tasks = fetch_raci_matrix(project_id, "task")

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    open_of = lambda t: sum(1 for i in raid if i["item_type"] == t and i["status"] != "closed")
    lines = [
        ("Project", project_id),
        ("Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")),
        ("", ""),
        ("Open risks", open_of("risk")),
        ("Open issues", open_of("issue")),
        ("Assumptions", open_of("assumption")),
        ("Dependencies", open_of("dependency")),
        ("Deliverables", len(deliverables)),
        ("Tasks in the RACI sheet", len(raci_tasks["rows"])),
        ("", ""),
        ("Waiting for review (NOT in this file)", waiting_raid + waiting_deliverables),
        ("", ""),
        ("Notes", "A snapshot of the registers at the time shown. Edit them in the tool, not in this file - changes here do not flow back."),
        ("", "In the RACI sheets, (R) means Responsible inferred from the owner and not assigned yet."),
    ]
    for r, (label, value) in enumerate(lines, start=1):
        _text(summary.cell(row=r, column=1), label)
        _text(summary.cell(row=r, column=2), value)
        summary.cell(row=r, column=1).font = Font(bold=True)
        summary.cell(row=r, column=2).alignment = _WRAP
    summary.column_dimensions["A"].width = 38
    summary.column_dimensions["B"].width = 90

    _sheet(wb, "RAID log",
           ["Type", "Title", "Description", "Owner", "Status", "Probability", "Impact", "Review by", "Source", "Created"],
           [[_TYPE_LABEL[i["item_type"]], i["title"], i["description"] or "", i["owner"] or "", i["status"].capitalize(),
             (i["probability"] or "").capitalize(), (i["impact"] or "").capitalize(), str(i["review_date"] or ""),
             _ORIGIN_LABEL.get(i["origin"], i["origin"]), i["created_at"].strftime("%Y-%m-%d")] for i in raid],
           [12, 52, 60, 18, 13, 12, 10, 12, 18, 12])

    _sheet(wb, "Deliverables",
           ["#", "Deliverable", "Owner", "Deadline", "Status", "Tasks assigned", "Source"],
           [[d["number"] or "", d["title"], d["owner"] or "", d["deadline"] or "", _STATUS_LABEL.get(d["status"], d["status"]),
             d["task_count"], _ORIGIN_LABEL.get(d["origin"], d["origin"])] for d in deliverables],
           [8, 56, 26, 24, 14, 14, 18])

    for title, matrix in (("RACI - Deliverables", raci_deliverables), ("RACI - Tasks", raci_tasks)):
        headers, rows = _raci_rows(matrix)
        _sheet(wb, title, headers, rows, [8, 56, 24] + [14] * len(matrix["people"]) + [34])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
