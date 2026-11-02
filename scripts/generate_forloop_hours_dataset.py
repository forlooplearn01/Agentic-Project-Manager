"""Generates the synthetic hours dataset for the forloop project (datasets/hours/forloop/).

One ground-truth table of hours (HOURS below) is rendered into several file layouts a real client might send, plus
forloop-hours-expected.json with the exact answers. Because every file is a view of the same truth, a correct importer
must land on the same totals whichever layout it reads. Deterministic: no randomness, rerunning rewrites identical files.

Story (matches datasets/inputs/input-mom-forloop.txt, the Week 3 sync): ETL, CRM sync, schema and data mapping are done;
the core layout finishes in Week 4; VPN access is blocked (hours keep accruing past its estimate); Returns & Refunds was
added as a change request. Kickoff Monday 2026-09-28, hours cover Weeks 1-4.

Run inside the api container (has openpyxl):  docker compose exec api python scripts/generate_forloop_hours_dataset.py
"""
import csv
import datetime as dt
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

OUT = Path(__file__).resolve().parent.parent / "datasets" / "hours" / "forloop"
KICKOFF = dt.date(2026, 9, 28)  # a Monday
WEEKS = [1, 2, 3, 4]
BUDGET_HOURS = 700

PEOPLE = {  # name -> (role from the SOW, weekly capacity on this project in hours)
    "Sofia Martins": ("Project Manager", 28),
    "Elena Ruiz": ("Engineering Lead", 36),
    "Marcus Chen": ("Backend Engineer", 40),
    "Priya Patel": ("Frontend Engineer", 40),
    "David Osei": ("QA Engineer", 32),
    "Nirmal Babu": ("Delivery Manager", 12),
    "Ram Anandkumar": ("Infrastructure Engineer", 20),
    "Rucheek": ("Technical Director", 6),
}
BUDGET_BY_ROLE = {
    "Engineering Lead": 130, "Backend Engineer": 170, "Frontend Engineer": 150, "QA Engineer": 70,
    "Infrastructure Engineer": 40, "Project Manager": 80, "Delivery Manager": 40, "Technical Director": 20,
}

