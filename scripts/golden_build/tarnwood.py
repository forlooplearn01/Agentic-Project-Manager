"""Demo project `tarnwood`: an AI analytics platform on VMware Aria Operations data, built by Forloop for the fictional Tarnwood Industrial.
Week 6 of 8 (weeks run Wednesday to Tuesday, as in the SOW), on track. Made from the VMware simulation (scripts/golden_build/transform_sim.py):
the plan is the SOW's 11 deliverables plus every action item from meetings 1-5, statuses worked out from the updates in each meeting.
The ready-to-run file is meeting 6 (12 new tasks and 4 status completions against this plan).

    python -m scripts.golden_build.tarnwood"""
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

from scripts.golden_build.common import build_project

SRC = Path(__file__).resolve().parents[2] / "datasets" / "golden-src" / "tarnwood"
KICKOFF = date(2026, 9, 23)  # a Wednesday: weeks run Wednesday to Tuesday
TODAY = date(2026, 10, 29)
LAST_DONE = date(2026, 10, 27)  # the end of week 5: what was due by then and never updated is finished
OWNER = {"Rucheek": "Rucheek", "Nirmal Babu": "Nirmal Babu", "Ram Anandkumar": "Ram Anandkumar", "Priya Nair": "Priya Nair"}


def read(name: str) -> str:
    return (SRC / name).read_text()


