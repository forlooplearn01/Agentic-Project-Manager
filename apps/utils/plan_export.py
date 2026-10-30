"""Excel export of a project's plan: the plan table (every task with its phase, weeks, estimate, baseline, logged and remaining hours), a
summary, and the change history. Built in memory. One-way by design: the app is the master of the plan, so nothing in the file ever
flows back (same security rule as registers_export: text cells are forced to plain strings, never formulas)."""
import io
from datetime import datetime, timezone

from openpyxl import Workbook

from apps.db.db import fetch_project_tasks_with_dependencies
from apps.db.hours_db import get_hours_settings
from apps.db.plan_db import HISTORY_FIELD_LABELS, list_plan_changes, plan_summary
from apps.utils.registers_export import _STATUS_LABEL, _sheet, _text


def _phase_order(tasks: list) -> dict:
    """Phases in the order they start (earliest planned week first); tasks with no phase last."""
    first = {}
    for t in tasks:
        week = t["plan_start_week"] if t["plan_start_week"] is not None else 9999
        first[t["phase"]] = min(first.get(t["phase"], 9999), week)
    return {p: i for i, p in enumerate(sorted(first, key=lambda p: (p is None, first[p], p or "")))}


def build_plan_workbook(project_id: str) -> bytes:
    tasks = fetch_project_tasks_with_dependencies(project_id)
    summary = plan_summary(project_id)
    has_baseline = summary["baseline_set_at"] is not None
    order = _phase_order(tasks)
    tasks.sort(key=lambda t: (order[t["phase"]], t["plan_start_week"] if t["plan_start_week"] is not None else 9999, t["task_text"].lower()))

    rows = []
    for t in tasks:
        est, base = t["estimate_hours"], t["baseline_estimate_hours"]
        change = None if (est is None or not has_baseline) else round(est - (base or 0), 2)
        rows.append([
            t["phase"] or "", t["task_text"], t["owner"] or "", _STATUS_LABEL.get(t["status"] or "not_started", t["status"]),
            (t["priority"] or "").capitalize(), t["deadline"] or "", t["blocker"] or "",
            t["plan_start_week"], t["plan_finish_week"], est, base, change, t["logged_hours"], t["remaining_hours"],
            "Yes" if t["added_after_baseline"] else "",
        ])
    wb = Workbook()
    wb.remove(wb.active)
    _sheet(wb, "Plan", ["Phase", "Task", "Owner", "Status", "Priority", "Deadline", "Blocker", "Start week", "Finish week", "Estimate (h)",
                        "Baseline (h)", "Change vs baseline (h)", "Logged (h)", "Left (h)", "Added after baseline"],
           rows, [22, 56, 18, 14, 10, 16, 22, 10, 10, 12, 12, 14, 11, 10, 12])

    settings = get_hours_settings(project_id)
    fmt = lambda v: "" if v is None else v  # noqa: E731
    _sheet(wb, "Summary", ["Item", "Value"], [
        ["Project", project_id],
        ["Exported", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
        ["Hours budget", fmt(settings["budget_hours"])],
        ["Kickoff date", settings["kickoff_date"].isoformat() if settings["kickoff_date"] else ""],
        ["Baseline set", summary["baseline_set_at"][:10] if has_baseline else "No baseline yet"],
        ["Baseline estimate (h)", fmt(summary["baseline_hours"])],
        ["Current estimate (h)", summary["current_hours"]],
        ["Change since baseline (h)", fmt(summary["change_hours"])],
        ["Tasks added since baseline", summary["tasks_added"] if has_baseline else ""],
        ["Tasks removed since baseline", summary["tasks_removed"] if has_baseline else ""],
    ], [30, 30])

    changes = [[c["changed_at"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), HISTORY_FIELD_LABELS.get(c["field"], c["field"]),
                c["task_text"] or "", c["old_value"] or "", c["new_value"] or "", c["source"]] for c in list_plan_changes(project_id, 1000)]
    _sheet(wb, "Change history", ["When", "Change", "Task", "From", "To", "Source"], changes, [20, 22, 56, 22, 22, 12])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