P1, P2, P3 = "Phase 1: Discovery & Data Integration", "Phase 2: Dashboard Development", "Phase 3: Testing & Launch"
CR, PMO = "Change requests", "Project management"
# key, exact task text in the forloop project, label in the plan file (None = not in the baseline plan), phase, plan owner,
# start week, finish week, estimate hours (None = not in the plan), status at end of Week 4 (assumed; the live DB differs)
TASKS = [
    ("discovery", "Lead technical discovery session with Meridian's IT team", "Technical discovery session with Meridian IT", P1, "Elena Ruiz", 1, 2, 16, "completed"),
    ("etl", "Build secure Extract Transform Load pipeline to ingest daily sales and inventory data", "Secure ETL pipeline: POS sales + inventory ingest", P1, "Marcus Chen", 1, 3, 60, "completed"),
    ("crm", "Implement nightly sync job to pull customer records from CRM", "Nightly CRM customer sync", P1, "Marcus Chen", 2, 3, 32, "completed"),
    ("schema", "Design analytics data schema to support dashboard metrics", "Analytics data schema (fact + dimension tables)", P1, "Elena Ruiz", 2, 3, 24, "completed"),
    ("vpn", "Coordinate with Meridian's IT team to set up secure VPN access", "VPN access to staging POS replica", P1, "Ram Anandkumar", 1, 2, 12, "in_progress"),
    ("mapping", "Deliver data mapping document to Meridian for sign-off", "Data mapping document and client sign-off", P1, "Sofia Martins", 1, 2, 20, "completed"),
    ("layout", "Build dashboard's core layout and navigation shell", "Dashboard shell and navigation", P2, "Priya Patel", 3, 4, 28, "completed"),
    ("sales", "Implement Sales Trends view in the dashboard", "Sales Trends view", P2, "Priya Patel", 4, 5, 36, "in_progress"),
    ("custseg", "Implement Customer Segmentation view in the dashboard", "Customer Segmentation view", P2, "Priya Patel", 5, 6, 36, "not_started"),
    ("inventory", "Implement Inventory Turnover view in the dashboard", "Inventory Turnover view", P2, "Priya Patel", 5, 6, 32, "not_started"),
    ("api", "Build backend API endpoints for dashboard views", "Backend API endpoints for dashboard views", P2, "Marcus Chen", 3, 6, 48, "in_progress"),
    ("rbac", "Implement role-based access control for dashboard", "Role-based access control", P2, "Elena Ruiz", 4, 6, 28, "in_progress"),
    ("qa_e2e", "Conduct end-to-end Quality Assurance across all dashboard views", "End-to-end QA, all dashboard views", P3, "David Osei", 6, 7, 40, "not_started"),
    ("load", "Perform load testing on the backend API", "Load testing (200 concurrent users)", P3, "David Osei", 7, 7, 24, "not_started"),
    ("uat", "Coordinate user acceptance testing with pilot group", "UAT with pilot store managers", P3, "Sofia Martins", 7, 7, 20, "not_started"),
    ("bugs", "Address critical bugs surfaced during User Acceptance Testing", "Critical bug fixes after UAT", P3, "Elena Ruiz", 7, 8, 24, "not_started"),
    ("training", "Deliver training session and user guide to Meridian's staff", "Training session and user guide", P3, "Sofia Martins", 8, 8, 24, "not_started"),
    ("oversee", "Oversee final delivery coordination and sign-off with stakeholders", "Final delivery coordination and sign-off", P3, "Nirmal Babu", 8, 8, 16, "not_started"),
    ("deploy", "Deploy the dashboard to Meridian's production environment", "Production deployment", P3, "Team", 8, 8, 16, "not_started"),
    ("returns", "Build Returns & Refunds trend view", "Returns & Refunds trend view (CR-01)", CR, "Priya Patel", 6, 7, 24, "not_started"),
    ("qaplans", "Draft Quality Assurance test plans for Sales Trends and Customer Segmentation views", "QA test plans: Sales Trends and Customer Segmentation", P3, "David Osei", 3, 5, 12, "in_progress"),
    ("pm", None, "Project management and status reporting", PMO, "Sofia Martins", 1, 8, 40, "in_progress"),
    ("followup", "Follow up with Meridian's IT contact", None, None, None, None, None, None, "completed"),
    ("vpnreview", "Review VPN access resolution", None, None, None, None, None, None, "in_progress"),
]
TASK = {t[0]: t for t in TASKS}