def week_of(d: date) -> int:
    return max(1, (d - KICKOFF).days // 7 + 1)


def parse_date(text: str):
    return datetime.strptime(text.strip(), "%B %d, %Y").date() if text and text.strip() not in ("None", "null") else None


def parse_expected(n: int) -> dict:
    out = {}
    for name, body in zip(*[iter(re.split(r"--- (.*?) ---", read(f"expected-output-meeting-0{n}.txt"))[1:])] * 2):
        items = [dict(re.findall(r"^(\w+): (.*)$", it, re.M)) for it in re.split(r"\n\d+\.\n", body)[1:] if "task:" in it]
        out[name.split("(")[0].strip()] = items
    return out


def phase_of(finish: int) -> str:
    return ("Week 1: Kickoff and setup" if finish <= 1 else "Weeks 2-3: Data source and API integration" if finish <= 3 else
            "Weeks 4-5: AI agent, graphs and reports" if finish == 4 or finish == 5 else "Week 6: Testing and UAT" if finish == 6 else
            "Week 7: Production deployment" if finish == 7 else "Week 8: Handover and closure")


def hours_for(text: str) -> int:
    first = text.split()[0].lower()
    if first in ("send", "share", "create", "circulate", "respond", "confirm", "follow", "identify", "brief", "add", "update", "flag"):
        return 3
    if first in ("complete", "implement", "prototype", "finalize", "push", "deliver"):
        return 10
    return 6


def deliverables() -> list:
    d = lambda key, text, owner, start, finish, est, status, deadline, depends=(), **kw: {  # noqa: E731
        **dict(key=key, text=text, owner=owner, start=start, finish=finish, est=est, status=status, deadline=deadline, depends=list(depends),
               phase=phase_of(finish), priority="high"), **kw}
    return [
        d("kick", "Project kickoff and environment setup", "Nirmal Babu", 1, 1, 20, "completed", "2026-09-29", priority="medium"),
        d("d41", "Deliverable 4.1: Data Source Integration Module (codebase and unit tests)", "Rucheek", 2, 2, 40, "completed", "2026-10-06"),
        d("d43", "Deliverable 4.3: VMware Aria Operations API Integration (codebase and unit tests)", "Priya Nair", 2, 3, 30, "completed", "2026-10-13", ["d41"]),
        d("d42", "Deliverable 4.2: AI Agent and Graph Generation Module (codebase and unit tests)", "Rucheek", 3, 4, 70, "completed", "2026-10-20", ["d41"]),
        d("d44", "Deliverable 4.4: Multi-cluster Data Aggregation Support (codebase and unit tests)", "Rucheek", 3, 4, 40, "completed", "2026-10-20", ["d43"]),
        d("d45", "Deliverable 4.5: Report Generation Module (codebase and unit tests)", "Rucheek", 4, 5, 50, "in_progress", "2026-10-27", ["d42"]),
        d("d46", "Deliverable 4.6: Integration Testing Report", "Ram Anandkumar", 5, 6, 24, "not_started", "2026-11-03", ["d41", "d42", "d43", "d44", "d45"]),
        d("d47", "Deliverable 4.7: User Acceptance Testing (UAT) Report", "Ram Anandkumar", 6, 6, 20, "not_started", "2026-11-03", ["d46"]),
        d("d48", "Deliverable 4.8: Production Deployment and Smoke Test Results", "Rucheek", 7, 7, 30, "not_started", "2026-11-10", ["d47"]),
        d("d49", "Deliverable 4.9: Deployment Runbook", "Rucheek", 6, 7, 12, "not_started", "2026-11-10", priority="medium"),
        d("d410", "Deliverable 4.10: User Guide and Technical Reference Document", "Ram Anandkumar", 7, 8, 30, "not_started", "2026-11-17", ["d48"], priority="medium"),
        d("d411", "Deliverable 4.11: Knowledge Transfer Session", "Nirmal Babu", 8, 8, 8, "not_started", "2026-11-17", ["d410"], priority="medium"),
        d("pm", "Project management and status reporting", "Nirmal Babu", 1, 8, 56, "in_progress", "2026-11-17", priority="medium"),
    ]


def action_tasks() -> tuple:
    tasks, events, taken = [], [], set()
    for n in range(1, 6):  # the new tasks of meetings 1-5, then each meeting's updates applied to what exists
        exp = parse_expected(n)
        doc = f"meeting-0{n}.txt"
        for it in exp.get("NEW TASKS", []):
            owner = OWNER.get(it["owner"].split(",")[0].strip())
            due = parse_date(it.get("deadline"))
            if not owner or not due:
                continue
            key = f"a{len(tasks) + 1:02d}"
            start, finish = n, max(n, week_of(due))
            status = "completed" if due <= LAST_DONE else "not_started"
            tasks.append(dict(key=key, text=it["task"], owner=owner, start=start, finish=finish, est=hours_for(it["task"]), status=status,
                              deadline=due.isoformat(), depends=[], phase=phase_of(finish), priority="medium"))
            events.append(dict(task=key, doc=doc, type="status_change", status="not_started", owner=owner, deadline=due.isoformat(), match="created"))
        for it in exp.get("UPDATE EVENTS", []):
            status = it.get("status")
            if status not in ("completed", "in_progress", "blocked"):
                continue
            best = max(tasks, key=lambda t: SequenceMatcher(None, t["text"].lower()[:60], it["task"].lower()[:60]).ratio(), default=None)
            if best and SequenceMatcher(None, best["text"].lower()[:60], it["task"].lower()[:60]).ratio() >= 0.8:
                best["status"] = status
                events.append(dict(task=best["key"], doc=doc, type="status_change", status=status, owner=best["owner"], match="exact"))
    # work due by the end of week 5 is done; what is open at the start of week 6 is in progress or waiting
    for t in tasks:
        if t["status"] == "not_started" and t["start"] == 5:
            t["status"] = "in_progress" if t["deadline"] <= "2026-11-03" and t["key"] in ("a31", "a34", "a37", "a38") else t["status"]
    return tasks, events


def spec() -> dict:
    acts, events = action_tasks()
    tasks = deliverables() + acts
    sow = read("sow.txt")
    history = [(f"meeting-0{n}.txt", read(f"meeting-0{n}.txt"), parse_date(re.search(r"^Date: (.*)$", read(f"meeting-0{n}.txt"), re.M).group(1))) for n in range(1, 6)]
    people = [
        dict(name="Nirmal Babu", role="Project Manager", capacity=14, skills=["project management", "stakeholder management", "delivery"]),
        dict(name="Rucheek", role="AI Engineer", capacity=36, skills=["python", "ai agents", "llm", "pandas", "reporting", "api integration"]),
        dict(name="Ram Anandkumar", role="Technical Director", capacity=24, skills=["architecture", "testing", "documentation", "uat"]),
        dict(name="Priya Nair", role="VMware Engineer (Tarnwood)", capacity=20, skills=["vmware", "aria operations", "infrastructure", "networking"]),
        dict(name="Mina Kowalski", role="ML Engineer", capacity=28, skills=["machine learning", "python", "data engineering", "pandas"]),
        dict(name="Tobias Lindqvist", role="QA Engineer", capacity=16, skills=["qa", "test automation", "integration testing"]),
    ]
    raid = [
        dict(type="issue", title="Production network whitelist approval is delayed", owner="Priya Nair", status="mitigating", probability="high", impact="high",
             description="Tarnwood's production network whitelist request was raised in week 1 and is still being chased; production deployment depends on it.",
             evidence="Meetings 1 to 5: whitelist approval status carried as an open action."),
        dict(type="risk", title="IT security concern about sending sample rows to the language model", owner="Priya Nair", status="mitigating", probability="medium", impact="high",
             description="Tarnwood's IT security team raised a data privacy concern about sample rows leaving the environment; the team is acknowledging and addressing it.",
             evidence="Meeting 4: respond to IT security acknowledging the data privacy concern."),
        dict(type="risk", title="Aria Operations API returns 503 errors under load", owner="Rucheek", status="mitigating", probability="medium", impact="medium",
             description="Intermittent 503 responses from the API; exponential backoff and retry is being implemented.", evidence="Meeting 3: implement 503 error handling with exponential backoff."),
        dict(type="issue", title="Sample CSV datasets arrived late", owner="Priya Nair", status="closed", probability=None, impact=None,
             description="Sample data was due in week 1 and arrived in week 2; the CSV module was not delayed.", evidence="Meeting 2: sample CSV files delivered."),
        dict(type="dependency", title="AI agent graph module must finish before report generation starts", owner="Rucheek", status="closed",
             description="Deliverable 4.2 had to be complete before work on Deliverable 4.5; 4.2 is complete.", evidence="SOW section 8.4."),
        dict(type="dependency", title="Production environment access must be provisioned by 1 November", owner="Priya Nair", status="open",
             description="Access to Tarnwood's production environment is needed before the week 7 deployment.", evidence="SOW section 8.2."),
        dict(type="dependency", title="UAT sign-off is required before production deployment", owner="Ram Anandkumar", status="open", review_state="suggested",
             description="Deliverable 4.8 cannot start until the UAT report (4.7) is signed off.", evidence="SOW section 8.6."),
        dict(type="assumption", title="Priya Nair is available at least 20 hours a week", owner="Priya Nair", status="open",
             description="Tarnwood commits a VMware engineer for UAT, access and deployment support.", evidence="SOW section 7.5."),
        dict(type="assumption", title="The Stat Key API structure will not change during the engagement", owner="Rucheek", status="open",
             description="A change to the endpoint structure would need a change request.", evidence="SOW section 7.8."),
        dict(type="risk", title="Language model running cost after handover is not yet estimated", owner="Ram Anandkumar", status="open", probability="medium", impact="medium",
             review_state="suggested", description="Tarnwood will pay for model calls once it owns the platform; an estimate is part of the closure documents.",
             evidence="Meeting 5: add the LLM operating cost estimate to the closure deliverables."),
    ]
    raci = lambda r, a, c, i: {r: "R", a: "A", c: "C", i: "I"}  # noqa: E731
    deliv = []
    for key, number, title, owner, due, status in [
        ("d41", "4.1", "Data Source Integration Module", "Rucheek", "Week 2 (October 6, 2026)", "completed"),
        ("d42", "4.2", "AI Agent and Graph Generation Module", "Rucheek", "Week 4 (October 20, 2026)", "completed"),
        ("d43", "4.3", "VMware Aria Operations API Integration", "Priya Nair", "Week 3 (October 13, 2026)", "completed"),
        ("d44", "4.4", "Multi-cluster Data Aggregation Support", "Rucheek", "Week 4 (October 20, 2026)", "completed"),
        ("d45", "4.5", "Report Generation Module", "Rucheek", "Week 5 (October 27, 2026)", "in_progress"),
        ("d46", "4.6", "Integration Testing Report", "Ram Anandkumar", "Week 6 (November 3, 2026)", "not_started"),
        ("d47", "4.7", "User Acceptance Testing (UAT) Report", "Ram Anandkumar", "Week 6 (November 3, 2026)", "not_started"),
        ("d48", "4.8", "Production Deployment and Smoke Test Results", "Rucheek", "Week 7 (November 10, 2026)", "not_started"),
        ("d49", "4.9", "Deployment Runbook", "Rucheek", "Week 7 (November 10, 2026)", "not_started"),
        ("d410", "4.10", "User Guide and Technical Reference Document", "Ram Anandkumar", "Week 8 (November 17, 2026)", "not_started"),
        ("d411", "4.11", "Knowledge Transfer Session", "Nirmal Babu", "Week 8 (November 17, 2026)", "not_started"),
    ]:
        report = number in ("4.6", "4.7", "4.10")
        deliv.append(dict(number=number, title=title, owner=owner, deadline=due, status=status, task=key,
                          raci=raci("Ram Anandkumar" if report else "Rucheek", "Nirmal Babu", "Rucheek" if report else "Ram Anandkumar", "Priya Nair"),
                          evidence=f"SOW section 4.{number.split('.')[1]}"))
    return dict(
        id="tarnwood", title="Tarnwood AI Analytics", client="Tarnwood Industrial", kickoff=KICKOFF, weeks=8, week_start_day=2,
        budget_hours=760, role_budgets={"AI Engineer": 330, "Technical Director": 190, "Project Manager": 90, "ML Engineer": 70, "QA Engineer": 50, "VMware Engineer (Tarnwood)": 30},
        people=people, tasks=tasks, last_logged_week=5, other_hours={"Nirmal Babu": 3, "Ram Anandkumar": 2, "Rucheek": 2},
        sow=sow, history_meetings=history, drop={"meeting-06.txt": read("meeting-06.txt")}, raid=raid, deliverables=deliv, events=events,
        meetings=[
            dict(title="Week 5 check-in", description="Weekly check-in: report module progress, integration test plan, UAT planning.", start="2026-10-21T10:00:00", end="2026-10-21T11:00:00",
                 status="scheduled", attendees=["Nirmal Babu", "Rucheek", "Ram Anandkumar", "Priya Nair"], reason="Weekly cadence."),
            dict(title="UAT readiness and production access review", description="Confirm UAT participants, the integration test defects and production access before deployment.",
                 start="2026-11-03T10:00:00", end="2026-11-03T10:45:00", status="pending_approval", attendees=["Nirmal Babu", "Rucheek", "Ram Anandkumar", "Priya Nair", "Tobias Lindqvist"],
                 reason="Production access is still outstanding and UAT starts this week; the team agreed to confirm both."),
        ],
    )


if __name__ == "__main__":
    s = spec()
    print(f"{len(s['tasks'])} tasks, est {sum(t['est'] for t in s['tasks'])} h; statuses:",
          {st: sum(1 for t in s['tasks'] if t['status'] == st) for st in ('completed', 'in_progress', 'not_started', 'blocked')})
    print(build_project(s))
