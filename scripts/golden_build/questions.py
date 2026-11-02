"""The 10 AI Chat questions of each demo project, with the answer worked out from the project's own loaded data (never typed in by hand).
Writes datasets/golden/<id>/questions.json: [{q, expect_all: [...], expect_any: [...], tool}] - an answer passes when it contains every
expect_all text and at least one expect_any text (case ignored). Run after a project is built or changed:

    python -m scripts.golden_build.questions"""
import json
from datetime import timedelta

from apps.db.db import get_connection, list_deliverables, list_raid_items
from apps.db.hours_db import availability_report, project_hours_report
from apps.db.plan_db import plan_summary
from apps.utils import clock, golden

STATUS = {"on_track": "on track", "watch": "watch", "at_risk": "at risk"}
# per project: skill for the availability question, the SOW question and its expected words, the "blocked / open" question
SPECIFIC = {
    "forloop": dict(skill="QA", sow=("According to the SOW, how long is the forloop engagement?", [], ["8-week", "8 week", "eight"]),
                    blocked=("What is blocked on forloop and why?", [], ["VPN"])),
    "tarnwood": dict(skill="machine learning", sow=("What does the tarnwood SOW require the report generation module's PDF to contain?", [], ["cover page", "table of contents"]),
                     blocked=("Which tasks on tarnwood are still in progress?", [], ["Report Generation Module", "whitelist", "integration test plan"])),
    "halvane": dict(skill="QA", sow=("When does the Halvane portal launch according to the SOW, and what is the budget?", ["920"], ["4 December", "December 4", "Week 12"]),
                    blocked=("What is blocked on halvane and why?", [], ["ID-verification", "payment gateway", "sandbox"])),
    "marrowfield": dict(skill="data", sow=("How many carriers does the Marrowfield project integrate at launch, and what happens if a fourth is wanted?", [], ["three", "3", "change request"]),
                        blocked=("What dependencies could delay marrowfield?", [], ["WMS", "credentials", "test accounts"])),
}


def build(pid: str) -> list:
    sp = SPECIFIC[pid]
    report = project_hours_report(pid)
    status = STATUS[report["burn_check"]["status"]]
    left = f"{report['hours_left_in_budget']:g}"
    start = clock.today()
    avail = [p for p in availability_report(pid, start, start + timedelta(days=27), sp["skill"])["people"] if p["available_hours"] and p["available_hours"] > 0][:3]
    risks = [r for r in list_raid_items(pid) if r["review_state"] == "confirmed" and r["item_type"] in ("risk", "issue")]
    delivs = list_deliverables(pid, states=("confirmed",))
    plan = plan_summary(pid)
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT title FROM meetings WHERE project_id = %s ORDER BY start_iso", (pid,))
        meetings = [r[0] for r in cur.fetchall()]
        cur.execute("""SELECT a.task_text, b.task_text FROM task_dependencies d JOIN tasks a ON a.task_id = d.task_id JOIN tasks b ON b.task_id = d.depends_on_task_id
                       WHERE a.project_id = %s AND a.is_active AND b.is_active ORDER BY a.created_at, 1 LIMIT 1""", (pid,))
        dep = cur.fetchone()
    conn.close()
    def words(text, n=3):  # the first few words of a task name, enough to recognise it in an answer
        return " ".join(text.replace("(", " ").split()[:n])
    changed = plan["change_hours"] not in (None, 0) or plan["tasks_added"]
    qs = [
        dict(q=f"How many hours are left in the {pid} budget?", expect_all=[left], expect_any=[], tool="get_project_hours"),
        dict(q=f"Is the {pid} project on track?", expect_all=[], expect_any=[status], tool="get_project_hours"),
        dict(q=f"Who is available for {sp['skill']} work on {pid} in the next four weeks?", expect_all=[], expect_any=[p["name"].split()[0] for p in avail], tool="find_available_people"),
        dict(q=sp["blocked"][0], expect_all=sp["blocked"][1], expect_any=sp["blocked"][2], tool="search_tasks / get_project_risks"),
        dict(q=f"What are the main risks on {pid}?", expect_all=[], expect_any=[words(r["title"], 2) for r in risks[:4]], tool="get_project_risks"),
        dict(q=f"What deliverables does {pid} have and who is accountable or responsible for them?", expect_all=[], expect_any=[d["title"].split()[0] for d in delivs[:6]], tool="get_project_deliverables"),
        dict(q=f"Has the {pid} plan changed since the baseline?", expect_all=[],
             expect_any=([f"{plan['change_hours']:g}"] + [f"{plan['baseline_hours']:g}"] if changed else ["no change", "not changed", "hasn't changed", "has not changed", "unchanged", f"{plan['baseline_hours']:g}"]),
             tool="get_plan_changes"),
        dict(q=sp["sow"][0], expect_all=sp["sow"][1], expect_any=sp["sow"][2], tool="search_documents"),
        dict(q=f"What meetings are scheduled for {pid}?", expect_all=[], expect_any=[words(m, 2) for m in meetings], tool="get_project_meetings"),
    ]
    if dep:
        qs.append(dict(q=f'What does the task "{dep[0]}" depend on in {pid}?', expect_all=[], expect_any=[words(dep[1], 3)], tool="get_project_dependencies"))
    return qs


def main():
    for pid in SPECIFIC:
        qs = build(pid)
        (golden.GOLDEN_DIR / pid / "questions.json").write_text(json.dumps(qs, indent=1) + "\n")
        print(f"{pid}: {len(qs)} questions")
        for q in qs:
            print("   ", q["q"][:92], "->", (q["expect_all"] + q["expect_any"])[:4])


if __name__ == "__main__":
    main()
