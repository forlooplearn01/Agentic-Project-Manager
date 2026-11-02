"""Demo project `halvane`: a customer onboarding portal with identity checks, payments and an ML fraud pre-screen, built by Forloop for the
fictional Halvane Financial. Week 7 of 12, AT RISK: two tasks blocked on third parties' credentials, estimates raised, one change request
(open banking), the front-end people over capacity, the forecast over budget. Weeks run Monday to Sunday.
The ready-to-run file is the Week 7 sync transcript (a status change, an owner change, a new finish week, a completion and one new task).

    python -m scripts.golden_build.halvane"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from scripts.golden_build.common import build_project

SRC = Path(__file__).resolve().parents[2] / "datasets" / "golden-src" / "halvane"
KICKOFF = date(2026, 9, 14)
P1, P2, P3, P4, P5, P6 = ("Discovery and design", "Core onboarding", "Payments", "Fraud pre-screen", "Testing and compliance", "Launch")
NIRMAL, RAM, RUCHEEK = "Nirmal Babu", "Ram Anandkumar", "Rucheek"
ELISE, KOFI, ANA, JONAS, HYE = "Elise Marchetti", "Kofi Mensah", "Ana Beltrán", "Jonas Weber", "Hye-jin Park"


def due(finish: int) -> str:
    return (KICKOFF + timedelta(days=7 * (finish - 1) + 4)).isoformat()


# key, text, phase, owner, start, finish, est, status, extras
ROWS = [
    ("kick", "Kickoff, environment and access setup", P1, NIRMAL, 1, 1, 10, "completed", {}),
    ("disc", "Discovery workshops with Halvane operations and compliance", P1, HYE, 1, 2, 24, "completed", {}),
    ("req", "Write onboarding and KYC requirements specification", P1, HYE, 2, 3, 28, "completed", {}),
    ("arch", "Design onboarding platform architecture and data model", P1, ELISE, 2, 3, 32, "completed", {}),
    ("ux", "Design onboarding user flows and screen mockups", P1, KOFI, 2, 4, 36, "completed", {}),
    ("api", "Build onboarding API (account creation, document upload, status)", P2, RUCHEEK, 3, 7, 70, "in_progress", {"est_baseline": 58}),
    ("fe", "Build onboarding web front end", P2, KOFI, 4, 8, 76, "in_progress", {"est_baseline": 66}),
    ("docs", "Document upload and secure storage", P2, ELISE, 4, 6, 30, "completed", {}),
    ("kyc", "Integrate ID-verification service (KYC checks)", P2, RUCHEEK, 5, 8, 46, "blocked",
     {"blocker": "The ID-verification vendor has not granted production API access", "priority": "high"}),
    ("notif", "Email and SMS notifications for onboarding status", P2, KOFI, 7, 9, 18, "not_started", {}),
    ("gw", "Integrate payment gateway (sandbox)", P3, RUCHEEK, 5, 7, 40, "blocked",
     {"blocker": "The payment gateway provider has not issued sandbox credentials", "priority": "high"}),
    ("ledger", "Build payment ledger and reconciliation job", P3, ELISE, 6, 9, 46, "in_progress", {}),
    ("payui", "Payment method screens and 3-D Secure flow", P3, KOFI, 7, 9, 36, "not_started", {}),
    ("ob", "Open banking account linking (CR-01)", P3, ELISE, 7, 10, 52, "not_started", {"cr": True, "priority": "high", "note": "Change request CR-01 raised 21 Oct"}),
    ("feat", "Prepare fraud-signal features from sample application data", P4, ANA, 6, 7, 24, "in_progress", {}),
    ("model", "Train and validate fraud pre-screen model", P4, ANA, 7, 9, 40, "not_started", {"priority": "high"}),
    ("serve", "Serve the model behind the onboarding API with fallbacks", P4, ANA, 8, 9, 24, "not_started", {}),
    ("rules", "Define manual-review rules with Halvane risk team", P4, HYE, 7, 8, 14, "not_started", {}),
    ("testplan", "Write test plan and automated test suite", P5, JONAS, 5, 9, 36, "in_progress", {}),
    ("e2e", "End-to-end and load testing", P5, JONAS, 9, 10, 30, "not_started", {}),
    ("sec", "Security review and penetration test remediation", P5, RAM, 9, 11, 24, "not_started", {"priority": "high"}),
    ("comp", "Compliance sign-off pack (data protection, audit trail)", P5, HYE, 9, 11, 22, "not_started", {"priority": "high"}),
    ("uat", "User acceptance testing with Halvane operations", P5, HYE, 10, 11, 24, "not_started", {}),
    ("deploy", "Production deployment and cutover plan", P6, RAM, 11, 12, 24, "not_started", {}),
    ("train", "Operations training and handover", P6, NIRMAL, 11, 12, 14, "not_started", {}),
    ("hyper", "Launch support and hypercare", P6, RUCHEEK, 12, 12, 18, "not_started", {}),
    ("pm", "Project management and status reporting", P1, NIRMAL, 1, 12, 56, "in_progress", {}),
]
LOGGED = {"kick": 11, "disc": 27, "req": 32, "arch": 36, "ux": 41, "docs": 34, "api": 52, "fe": 40, "kyc": 5, "gw": 6, "ledger": 8, "feat": 12, "testplan": 10, "pm": 28}


def read(name: str) -> str:
    return (SRC / name).read_text()


def tasks() -> list:
    out = []
    for key, text, phase, owner, start, finish, est, status, extra in ROWS:
        out.append({**dict(key=key, text=text, phase=phase, owner=owner, start=start, finish=finish, est=est, status=status, deadline=due(finish),
                           priority="medium", depends=[]), **extra})
    deps = {"api": ["arch"], "fe": ["ux"], "kyc": ["api"], "gw": ["api"], "ledger": ["gw"], "payui": ["fe"], "model": ["feat"], "serve": ["model", "rules"],
            "e2e": ["testplan", "api"], "uat": ["e2e"], "deploy": ["uat", "comp"], "train": ["deploy"]}
    for t in out:
        t["depends"] = deps.get(t["key"], [])
    return out


def meeting_date(text: str) -> date:
    return datetime.strptime(re.search(r"^Date: \w+ (\d+ \w+ \d{4})", text, re.M).group(1), "%d %B %Y").date()


def spec() -> dict:
    hist = [(f"meeting-{w}.txt", read(f"meeting-{w}.txt")) for w in ("w3", "w5", "w6")]
    people = [
        dict(name=NIRMAL, role="Delivery Manager", capacity=12, skills=["delivery management", "stakeholder management", "change control"]),
        dict(name=RAM, role="Technical Director", capacity=16, skills=["architecture", "security", "payments", "devops"]),
        dict(name=RUCHEEK, role="Backend and Payments Engineer", capacity=32, skills=["python", "apis", "payments", "kyc", "backend"]),
        dict(name=ELISE, role="Engineering Lead", capacity=36, skills=["architecture", "backend", "python", "data modelling", "payments"]),
        dict(name=KOFI, role="Frontend Engineer", capacity=36, skills=["react", "typescript", "frontend", "ux"]),
        dict(name=ANA, role="ML Engineer", capacity=32, skills=["machine learning", "python", "fraud detection", "data science"]),
        dict(name=JONAS, role="QA Engineer", capacity=30, skills=["qa", "test automation", "load testing"]),
        dict(name=HYE, role="Business Analyst", capacity=32, skills=["requirements", "compliance", "uat", "documentation"]),
    ]
    raid = [
        dict(type="issue", title="Payment gateway sandbox credentials not provided", owner=RUCHEEK, status="mitigating", probability="high", impact="high",
             description="The gateway provider has not issued sandbox credentials, so payment integration cannot start. Ram is escalating with the provider's account manager.",
             evidence="Weekly syncs, weeks 5 and 6: sandbox credentials still outstanding."),
        dict(type="issue", title="ID-verification vendor has not granted production API access", owner=RAM, status="mitigating", probability="high", impact="high",
             description="KYC integration is blocked until the vendor grants production access; Nirmal escalated through Halvane's Head of Digital Onboarding.",
             evidence="Weekly syncs, weeks 3, 5 and 6."),
        dict(type="risk", title="Open banking scope added without moving the launch date", owner=NIRMAL, status="open", probability="high", impact="high",
             description="CR-01 adds about 52 hours of work while the launch date and the 920 hour budget are unchanged.", evidence="Week 6 sync: change request CR-01 raised."),
        dict(type="risk", title="Front-end team over capacity in weeks 7 to 9", owner=ELISE, status="mitigating", probability="high", impact="medium",
             description="Kofi carries the front end, notifications and payment screens at the same time; some work is being moved to Elise.", evidence="Week 6 sync."),
        dict(type="risk", title="Fraud model may not reach the target precision on the sample data", owner=ANA, status="open", probability="medium", impact="high",
             description="Anonymised sample data is limited; early results may need more validation time.", evidence="Fraud pre-screen scope in the SOW."),
        dict(type="dependency", title="Halvane risk team must approve the manual-review rules before the model is served", owner=HYE, status="open",
             description="The model cannot be released to production without agreed review rules.", evidence="SOW phase 4."),
        dict(type="dependency", title="Compliance sign-off is required before production deployment", owner=HYE, status="open", review_state="suggested",
             description="Deployment cannot be scheduled until the sign-off pack is approved.", evidence="SOW section 5."),
        dict(type="assumption", title="Halvane provides anonymised sample application data by Week 5", owner=NIRMAL, status="closed",
             description="Delivered in Week 5.", evidence="Week 5 sync."),
        dict(type="assumption", title="Single country at go-live", owner=NIRMAL, status="open", description="Multi-country launch would be a change request.", evidence="SOW section 5."),
        dict(type="risk", title="Penetration test findings could push the deployment date", owner=RAM, status="open", probability="medium", impact="medium", review_state="suggested",
             description="Remediation time is not yet in the plan.", evidence="Security review task, weeks 9 to 11."),
    ]
    deliv = []
    for number, title, owner, due_w, status, key in [
        ("1", "Requirements Specification", HYE, 3, "completed", "req"), ("2", "Architecture and Data Model", ELISE, 3, "completed", "arch"),
        ("3", "Onboarding Portal (API and web front end)", RUCHEEK, 8, "in_progress", "api"), ("4", "Payments Integration", RUCHEEK, 9, "blocked", "gw"),
        ("5", "Fraud Pre-screen Model", ANA, 9, "not_started", "model"), ("6", "Test Report", JONAS, 11, "not_started", "e2e"),
        ("7", "Compliance Sign-off Pack", HYE, 11, "not_started", "comp"), ("8", "Operations Handover", NIRMAL, 12, "not_started", "train")]:
        deliv.append(dict(number=number, title=title, owner=owner, deadline=f"Week {due_w}", status=status, task=key, evidence=f"SOW section 4, deliverable {number}",
                          raci={owner: "R", NIRMAL: "A", RAM: "C", HYE: "I"} if owner != NIRMAL else {owner: "A", RAM: "C", HYE: "I"}))
    return dict(
        id="halvane", title="Halvane Customer Onboarding Portal", client="Halvane Financial", kickoff=KICKOFF, weeks=12, week_start_day=0,
        budget_hours=920, role_budgets={"Delivery Manager": 80, "Technical Director": 80, "Backend and Payments Engineer": 190, "Engineering Lead": 150, "Frontend Engineer": 140,
                                        "ML Engineer": 90, "QA Engineer": 70, "Business Analyst": 120},
        people=people, tasks=tasks(), logged=LOGGED, last_logged_week=6, other_hours={NIRMAL: 4, RAM: 3, RUCHEEK: 2},
        sow=read("sow.txt"), history_meetings=[(n, t, meeting_date(t)) for n, t in hist], drop={"drop-week7-sync.txt": read("drop-week7-sync.txt")},
        raid=raid, deliverables=deliv, cr_date=date(2026, 10, 21),
        events=[dict(task=k, doc="sow", status="not_started") for k in ("req", "arch", "ux", "api", "fe")] +
               [dict(task="kyc", doc="meeting-w5.txt", status="blocked", blocker="The ID-verification vendor has not granted production API access", match="exact"),
                dict(task="gw", doc="meeting-w5.txt", status="blocked", blocker="The payment gateway provider has not issued sandbox credentials", match="exact"),
                dict(task="req", doc="meeting-w3.txt", status="completed", match="exact"), dict(task="arch", doc="meeting-w3.txt", status="completed", match="exact"),
                dict(task="docs", doc="meeting-w5.txt", status="completed", match="exact")],
        meetings=[
            dict(title="Week 6 sync", description="Weekly sync: change request CR-01, blockers and capacity.", start="2026-10-21T10:00:00", end="2026-10-21T10:45:00", status="scheduled",
                 attendees=[NIRMAL, RAM, RUCHEEK, ELISE, KOFI, HYE], reason="Weekly cadence."),
            dict(title="Escalation: ID verification and payment gateway access", description="Agree the escalation to the vendor and the gateway provider, and decide what to move off the front-end team.",
                 start="2026-11-03T10:00:00", end="2026-11-03T10:30:00", status="pending_approval", attendees=[NIRMAL, RAM, RUCHEEK, ELISE, KOFI],
                 reason="Two third-party blockers and an over-stretched front-end team put the launch date at risk."),
        ],
    )


if __name__ == "__main__":
    s = spec()
    print(f"{len(s['tasks'])} tasks, est {sum(t['est'] for t in s['tasks'])} h; baseline est {sum(t['est_baseline'] if t.get('est_baseline') else t['est'] for t in s['tasks'] if not t.get('cr'))} h")
    print(build_project(s))
