"""A starter plan file made from the SOW's tasks.

Running an SOW creates the project's tasks in the app but no plan FILE. This writes one into projects/<id>/plan/ (only when the folder has no
plan yet, and never over an existing file) with the tasks in the standard columns, so the project plan is a file from day one: the PM fills in
estimates and weeks in Excel, the app reads it whenever it is saved, and approved meeting changes are written into it. The file is read back at
once, so every task gets its Task ID and the plan shows it was read. DECISIONS.md "Plan and meetings by folder"."""
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from apps.db.hours_db import fetch_active_tasks_for_hours, get_hours_settings
from apps.logger import get_logger
from apps.utils import plan_file as pf
from apps.utils.meeting_suggest import finish_week_of

logger = get_logger("PLAN_STARTER")
HEADER = ["Task ID", "Phase", "Task", "Owner", "Start wk", "Finish wk", "Est. hours", "Notes", "Status"]
WIDTHS = (9, 30, 56, 18, 10, 10, 11, 42, 13)
STATUS_TEXT = pf.STATUS_TEXT


class PlanExists(Exception):
    """The project already has a plan file; the starter is never written over or beside it."""


def write_starter_plan(project_id: str, root=None, read_back: bool = True) -> Path:
    """Write projects/<id>/plan/<id>-plan.xlsx from the project's active tasks. Raises PlanExists, or ValueError when there are no tasks."""
    from apps.utils import timesheet_watcher as tw
    plan_dir = Path(root or tw.ROOT) / project_id / "plan"
    if pf.plan_files(plan_dir):
        raise PlanExists(f"This project already has a plan file ({pf.plan_files(plan_dir)[-1].name}).")
    tasks = fetch_active_tasks_for_hours(project_id)
    if not tasks:
        raise ValueError("The project has no tasks yet, so there is nothing to put in a plan.")
    settings = get_hours_settings(project_id)
    kickoff, start_day = settings.get("kickoff_date"), int(settings.get("week_start_day") or 0)

    wb = Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws["A1"] = f"{project_id} - project plan"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = (f"Starter plan made from the SOW on {date.today():%d %b %Y}. Add estimates and start / finish weeks, and keep editing this file: "
                "the app reads it every time it is saved.")
    for c, name in enumerate(HEADER, start=1):
        ws.cell(row=4, column=c, value=name).font = Font(bold=True)
    for n, t in enumerate(tasks, start=1):
        notes = [f"Deadline: {t['deadline']}" if t.get("deadline") else None]
        week = finish_week_of(t.get("deadline"), kickoff, start_day)
        values = [f"T-{n:03d}", None, t["task_text"], t.get("owner"), None, week, None, "; ".join(x for x in notes if x) or None,
                  STATUS_TEXT.get(t.get("status") or "not_started", "Not started")]
        for c, v in enumerate(values, start=1):
            ws.cell(row=4 + n, column=c, value=v)
    last = 4 + len(tasks)
    ws.cell(row=last + 2, column=3, value="Total estimated hours").font = Font(bold=True)
    ws.cell(row=last + 2, column=7, value=f"=SUM(G5:G{last})").font = Font(bold=True)
    for i, w in enumerate(WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A5"

    plan_dir.mkdir(parents=True, exist_ok=True)
    path = plan_dir / f"{project_id}-plan.xlsx"
    tmp = path.with_name(f".{path.stem}.tmp{path.suffix}")
    wb.save(tmp)
    tmp.replace(path)
    logger.info(f"{project_id}: starter plan written with {len(tasks)} task(s)")
    if read_back:
        from apps.utils import project_folders
        project_folders.process_plan_file(project_id, path, root=root)  # every task takes its Task ID; nothing is added or changed
    return path
