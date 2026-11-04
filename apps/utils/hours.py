"""Hours arithmetic: budget, estimates, logged hours, forecast, burn check and who-is-available. Pure functions over plain
dicts (no database, no LLM), so every number the Chat or the Hours page shows is computed here and nowhere else - the LLM only
reports them. DECISIONS.md "Hours" has the reasoning; the rules in short:

  remaining (open task) = hours_still_needed if someone set it, else estimate - logged (never below 0); completed -> 0;
                          no estimate and no hours_still_needed -> unknown (the task is listed, never guessed).
  forecast              = all hours logged + remaining on open tasks.
  burn check            = work DONE vs work DUE BY NOW (not vs budget used): a task counts as done in proportion to its
                          hours (logged / (logged + remaining), capped at 90% until it is marked completed), and as due in
                          proportion to how much of its planned weeks (plan start..finish) have passed."""
import calendar
from datetime import date, timedelta

IN_PROGRESS_CREDIT_CAP = 0.9  # an open task never counts as more than 90% done until someone marks it completed
SCHEDULE_TOLERANCE_PTS = 5.0  # done may trail due by this many percentage points and still read "on track"
TIGHT_BUDGET_RATIO = 0.95  # a forecast above 95% of the budget reads "tight"
_SKILL_STOP_WORDS = {"for", "the", "and", "with", "who", "can", "any", "of", "to", "in", "on", "an", "or", "is", "do", "work"}


def week_start(d: date, start_day: int = 0) -> date:
    """The first day of the week containing d. start_day is Python's weekday number: 0 = Monday ... 5 = Saturday, 6 = Sunday."""
    return d - timedelta(days=(d.weekday() - start_day) % 7)


def last_workday_offset(start_day: int = 0) -> int:
    """Days from the first day of a week to its Friday, the last working day (4 for a Monday week, 5 for Sunday, 6 for Saturday)."""
    return (4 - start_day) % 7


def weeks_elapsed(kickoff: date, as_of: date, start_day: int = 0) -> float:
    """Working weeks completed since kickoff, counting the current week by the working days (Mon-Fri) done so far."""
    first = week_start(kickoff, start_day)
    days = (as_of - first).days
    if days < 0:
        return 0.0
    full = days // 7
    return full + min(len(business_days(first + timedelta(days=7 * full), as_of)), 5) / 5


def _num(x):
    return None if x is None else float(x)


def task_remaining(estimate, logged, status, hours_still_needed=None):
    """Hours left on one task, or None when it cannot be known (open, no estimate, nothing said about what is still needed)."""
    if status == "completed":
        return 0.0
    if hours_still_needed is not None:
        return float(hours_still_needed)
    if estimate is None:
        return None
    return max(float(estimate) - float(logged), 0.0)


def _done_fraction(status, logged, remaining):
    if status == "completed":
        return 1.0
    if remaining is None:
        return 0.0
    total = logged + remaining
    return min(logged / total, IN_PROGRESS_CREDIT_CAP) if total > 0 else 0.0


def _due_fraction(start_wk, finish_wk, elapsed):
    """Share of a task's planned window that has passed (linear between the start and the end of its planned weeks)."""
    opens, closes = start_wk - 1, max(finish_wk, start_wk)
    return min(max((elapsed - opens) / (closes - opens), 0.0), 1.0)


def _r(x, n=1):
    return None if x is None else round(float(x), n)