# (person, week, task key or "other", hours, free-text label as it appears in the timesheet file)
HOURS = [
    ("Elena Ruiz", 1, "discovery", 6, "Discovery session prep w/ Meridian IT"),
    ("Elena Ruiz", 1, "discovery", 4, "Discovery session - data sources catalog"),
    ("Elena Ruiz", 2, "discovery", 4, "Discovery session follow-ups"),
    ("Elena Ruiz", 2, "schema", 12, "Analytics schema design (fact/dim tables)"),
    ("Elena Ruiz", 3, "schema", 10, "Analytics schema - team review + finalize"),
    ("Elena Ruiz", 4, "rbac", 6, "RBAC design for dashboard"),
    *[("Elena Ruiz", w, "other", 3, "Standup / internal meetings") for w in WEEKS],
    ("Marcus Chen", 1, "etl", 14, "ETL pipeline - POS ingest"),
    ("Marcus Chen", 1, "etl", 8, "ETL - secure connection setup"),
    ("Marcus Chen", 2, "etl", 13.5, "ETL pipeline build - ingest"),
    ("Marcus Chen", 2, "etl", 12.5, "ETL pipeline build - transform"),
    ("Marcus Chen", 3, "etl", 18, "ETL prod rollout + monitoring"),
    ("Marcus Chen", 2, "crm", 10, "CRM nightly sync job"),
    ("Marcus Chen", 3, "crm", 12, "CRM sync - dedupe + verification"),
    ("Marcus Chen", 3, "api", 6, "Dashboard API endpoints"),
    ("Marcus Chen", 4, "api", 22, "Backend API - sales trends endpoint"),
    *[("Marcus Chen", w, "other", 3, "Standup / meetings") for w in WEEKS],
    *[("Marcus Chen", w, "other", 1, "Admin") for w in WEEKS],
    ("Sofia Martins", 1, "mapping", 8, "Data mapping doc - draft"),
    ("Sofia Martins", 2, "mapping", 12, "Data mapping doc - Meridian sign-off"),
    ("Sofia Martins", 3, "followup", 2, "Chase Meridian IT contact (VPN)"),
    *[("Sofia Martins", w, "pm", 5, "PM: status reports + planning") for w in WEEKS],
    *[("Nirmal Babu", w, "other", 6 if w < 4 else 5, "Delivery management / leadership check-ins") for w in WEEKS],
    ("Nirmal Babu", 4, "vpnreview", 1, "VPN blocker - leadership review"),
    ("Rucheek", 2, "other", 2, "Architecture review"),
    ("Rucheek", 3, "other", 2, "Architecture review"),
    ("Rucheek", 4, "other", 1, "Architecture review"),
    ("Rucheek", 4, "vpnreview", 1, "VPN blocker review"),
    ("Priya Patel", 1, "other", 6, "Onboarding / ramp-up"),
    ("Priya Patel", 2, "other", 6, "Onboarding / design reviews"),
    ("Priya Patel", 3, "layout", 14, "Dashboard shell + nav (React)"),
    ("Priya Patel", 3, "other", 4, "Meetings"),
    ("Priya Patel", 4, "layout", 16, "Dashboard shell + nav (React)"),
    ("Priya Patel", 4, "sales", 8, "Sales Trends view"),
    ("Priya Patel", 4, "other", 4, "Meetings"),
    ("David Osei", 2, "other", 2, "Meetings"),
    ("David Osei", 3, "qaplans", 6, "QA test plans - Sales/Segmentation"),
    ("David Osei", 3, "other", 2, "Meetings"),
    ("David Osei", 4, "qaplans", 8, "QA test plans - Sales/Segmentation"),
    ("David Osei", 4, "other", 2, "Meetings"),
    ("Ram Anandkumar", 1, "vpn", 4, "VPN setup - Meridian staging POS"),
    ("Ram Anandkumar", 2, "vpn", 6, "VPN setup - Meridian staging POS"),
    ("Ram Anandkumar", 3, "vpn", 6, "VPN setup - Meridian staging POS"),
    ("Ram Anandkumar", 4, "vpnreview", 3, "VPN access resolution review"),
    *[("Ram Anandkumar", w, "other", 2, "Meetings / admin") for w in WEEKS],
]

# Week 5 (28 Sep + 28 days = Monday 26 Oct), one flat file for everyone: the "one more timesheet" to upload live in a client demo.
# Labels are deliberately clear, so the import preview needs no corrections. Statuses are unchanged from the end of Week 4.
WEEK5 = 5
HOURS_WEEK5 = [
    ("Marcus Chen", "api", 22, "Backend API endpoints - dashboard views"),
    ("Marcus Chen", "other", 3, "Standup / meetings"),
    ("Elena Ruiz", "rbac", 14, "Role-based access control (RBAC)"),
    ("Elena Ruiz", "other", 3, "Standup / internal meetings"),
    ("Priya Patel", "sales", 16, "Sales Trends view"),
    ("Priya Patel", "custseg", 14, "Customer Segmentation view"),
    ("Priya Patel", "other", 4, "Meetings"),
    ("David Osei", "qaplans", 4, "QA test plans - Sales Trends and Customer Segmentation"),
    ("David Osei", "other", 2, "Meetings"),
    ("Ram Anandkumar", "vpnreview", 2, "VPN access resolution review"),
    ("Ram Anandkumar", "other", 2, "Meetings / admin"),
    ("Sofia Martins", "pm", 5, "PM: status reports + planning"),
    ("Sofia Martins", "uat", 3, "UAT planning with pilot group"),
    ("Nirmal Babu", "other", 5, "Leadership check-ins"),
    ("Rucheek", "other", 1, "Architecture review"),
]

FLAT_PEOPLE = ["Elena Ruiz", "Marcus Chen", "Sofia Martins", "Nirmal Babu", "Rucheek"]  # one row per entry (CSV)
GRID_PEOPLE = ["Priya Patel", "David Osei", "Ram Anandkumar"]  # weekly grid, one sheet per person (xlsx)
# How a name is written in the flat CSV (an importer has to resolve these to the People list)
NAME_VARIANT = {"Elena Ruiz": lambda w: "Ruiz, Elena" if w == 3 else "Elena Ruiz", "Marcus Chen": lambda w: "M. Chen" if w == 4 else "Marcus Chen"}


