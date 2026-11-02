"""The weekly summary for the PMO: a short plain-text status of one project, built only from what the app already knows (hours report,
availability, plan changes, suggestions waiting). Nobody has to log in to read it: it is text that can be pasted into an email or a chat.
Nothing is sent from here; DECISIONS.md "Plan and meetings by folder" says why."""
from datetime import date, datetime, timedelta, timezone

from apps.db.hours_db import availability_report, project_hours_report
from apps.db.plan_db import list_plan_changes, plan_summary
from apps.db.suggestions_db import counts

STATUS_WORDS = {"on_track": "On track", "watch": "Watch", "at_risk": "At risk", "unknown": "Not enough data"}


def _h(n) -> str:
    return "-" if n is None else f"{round(float(n), 1):g}"


def build_weekly_summary(project_id: str, today: date = None) -> dict:
    today = today or date.today()
    report = project_hours_report(project_id)
    burn = report["burn_check"]
    lines = [f"{project_id}: weekly summary, {today:%d %b %Y}", ""]
    lines.append(f"Status: {STATUS_WORDS.get(burn['status'], burn['status'])}. {burn.get('summary') or ''}".strip())
    if report["budget_hours"] is not None:
        lines.append(f"Hours: {_h(report['logged_hours'])} of {_h(report['budget_hours'])} budget used, {_h(report['hours_left_in_budget'])} left; "
                     f"forecast {_h(report['forecast_at_completion'])} ({_h(abs(report['budget_minus_forecast']))} h "
                     f"{'under' if report['budget_minus_forecast'] >= 0 else 'over'} budget).")
    else:
        lines.append(f"Hours: {_h(report['logged_hours'])} logged; no budget set.")

    plan = plan_summary(project_id)
    if plan["baseline_set_at"]:
        diff = plan["change_hours"] or 0
        lines.append(f"Plan: {_h(plan['current_hours'])} h planned against a baseline of {_h(plan['baseline_hours'])} h ({'+' if diff > 0 else ''}{_h(diff)} h)"
                     + (f", {plan['tasks_added']} task(s) added" if plan["tasks_added"] else "") + (f", {plan['tasks_removed']} removed" if plan["tasks_removed"] else "") + ".")
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = [c for c in list_plan_changes(project_id, 200) if c["changed_at"] >= cutoff and c["field"] not in ("plan_file_read", "plan_imported", "baseline_set")]
    if recent:
        lines += ["", "Plan changes in the last 7 days:"]
        for c in recent[:8]:
            what = f"{c['label']}: {c['old_value'] or 'none'} -> {c['new_value'] or 'none'}" if c["old_value"] or c["new_value"] else c["label"]
            lines.append(f"- {c['task_text'] or 'Project'}: {what}")

    over = report["open_tasks_over_their_estimate"]
    noest = report["open_tasks_without_estimate"]
    if over or noest:
        lines += ["", "Needs attention in the plan:"]
        lines += [f"- {o['task']}: {_h(o['logged'])} h logged of {_h(o['estimate'])} h estimated, still open ({o['owner'] or 'no owner'})" for o in over[:5]]
        lines += [f"- {t}: no estimate, left out of the forecast" for t in noest[:5]]

    avail = availability_report(project_id, today, today + timedelta(days=13))
    busy = [p for p in avail["people"] if p["over_weeks"]]
    if busy or avail["summary"]["no_capacity"]:
        lines += ["", "Capacity, next two weeks:"]
        lines += [f"- {p['name']} is over capacity in {p['over_weeks']} week(s) ({_h(p['planned_load_in_window'])} h planned against {_h(p['capacity_in_window'])} h)" for p in busy[:6]]
        if avail["summary"]["no_capacity"]:
            lines.append(f"- {avail['summary']['no_capacity']} person(s) have no weekly capacity set, so their availability is unknown.")

    waiting = counts(project_id).get("suggested", 0)
    if waiting:
        lines += ["", f"Waiting for your decision: {waiting} suggestion(s) from meetings (Workspace > Suggestions)."]
    return {"project_id": project_id, "subject": f"{project_id}: weekly status, {STATUS_WORDS.get(burn['status'], burn['status'])}", "text": "\n".join(lines)}