def compute_project_hours(settings: dict, tasks: list, entries: list, as_of: date) -> dict:
    """settings {budget_hours, kickoff_date}; tasks (active ones) [{task_id, task_text, owner, status, estimate_hours,
    plan_start_week, plan_finish_week, hours_still_needed}]; entries [{person, week_start, task_id, hours}]."""
    budget = _num((settings or {}).get("budget_hours"))
    kickoff = (settings or {}).get("kickoff_date")

    logged_by_task, by_person, by_week, by_person_week = {}, {}, {}, {}
    actual_total = 0.0
    for e in entries:
        h = float(e["hours"])
        actual_total += h
        if e.get("task_id") is not None:
            logged_by_task[e["task_id"]] = logged_by_task.get(e["task_id"], 0.0) + h
        by_person[e["person"]] = by_person.get(e["person"], 0.0) + h
        by_week[e["week_start"]] = by_week.get(e["week_start"], 0.0) + h
        pw = by_person_week.setdefault(e["person"], {})
        pw[e["week_start"]] = pw.get(e["week_start"], 0.0) + h

    start_day = int((settings or {}).get("week_start_day") or 0)
    elapsed = weeks_elapsed(kickoff, as_of, start_day) if kickoff else None
    rows, remaining_open = [], 0.0
    sum_est = sched_est = planned_val = earned_val = 0.0
    no_estimate_open, open_over, scheduled_tasks = [], [], 0
    for t in tasks:
        est, status = _num(t.get("estimate_hours")), t.get("status") or "not_started"
        logged = logged_by_task.get(t["task_id"], 0.0)
        ets = _num(t.get("hours_still_needed"))
        remaining = task_remaining(est, logged, status, ets)
        is_open = status != "completed"
        if is_open and remaining is None:
            no_estimate_open.append(t["task_text"])
        if is_open and remaining is not None:
            remaining_open += remaining
        over_estimate = est is not None and logged > est
        if is_open and over_estimate and ets is None:
            open_over.append({"task": t["task_text"], "estimate": _r(est), "logged": _r(logged), "owner": t.get("owner")})
        done = _done_fraction(status, logged, remaining)
        if est is not None:
            sum_est += est
            if elapsed is not None and t.get("plan_start_week") and t.get("plan_finish_week"):
                sched_est += est
                scheduled_tasks += 1
                planned_val += est * _due_fraction(t["plan_start_week"], t["plan_finish_week"], elapsed)
                earned_val += est * done
        rows.append({
            "task_id": t["task_id"], "task_text": t["task_text"], "owner": t.get("owner"), "status": status,
            "estimate_hours": _r(est), "plan_start_week": t.get("plan_start_week"), "plan_finish_week": t.get("plan_finish_week"),
            "logged_hours": _r(logged), "remaining_hours": _r(remaining),
            "hours_still_needed": _r(ets), "over_estimate_by": _r(logged - est) if over_estimate else None,
            "forecast_hours": _r(logged + remaining) if remaining is not None else None,
        })

    task_logged = sum(logged_by_task.get(t["task_id"], 0.0) for t in tasks)
    forecast = actual_total + remaining_open
    planned_pct = round(100 * planned_val / sched_est, 1) if sched_est else None
    done_pct = round(100 * earned_val / sched_est, 1) if sched_est else None
    used_pct = round(100 * actual_total / budget, 1) if budget else None

    schedule = "unknown" if planned_pct is None else ("behind" if done_pct < planned_pct - SCHEDULE_TOLERANCE_PTS else "in_line")
    if budget is None:
        cost = "unknown"
    elif forecast > budget:
        cost = "over"
    elif forecast > TIGHT_BUDGET_RATIO * budget:
        cost = "tight"
    else:
        cost = "within"
    if schedule == "behind" and cost == "over":
        overall = "at_risk"
    elif schedule == "behind" or cost in ("over", "tight"):
        overall = "watch"
    elif schedule == "unknown" and cost == "unknown":
        overall = "unknown"
    else:
        overall = "on_track"

    label = {"on_track": "On track", "watch": "Watch", "at_risk": "At risk", "unknown": "Not enough data"}[overall]
    parts = []
    if schedule == "behind":
        parts.append("less work is done than the plan says should be by now")
    elif schedule == "in_line":
        parts.append("work done is in line with the plan")
    if cost == "over":
        parts.append(f"the forecast is {_r(forecast - budget)} h over the budget")
    elif cost == "tight":
        parts.append("the forecast is within 5% of the budget")
    elif cost == "within":
        parts.append("the forecast is within the budget")
    summary = f"{label}: " + ", but ".join(parts) + "." if parts else f"{label}: not enough information (no budget, kickoff date or planned weeks)."

    recent = [h for w, h in sorted(by_week.items()) if h > 0][-4:]
    burn = sum(recent) / len(recent) if recent else None
    left = None if budget is None else budget - actual_total

    notes = []
    if budget is None:
        notes.append("No hours budget has been set for this project, so hours left in the budget cannot be calculated.")
    if no_estimate_open:
        notes.append(f"{len(no_estimate_open)} open task(s) have no estimate and are left out of the forecast.")
    if open_over:
        notes.append(f"{len(open_over)} open task(s) have used more hours than estimated and nobody has said how many hours "
                     "they still need, so the forecast is probably too low.")
    if kickoff is None:
        notes.append("No kickoff date is set, so work done versus work due by now cannot be calculated.")
    elif sched_est == 0:
        notes.append("No estimated task has planned start and finish weeks, so work done versus work due by now cannot be calculated.")
    if not entries:
        notes.append("No hours have been logged yet.")

    return {
        "as_of": as_of.isoformat(),
        "budget_hours": _r(budget), "logged_hours": _r(actual_total), "hours_left_in_budget": _r(left),
        "remaining_hours_on_open_tasks": _r(remaining_open), "forecast_at_completion": _r(forecast),
        "budget_minus_forecast": None if budget is None else _r(budget - forecast),
        "sum_of_task_estimates": _r(sum_est),
        "hours_not_tied_to_a_task": _r(actual_total - task_logged),
        "burn_check": {
            "budget_used_pct": used_pct, "work_due_by_now_pct": planned_pct, "work_done_pct": done_pct,
            "schedule": schedule, "cost": cost, "status": overall, "summary": summary,
            "weeks_elapsed": _r(elapsed, 2), "tasks_compared": scheduled_tasks,
        },
        "recent_weekly_burn": _r(burn),
        "weeks_of_budget_left_at_that_burn": _r(left / burn) if (burn and left is not None) else None,
        "open_tasks_without_estimate": no_estimate_open,
        "open_tasks_over_their_estimate": open_over,
        "logged_by_person": {p: _r(h) for p, h in sorted(by_person.items())},
        "logged_by_week": {w.isoformat(): _r(h) for w, h in sorted(by_week.items())},
        "logged_by_person_week": {p: {w.isoformat(): _r(x) for w, x in sorted(ws.items())} for p, ws in sorted(by_person_week.items())},
        "tasks": rows,
        "notes": notes,
    }


