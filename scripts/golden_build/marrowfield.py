"""Demo project `marrowfield`: warehouse (WMS) and carrier integration with an ML arrival-time model and a tracking dashboard, built by Forloop for the
fictional Marrowfield Logistics. JUST KICKED OFF: week 2 of 8, one week of hours logged, the plan and its first meetings in place.
The ready-to-run file is a new version of the plan file (an estimate raised, a new carrier added, two tasks completed, an owner and a finish week
changed): the change history and the numbers move when it is dropped in.

    python -m scripts.golden_build.marrowfield"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from scripts.golden_build.common import build_project

SRC = Path(__file__).resolve().parents[2] / "datasets" / "golden-src" / "marrowfield"
KICKOFF = date(2026, 10, 19)
P1, P2, P3, P4, P5 = "Discovery", "Integration build", "Arrival-time prediction", "Tracking dashboard", "Testing and go-live"
NIRMAL, RAM, RUCHEEK = "Nirmal Babu", "Ram Anandkumar", "Rucheek"
DANA, LUIS, PRIYANKA, MAREK = "Dana Whitfield", "Luis Ortega", "Priyanka Rao", "Marek Novak"


def due(finish: int) -> str:
    return (KICKOFF + timedelta(days=7 * (finish - 1) + 4)).isoformat()


ROWS = [
    ("kick", "Kickoff and access setup", P1, NIRMAL, 1, 1, 8, "completed", [], {}),
    ("disc", "Discovery sessions with Marrowfield warehouse and IT teams", P1, RAM, 1, 2, 16, "completed", [], {}),
    ("apirev", "Review WMS, ERP and carrier API documentation", P1, RUCHEEK, 1, 1, 12, "completed", [], {}),
    ("req", "Write integration requirements and data mapping", P1, RAM, 1, 2, 20, "in_progress", ["disc"], {"priority": "high"}),
    ("arch", "Design integration architecture", P1, RAM, 1, 2, 24, "in_progress", ["disc"], {"priority": "high"}),
    ("wms", "Build WMS connector (inventory and shipment events)", P2, DANA, 1, 4, 48, "in_progress", ["arch"], {"priority": "high"}),
    ("carA", "Integrate carrier A tracking API", P2, RUCHEEK, 1, 4, 30, "in_progress", ["apirev"], {}),
    ("carB", "Integrate carrier B tracking API", P2, RUCHEEK, 3, 5, 30, "not_started", ["carA"], {}),
    ("carC", "Integrate carrier C tracking API", P2, RUCHEEK, 4, 5, 24, "not_started", ["carA"], {}),
    ("erp", "Integrate ERP order feed", P2, LUIS, 3, 5, 36, "not_started", ["wms"], {}),
    ("norm", "Normalise shipment events into a common model", P2, LUIS, 1, 4, 24, "in_progress", ["wms"], {}),
    ("etadata", "Prepare historical shipment data for the arrival-time model", P3, LUIS, 3, 4, 16, "not_started", ["norm"], {}),
    ("eta", "Train and validate the arrival-time model", P3, LUIS, 4, 6, 40, "not_started", ["etadata"], {"priority": "high"}),
    ("etaserve", "Serve arrival-time predictions through the tracking API", P3, DANA, 6, 7, 16, "not_started", ["eta"], {}),
    ("dsgn", "Design the tracking dashboard", P4, PRIYANKA, 1, 3, 16, "in_progress", [], {}),
    ("dash", "Build the tracking dashboard", P4, PRIYANKA, 4, 7, 56, "not_started", ["dsgn", "norm"], {}),
    ("alert", "Late-shipment alerts", P4, PRIYANKA, 6, 7, 16, "not_started", ["etaserve", "dash"], {}),
    ("testplan", "Write test plan", P5, MAREK, 4, 6, 16, "not_started", [], {}),
    ("e2e", "End-to-end and load testing", P5, MAREK, 6, 7, 28, "not_started", ["carC", "erp", "alert"], {}),
    ("uat", "User acceptance testing with Marrowfield operations", P5, NIRMAL, 7, 8, 20, "not_started", ["e2e"], {}),
    ("deploy", "Production deployment", P5, RAM, 8, 8, 16, "not_started", ["uat"], {}),
    ("handover", "Training and handover", P5, NIRMAL, 8, 8, 12, "not_started", ["deploy"], {}),
    ("pm", "Project management and status reporting", P1, NIRMAL, 1, 8, 32, "in_progress", [], {}),
]
LOGGED = {"kick": 8, "disc": 17, "apirev": 13, "req": 11, "arch": 8, "wms": 14, "carA": 9, "norm": 6, "dsgn": 5, "pm": 7}


def read(name: str) -> str:
    return (SRC / name).read_text()


def drop_plan(tasks: list) -> list:
    """The next version of the plan, as the project manager would save it after the week 2 checkpoint."""
    for t in tasks:
        if t["key"] == "wms":
            t["est"] = 60
        elif t["key"] in ("req", "carA"):
            t["status"] = "completed"
        elif t["key"] == "erp":
            t["owner"] = DANA
        elif t["key"] == "eta":
            t["finish"] = 7
    tasks.append(dict(key="carD", text="Integrate carrier D tracking API (new carrier requested by Marrowfield)", phase=P2, owner=RUCHEEK, start=5, finish=6, est=24,
                      status="not_started", deadline=due(6), priority="medium", depends=["carA"], note="Added after the week 2 checkpoint"))
    return tasks


def spec() -> dict:
    tasks = [{**dict(key=k, text=text, phase=ph, owner=o, start=a, finish=b, est=e, status=st, deadline=due(b), priority="medium", depends=dep), **extra}
             for k, text, ph, o, a, b, e, st, dep, extra in ROWS]
    meeting_date = lambda t: datetime.strptime(re.search(r"^Date: \w+ (\d+ \w+ \d{4})", t, re.M).group(1), "%d %B %Y").date()  # noqa: E731
    hist = [(n, read(n)) for n in ("meeting-kickoff.txt", "meeting-w1.txt")]
    people = [
        dict(name=NIRMAL, role="Project Manager", capacity=12, skills=["project management", "stakeholder management", "change control"]),
        dict(name=RAM, role="Solutions Architect", capacity=16, skills=["architecture", "integration", "wms", "devops"]),
        dict(name=RUCHEEK, role="Integration Engineer", capacity=30, skills=["python", "apis", "integration", "carrier apis"]),
        dict(name=DANA, role="Senior Backend Engineer", capacity=36, skills=["backend", "python", "wms", "apis"]),
        dict(name=LUIS, role="Data Engineer", capacity=32, skills=["data engineering", "sql", "machine learning", "etl"]),
        dict(name=PRIYANKA, role="Frontend Engineer", capacity=30, skills=["react", "typescript", "dashboards", "frontend"]),
        dict(name=MAREK, role="QA Engineer", capacity=24, skills=["qa", "test automation", "load testing"]),
    ]
    raid = [
        dict(type="dependency", title="WMS API credentials and test environment must arrive by the end of Week 2", owner=NIRMAL, status="open", probability="high", impact="high",
             description="The WMS connector cannot be tested without credentials; Marrowfield's IT lead is being chased.", evidence="Kickoff meeting and Week 1 review."),
        dict(type="issue", title="WMS vendor documentation is out of date", owner=RAM, status="mitigating", probability="high", impact="medium",
             description="Several endpoints differ from the documents; each will be checked against the test environment.", evidence="Week 1 review."),
        dict(type="dependency", title="Test accounts for carriers B and C needed by Week 3", owner=NIRMAL, status="open", description="Carrier integrations B and C start in weeks 3 and 4.",
             evidence="Kickoff meeting."),
        dict(type="risk", title="Carrier API rate limits may slow tracking updates", owner=RUCHEEK, status="open", probability="medium", impact="medium",
             description="Polling many shipments could hit rate limits; webhooks or batching may be needed.", evidence="Carrier API review."),
        dict(type="risk", title="Quality of the historical shipment data for the arrival-time model is unknown", owner=LUIS, status="open", probability="medium", impact="high",
             description="The model depends on past shipment records; gaps would reduce accuracy.", evidence="SOW phase 3."),
        dict(type="assumption", title="Three carriers at launch", owner=NIRMAL, status="open", description="A fourth carrier would be a change request.", evidence="SOW section 5."),
        dict(type="assumption", title="One warehouse at go-live", owner=NIRMAL, status="open", description="Additional sites are out of scope.", evidence="SOW section 5."),
        dict(type="risk", title="Peak-season shipment volumes could stress the connector", owner=DANA, status="open", probability="low", impact="high", review_state="suggested",
             description="Load testing in weeks 6 and 7 must cover peak volumes.", evidence="Testing phase."),
    ]
    deliv = [dict(number=str(n), title=t, owner=o, deadline=f"Week {w}", status=st, task=k, evidence=f"SOW section 4, deliverable {n}",
                  raci={o: "R", NIRMAL: "A", RAM: "C"} if o != NIRMAL else {o: "A", RAM: "C"}) for n, t, o, w, st, k in [
        (1, "Integration Requirements and Data Mapping", RAM, 2, "in_progress", "req"), (2, "Integration Architecture", RAM, 2, "in_progress", "arch"),
        (3, "WMS and ERP Connectors", DANA, 5, "in_progress", "wms"), (4, "Carrier Integrations", RUCHEEK, 5, "in_progress", "carA"),
        (5, "Shipment Arrival-time Model", LUIS, 6, "not_started", "eta"), (6, "Tracking Dashboard", PRIYANKA, 7, "not_started", "dash"),
        (7, "Test Report and UAT Sign-off", MAREK, 8, "not_started", "e2e"), (8, "Go-live and Handover", NIRMAL, 8, "not_started", "handover")]]
    return dict(
        id="marrowfield", title="Marrowfield Warehouse and Carrier Integration", client="Marrowfield Logistics", kickoff=KICKOFF, weeks=8, week_start_day=0,
        budget_hours=620, role_budgets={"Project Manager": 60, "Solutions Architect": 90, "Integration Engineer": 110, "Senior Backend Engineer": 110, "Data Engineer": 120,
                                        "Frontend Engineer": 80, "QA Engineer": 50},
        people=people, tasks=tasks, logged=LOGGED, last_logged_week=1, other_hours={NIRMAL: 3, RAM: 2, RUCHEEK: 1},
        sow=read("sow.txt"), history_meetings=[(n, t, meeting_date(t)) for n, t in hist], drop={}, drop_plan=drop_plan,
        raid=raid, deliverables=deliv,
        events=[dict(task=k, doc="sow", status="not_started") for k in ("req", "arch", "wms", "carA", "dash")] +
               [dict(task=k, doc="meeting-w1.txt", status="in_progress", match="exact") for k in ("req", "arch", "wms", "carA", "norm")] +
               [dict(task=k, doc="meeting-w1.txt", status="completed", match="exact") for k in ("disc", "apirev")],
        meetings=[
            dict(title="Kickoff meeting", description="Scope, timeline, budget and access.", start="2026-10-19T10:00:00", end="2026-10-19T11:00:00", status="scheduled",
                 attendees=[NIRMAL, RAM, RUCHEEK, DANA, LUIS], reason="Project kickoff."),
            dict(title="Week 2 checkpoint: WMS access and carrier test accounts", description="Confirm the WMS credentials, the test environment and the carrier B and C test accounts.",
                 start="2026-11-03T10:00:00", end="2026-11-03T10:30:00", status="pending_approval", attendees=[NIRMAL, RAM, RUCHEEK, DANA],
                 reason="The WMS credentials are overdue and the carrier test accounts are needed next week."),
        ],
    )


if __name__ == "__main__":
    s = spec()
    print(f"{len(s['tasks'])} tasks, est {sum(t['est'] for t in s['tasks'])} h")
    print(build_project(s))