def week_monday(w):
    return KICKOFF + dt.timedelta(days=7 * (w - 1))


def week_friday(w):
    return week_monday(w) + dt.timedelta(days=4)


def fmt_num(x):
    return int(x) if float(x).is_integer() else x


def spread(h):
    base, extra = divmod(int(h), 5)
    return [base + (1 if i < extra else 0) for i in range(5)]


def header(ws, row, cols):
    for c, v in enumerate(cols, start=1):
        cell = ws.cell(row=row, column=c, value=v)
        cell.font = Font(bold=True)


STATUS_WORDS = {"completed": "Completed", "in_progress": "In progress", "not_started": "Not started", "blocked": "Blocked"}  # as a plan file would say it (end of Week 4)


def write_plan():
    wb = Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws["A1"] = "Forloop - Customer Insights Dashboard for Meridian Retail Group"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = "Project plan, baseline v1 (change requests appended below)"
    ws["A3"] = f"Kickoff: {KICKOFF:%d %b %Y}    Duration: 8 weeks"
    header(ws, 5, ["WBS", "Phase", "Task", "Owner", "Start wk", "Finish wk", "Est. hours", "Notes", "Status"])
    planned = [t for t in TASKS if t[2]]
    r = 6
    for n, t in enumerate(planned, start=1):
        _, _, label, phase, owner, start, finish, est, status = t
        note = "Added by change request CR-01 in Week 3" if phase == CR else ("Ongoing, spread over the engagement" if t[0] == "pm" else "")
        for c, v in enumerate([f"{n}.0", phase, label, owner, start, finish, est, note, STATUS_WORDS[status]], start=1):
            ws.cell(row=r, column=c, value=v)
        r += 1
    ws.cell(row=r + 1, column=3, value="Total estimated hours").font = Font(bold=True)
    ws.cell(row=r + 1, column=7, value=sum(t[7] for t in planned)).font = Font(bold=True)
    for col, width in zip("ABCDEFGHI", (7, 38, 54, 18, 10, 10, 11, 40, 13)):
        ws.column_dimensions[col].width = width

    bs = wb.create_sheet("Budget")
    bs["A1"] = "Commercial budget (hours)"
    bs["A1"].font = Font(bold=True, size=13)
    bs["A3"] = "Total project budget (hours)"
    bs["B3"] = BUDGET_HOURS
    bs["A3"].font = bs["B3"].font = Font(bold=True)
    bs["A4"] = "Includes contingency; the task estimates in the Plan sheet add up to less than this."
    header(bs, 6, ["Role", "Budgeted hours"])
    for i, (role, h) in enumerate(BUDGET_BY_ROLE.items(), start=7):
        bs.cell(row=i, column=1, value=role)
        bs.cell(row=i, column=2, value=h)
    bs.column_dimensions["A"].width = 44
    bs.column_dimensions["B"].width = 16
    wb.save(OUT / "forloop-plan.xlsx")


def write_flat_csv():
    rows = []
    for person, week, key, hours, label in HOURS:
        if person in FLAT_PEOPLE:
            name = NAME_VARIANT.get(person, lambda w: person)(week)
            rows.append((week_friday(week), name, label, hours))
    rows.sort(key=lambda r: (r[0], r[1]))
    with open(OUT / "forloop-timesheet-flat.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Week Ending", "Employee", "Project", "Activity", "Hours", "Comments"])
        for friday, name, label, hours in rows:
            project = "" if label == "Admin" else "Forloop - Meridian"  # a few rows leave the project blank
            comment = "approved" if friday < week_friday(4) else "submitted"
            w.writerow([f"{friday:%d-%b-%Y}", name, project, label, fmt_num(hours), comment])
        w.writerow([])
        w.writerow(["", "", "", "TOTAL", fmt_num(sum(r[3] for r in rows)), ""])  # a footer row the importer must ignore