# ---------------------------------------------------------------- availability

def business_days(start: date, end: date) -> list:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _due_date(task: dict, kickoff, start_day: int = 0):
    """Friday of the task's planned finish week, or an ISO date in its deadline text; None when neither exists."""
    if kickoff and task.get("plan_finish_week"):
        return week_start(kickoff, start_day) + timedelta(days=7 * (task["plan_finish_week"] - 1) + last_workday_offset(start_day))
    text = (task.get("deadline") or "").strip()
    try:
        return date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def _skill_match(person: dict, query: str) -> bool:
    if not query:
        return True
    hay = " ".join([person.get("name") or "", person.get("role") or "", *(person.get("skills") or [])]).lower()  # the name too: people ask "for Elise and Kofi"
    terms = [w for w in query.lower().replace("/", " ").replace(",", " ").split() if len(w) >= 2 and w not in _SKILL_STOP_WORDS]
    # A term matches if it is inside the person's role/skill text, or a longer role word is inside the term ("engineer" in "engineering").
    return any(w in hay or any(part in w for part in hay.split() if len(part) > 3) for w in terms)


def compute_availability(people: list, tasks: list, entries: list, settings: dict, window_start: date, window_end: date,
                         as_of: date, skill: str = None) -> dict:
    """Free hours per person between window_start and window_end: weekly capacity for the working days in the window minus the
    remaining hours of their open tasks, each spread evenly over the working days from today to that task's due date. Open tasks
    with no known remaining hours or no due date are listed as unknown and never guessed.

    Also a week-by-week breakdown for the Capacity grid: each week of the window (by the project's week start) has the person's capacity for
    its working days inside the window and a load. A week that is already over shows the hours LOGGED in it (actual); the current and later
    weeks show the planned hours (planned work only starts today, so the current week's capacity is its remaining days; hours already logged
    this week are given separately and are not added, to avoid mixing a whole week of logged hours with only its remaining capacity)."""
    kickoff = (settings or {}).get("kickoff_date")
    start_day = int((settings or {}).get("week_start_day") or 0)
    logged = {}
    for e in entries:
        if e.get("task_id") is not None:
            logged[e["task_id"]] = logged.get(e["task_id"], 0.0) + float(e["hours"])
    requested = (window_start, window_end)

    def over(d: date) -> bool:
        return week_start(d, start_day) + timedelta(days=6) < as_of

    # Hours are logged per week, so a week that is already over can only be judged whole: widen the window to cover it, rather than set a whole
    # week's logged hours against a few days of capacity.
    if over(window_start):
        window_start = week_start(window_start, start_day)
    if over(window_end):
        window_end = week_start(window_end, start_day) + timedelta(days=6)
    window_days = business_days(window_start, window_end)

    def counts(d: date) -> bool:
        """A day has capacity to give if it is today or later, or if its whole week is over (then its load is what was logged in it).
        The days already gone of the current week are neither: they cannot be planned any more and are not free time."""
        return d >= as_of or over(d)

    counted_days = [d for d in window_days if counts(d)]
    grid = {}  # week start -> working days of that week inside the window
    for d in window_days:
        grid.setdefault(week_start(d, start_day), []).append(d)
    logged_by = {}  # (lower-cased person, week start) -> hours logged
    for e in entries:
        ws = e["week_start"] if isinstance(e["week_start"], date) else date.fromisoformat(str(e["week_start"])[:10])
        key = (e["person"].lower(), ws)
        logged_by[key] = logged_by.get(key, 0.0) + float(e["hours"])
    out = []
    for p in people:
        if not _skill_match(p, skill):
            continue
        cap = _num(p.get("weekly_capacity_hours"))
        load, unknown = 0.0, []
        planned_by_week = {}
        for t in tasks:
            if (t.get("owner") or "").lower() != p["name"].lower() or t.get("status") == "completed":
                continue
            rem = task_remaining(_num(t.get("estimate_hours")), logged.get(t["task_id"], 0.0), t.get("status"),
                                 _num(t.get("hours_still_needed")))
            due = _due_date(t, kickoff, start_day)
            if rem is None or due is None:
                unknown.append({"task_id": t["task_id"], "task": t["task_text"], "reason": "no estimate" if rem is None else "no due date"})
                continue
            span = business_days(as_of, max(due, as_of))
            if due < as_of:  # already late: assume the work lands in the next five working days
                span = business_days(as_of, as_of + timedelta(days=6))[:5]
            if not span:
                span = [as_of]
            overlap = [d for d in span if window_start <= d <= window_end]
            load += rem * len(overlap) / len(span)
            for d in overlap:
                ws = week_start(d, start_day)
                planned_by_week[ws] = planned_by_week.get(ws, 0.0) + rem / len(span)
        capacity = None if cap is None else cap * len(counted_days) / 5
        weeks = []
        past_logged = 0.0  # hours actually worked in the weeks of the window that are already over
        for ws, days in sorted(grid.items()):
            week_cap = None if cap is None else cap * len([d for d in days if counts(d)]) / 5
            past = ws + timedelta(days=6) < as_of
            logged_w = logged_by.get((p["name"].lower(), ws), 0.0)
            week_load = logged_w if past else planned_by_week.get(ws, 0.0)
            if past:
                past_logged += logged_w
            weeks.append({"week": ws.isoformat(), "kind": "actual" if past else "planned", "capacity": _r(week_cap), "load": _r(week_load),
                          "logged": _r(logged_w), "over": week_cap is not None and week_load > week_cap + 0.05})
        out.append({
            "name": p["name"], "role": p.get("role"), "skills": p.get("skills") or [], "weeks": weeks,  # by week first: the figure to quote for a named week
            "weekly_capacity_hours": _r(cap), "capacity_in_window": _r(capacity), "planned_load_in_window": _r(load),
            "logged_in_past_weeks": _r(past_logged),
            "available_hours": None if capacity is None else _r(capacity - load - past_logged),
            "unknown_items": unknown, "over_weeks": sum(1 for w in weeks if w["over"]),
        })
    out.sort(key=lambda r: (r["available_hours"] is None, -(r["available_hours"] or 0)))
    notes = []
    has_past = any(w["kind"] == "actual" for r in out for w in r["weeks"])
    if (window_start, window_end) != requested:
        notes.append(f"The window was widened to {window_start.isoformat()} to {window_end.isoformat()} because a week that is over is counted "
                     "whole (hours are logged per week).")
    if has_past:  # planned work only exists from today on, so an earlier week can only say what was really worked
        notes.append(f"Weeks before today ({as_of.isoformat()}) show the hours logged in them, not planned work; "
                     "available hours there are capacity minus hours logged.")
        if window_end < as_of and not any(r["logged_in_past_weeks"] for r in out):
            notes.append("No hours are logged by anyone in this window. If it was not meant to be in the past, check the dates (today is "
                         f"{as_of.isoformat()}).")
    if any(r["weekly_capacity_hours"] is None for r in out):
        notes.append("Some people have no weekly capacity set, so their free hours are unknown.")
    if any(r["unknown_items"] for r in out):
        notes.append("Some open tasks have no estimate or no due date; they are listed per person and not counted in the load.")
    known = [r for r in out if r["available_hours"] is not None]
    summary = {"people": len(out), "capacity_in_window": _r(sum(r["capacity_in_window"] for r in known)),
               "planned_load_in_window": _r(sum(r["planned_load_in_window"] for r in known)),
               "logged_in_past_weeks": _r(sum(r["logged_in_past_weeks"] for r in known)),
               "available_hours": _r(sum(r["available_hours"] for r in known)),
               "over_allocated": sum(1 for r in out if r["over_weeks"] or (r["available_hours"] is not None and r["available_hours"] < 0)),
               "no_capacity": sum(1 for r in out if r["weekly_capacity_hours"] is None),
               "unknown_tasks": sum(len(r["unknown_items"]) for r in out)}
    return {"window": {"from": window_start.isoformat(), "to": window_end.isoformat(), "working_days": len(window_days), "today": as_of.isoformat(),
                       "requested_from": requested[0].isoformat(), "requested_to": requested[1].isoformat(),
                       "weeks_start_on": calendar.day_name[start_day]},
            "skill_filter": skill, "people": out, "weeks": [w.isoformat() for w in sorted(grid)], "summary": summary, "notes": notes}