def write_week5_csv():
    with open(OUT / "forloop-timesheet-week5.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Week Ending", "Employee", "Project", "Activity", "Hours", "Comments"])
        for person, _, hours, label in sorted(HOURS_WEEK5, key=lambda r: r[0]):
            w.writerow([f"{week_friday(WEEK5):%d-%b-%Y}", person, "Forloop - Meridian", label, fmt_num(hours), "submitted"])


def write_grid_xlsx():
    wb = Workbook()
    wb.remove(wb.active)
    for person in GRID_PEOPLE:
        ws = wb.create_sheet(person)
        ws["A1"] = "WEEKLY TIMESHEET"
        ws["A1"].font = Font(bold=True, size=13)
        ws["A2"], ws["B2"] = "Employee:", person
        ws["A3"], ws["B3"] = "Project:", "Forloop - Meridian Retail Group"
        r = 5
        for week in WEEKS:
            entries = [(label, hours) for p, w, _, hours, label in HOURS if p == person and w == week]
            by_label = {}
            for label, hours in entries:
                by_label[label] = by_label.get(label, 0) + hours
            ws.cell(row=r, column=1, value="Week commencing:").font = Font(bold=True)
            ws.cell(row=r, column=2, value=f"{week_monday(week):%d-%b-%Y}")
            r += 1
            days = [f"{(week_monday(week) + dt.timedelta(days=i)):%a %d %b}" for i in range(5)]
            header(ws, r, ["Task / Activity", *days, "Total"])
            r += 1
            day_totals = [0] * 5
            for label, hours in by_label.items():
                ws.cell(row=r, column=1, value=label)
                for i, v in enumerate(spread(hours)):
                    ws.cell(row=r, column=2 + i, value=v)
                    day_totals[i] += v
                ws.cell(row=r, column=7, value=int(hours))
                r += 1
            # Totals are written as plain numbers: openpyxl cannot store a formula's cached value, so a formula here would
            # read back as empty in any importer that opens the file with data_only=True.
            ws.cell(row=r, column=1, value="Total hours").font = Font(bold=True)
            for c, v in enumerate([*day_totals, sum(day_totals)], start=2):
                ws.cell(row=r, column=c, value=v).font = Font(bold=True)
            r += 2
        ws.column_dimensions["A"].width = 42
        for col in "BCDEFG":
            ws.column_dimensions[col].width = 12
    wb.save(OUT / "forloop-timesheet-grid.xlsx")


def person_week_totals():
    totals = {p: {w: 0 for w in WEEKS} for p in PEOPLE}
    for person, week, _, hours, _ in HOURS:
        totals[person][week] += hours
    return totals


def write_summary_xlsx():
    totals = person_week_totals()
    wb = Workbook()
    ws = wb.active
    ws.title = "Hours summary"
    ws["A1"] = "Forloop - hours by person and week (no task detail)"
    ws["A1"].font = Font(bold=True, size=13)
    header(ws, 3, ["Employee", "Role", "Capacity (h/wk)", *[f"Wk {w} ({week_monday(w):%d %b})" for w in WEEKS], "Total"])
    for r, (person, (role, cap)) in enumerate(PEOPLE.items(), start=4):
        ws.cell(row=r, column=1, value=person)
        ws.cell(row=r, column=2, value=role)
        ws.cell(row=r, column=3, value=cap)
        for i, w in enumerate(WEEKS):
            ws.cell(row=r, column=4 + i, value=fmt_num(totals[person][w]))
        ws.cell(row=r, column=8, value=fmt_num(sum(totals[person].values())))
    last = 3 + len(PEOPLE)
    ws.cell(row=last + 1, column=1, value="Total").font = Font(bold=True)
    column_totals = [sum(totals[p][w] for p in PEOPLE) for w in WEEKS]
    for c, v in enumerate([*column_totals, sum(column_totals)], start=4):
        ws.cell(row=last + 1, column=c, value=fmt_num(v)).font = Font(bold=True)
    for col, width in zip("ABCDEFGH", (18, 24, 16, 16, 16, 16, 16, 10)):
        ws.column_dimensions[col].width = width
    wb.save(OUT / "forloop-timesheet-summary.xlsx")


def expected():
    actual = {t[0]: {w: 0 for w in WEEKS} for t in TASKS}
    actual["other"] = {w: 0 for w in WEEKS}
    for _, week, key, hours, _ in HOURS:
        actual[key][week] += hours
    tasks_out, remaining_open, open_no_estimate, open_overruns = [], 0, [], []
    completed_est = total_est = 0
    for key, text, label, phase, owner, start, finish, est, status in TASKS:
        total = sum(actual[key].values())
        is_open = status != "completed"
        remaining = max(est - total, 0) if (est is not None and is_open) else 0
        if est is not None:
            total_est += est
            if not is_open:
                completed_est += est
        if is_open and est is None:
            open_no_estimate.append(text)
        if is_open and est is not None and total > est:
            open_overruns.append({"task": text, "estimate": est, "actual": fmt_num(total)})
        remaining_open += remaining
        tasks_out.append({
            "key": key, "db_task_text": text, "plan_label": label, "owner": owner, "estimate_hours": est,
            "status_assumed": status, "actual_by_week": {str(w): fmt_num(h) for w, h in actual[key].items()},
            "actual_total": fmt_num(total), "remaining_hours": remaining,
            "variance_vs_estimate": None if est is None else fmt_num(total - est),
        })
    actual_total = sum(sum(v.values()) for v in actual.values())
    totals = person_week_totals()
    label_map = {}
    for person, _, key, _, label in HOURS:
        label_map.setdefault(label, set()).add(key)
    return {
        "project_id": "forloop", "kickoff": KICKOFF.isoformat(), "weeks_covered": WEEKS,
        "budget_hours": BUDGET_HOURS, "budget_by_role": BUDGET_BY_ROLE,
        "people": [{"name": n, "role": r, "weekly_capacity_hours": c, "in_flat_csv": n in FLAT_PEOPLE, "in_grid_xlsx": n in GRID_PEOPLE} for n, (r, c) in PEOPLE.items()],
        "metrics": {
            "actual_hours_total": fmt_num(actual_total),
            "hours_left_in_budget": fmt_num(BUDGET_HOURS - actual_total),
            "remaining_hours_on_open_estimated_tasks": remaining_open,
            "forecast_at_completion": fmt_num(actual_total + remaining_open),
            "budget_minus_forecast": fmt_num(BUDGET_HOURS - actual_total - remaining_open),
            "sum_of_task_estimates": total_est,
            "hours_not_tied_to_a_task": fmt_num(sum(actual["other"].values())),
            "budget_used_pct": round(100 * actual_total / BUDGET_HOURS, 1),
            "work_completed_pct_of_estimates": round(100 * completed_est / total_est, 1),
            "open_tasks_without_estimate": open_no_estimate,
            "open_tasks_over_their_estimate": open_overruns,
        },
        "actual_hours_by_person_by_week": {p: {str(w): fmt_num(h) for w, h in ws_.items()} | {"total": fmt_num(sum(ws_.values()))} for p, ws_ in totals.items()},
        "tasks": tasks_out,
        "label_to_task_key": {k: sorted(v) for k, v in sorted(label_map.items())},
        "week5_upload": {
            "file": "forloop-timesheet-week5.csv", "hours": sum(h for _, _, h, _ in HOURS_WEEK5),
            "logged_after": actual_total + sum(h for _, _, h, _ in HOURS_WEEK5),
            "hours_left_in_budget_after": BUDGET_HOURS - actual_total - sum(h for _, _, h, _ in HOURS_WEEK5),
            "forecast_after": 737, "budget_minus_forecast_after": -37,
            "derivation": "forecast = logged + remaining on open tasks; the 100 h of Week 5 cut remaining by 72 h (API 20, RBAC 14, "
                          "Sales Trends 16, Customer Segmentation 14, UAT 3, project management 5), so remaining 346 -> 274 and "
                          "forecast = 463 + 274 = 737",
        },
        "note": "Remaining hours = estimate - actual for open tasks (0 if over or completed); forecast = actual + remaining. 'other' means time not tied to any plan task. Statuses are the assumed state at the end of Week 4 and are not what the live forloop rows currently say.",
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    write_plan()
    write_flat_csv()
    write_week5_csv()
    write_grid_xlsx()
    write_summary_xlsx()
    (OUT / "forloop-hours-expected.json").write_text(json.dumps(expected(), indent=2) + "\n")
    print(f"wrote 6 files to {OUT}")


if __name__ == "__main__":
    main()
