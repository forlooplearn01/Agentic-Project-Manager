"""Rehearses the demo of each project's ready-to-run file, then puts the project back to its golden state.

  python -m scripts.rehearse_demo all                    one run of every project
  python -m scripts.rehearse_demo halvane --runs 10      the same drop ten times (how stable is what the model suggests?)
  python -m scripts.rehearse_demo tarnwood --approve     also approve every suggestion and check the plan file

Each run: restore the project, copy its file into the right folder, wait for the app to read it, check the result against what the demo should
show, optionally approve, restore again and verify. Calendar events are never created here (the pending meeting is not approved), and
the projects other than forloop have no Jira key, so nothing leaves the app."""
import json
import sys
import time
import urllib.request
from collections import Counter
from difflib import SequenceMatcher

from apps.utils import golden
from apps.utils import timesheet_watcher as tw

API = "http://localhost:8000"
WAIT = 240


def call(method, path, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read() or b"{}")


def drop(pid: str):
    import shutil
    src = golden.GOLDEN_DIR / pid / "drop"
    for f in sorted(src.glob("*")):
        sub = "meetings" if f.suffix in (".txt", ".md") else "timesheets" if "timesheet" in f.name.lower() else "plan"
        shutil.copy2(f, tw.ROOT / pid / sub / f.name)


def wait_for(pid: str, kind: str, before: int):
    """Until a new file record of this kind appears for the project."""
    deadline = time.time() + WAIT
    while time.time() < deadline:
        files = call("GET", f"/projects/{pid}/folders")
        recs = files["plan"]["files"] if kind == "plan" else files["meetings"]["files"]
        if len(recs) > before and (kind == "plan" or recs[0]["status"] != "applied" or recs[0]["summary"].get("state") == "done"):
            return recs[0]  # a meeting is recorded when reading starts; it is finished when its state says done
        time.sleep(2)
    return None


def has(s, text, **kw):
    """A suggestion whose task name contains `text` and whose change / fields match kw (field -> expected 'to' value, substring for text)."""
    if text.lower() not in s["task_text"].lower():
        return False
    for field, want in kw.items():
        got = (s["changes"].get(field) or {}).get("to") if field in s["changes"] else s["fields"].get(field)
        if got is None or str(want).lower() not in str(got).lower():
            return False
    return True


# ---------------------------------------------------------------- what each demo should show

def check_halvane(sugs):
    return {
        "KYC task unblocked (status in progress)": any(has(s, "ID-verification", status="in_progress") and s["kind"] == "change" for s in sugs),
        "Notifications task moves to Elise": any(has(s, "Email and SMS", owner="Elise") and s["kind"] == "change" for s in sugs),
        "Test suite task completed": any(has(s, "test plan and automated", status="completed") for s in sugs),
        "Fraud model finishes in week 10": any(has(s, "fraud pre-screen model", finish_week=10) for s in sugs),
        "New task: SMS one-time passcode login (Elise, week 9)": any(s["kind"] == "new_task" and any(w in s["task_text"].lower() for w in ("passcode", "otp", "one-time")) and
                                                                    "elise" in str(s["fields"].get("owner", "")).lower() and s["fields"].get("finish_week") == 9 for s in sugs),
        "Nothing suggested for the gateway task (still blocked)": not any("payment gateway" in s["task_text"].lower() for s in sugs),
    }


TARNWOOD_CONCEPTS = {  # what meeting 6 commits people to, as the words that would appear in a suggested task (any of them)
    "fixes for the integration test defects": ("scatter", "pie chart", "two-pass", "stat key"),
    "UAT feedback form": ("feedback form",), "UAT participant guide sent": ("participant guide",), "consolidated UAT feedback": ("consolidated",),
    "regression test of the fixes": ("regress", "retest"), "production access package": ("production access", "access package", "access instructions"),
    "deployment runbook": ("runbook",), "production server Python version": ("python",),
}


def check_tarnwood(sugs):
    new = [s["task_text"].lower() for s in sugs if s["kind"] == "new_task"]
    found = [c for c, words in TARNWOOD_CONCEPTS.items() if any(w in t for t in new for w in words)]
    return {"New tasks cover the meeting (at least 6 of its 8 topics)": len(found) >= 6, "All 8 topics covered": len(found) == 8,
            "Whitelist approval marked completed": any(has(s, "whitelist", status="completed") and s["kind"] == "change" for s in sugs),
            "No more than 25 suggestions (no repeats)": len(sugs) <= 25}


def check_marrowfield(_sugs):
    changes = call("GET", "/projects/marrowfield/plan/changes")
    rows = changes.get("changes", changes)
    text = " | ".join(f"{r['label']}:{r.get('task_text')}:{r.get('old_value')}>{r.get('new_value')}" for r in rows)
    return {
        "Estimate raised on the WMS connector (48 to 60)": "Estimate:Build WMS connector (inventory and shipment events):48>60" in text,
        "Requirements and carrier A completed": text.count("Status:") >= 2 and "completed" in text,
        "ERP feed moves to Dana": "Owner:Integrate ERP order feed" in text and "Dana Whitfield" in text,
        "Arrival-time model finish week moves to 7": "Finish week:Train and validate the arrival-time model:6>7" in text,
        "New task: carrier D": "Task added:Integrate carrier D" in text,
    }


def check_forloop(_sugs):
    h = call("GET", "/projects/forloop/hours")
    return {"463 h logged, 237 h left, 737 h forecast": (h["logged_hours"], h["hours_left_in_budget"], h["forecast_at_completion"]) == (463.0, 237.0, 737.0)}


def check_halvane_followup(pid):
    """The transcript asks everyone to reconvene 'this Friday at 2 pm Eastern': a pending meeting on a Friday at 14:00 with the three real people on it."""
    from datetime import datetime
    real = {"nirmalbabu14@gmail.com", "ram@shanthee.com", "rucheekrk28@gmail.com"}
    pending = [m for m in call("GET", f"/projects/{pid}/meetings")["meetings"] if m["status"] == "pending_approval"]
    proposed = [m for m in pending if m["title"] != "Escalation: ID verification and payment gateway access"]
    when = [datetime.fromisoformat(m["start_iso"]) for m in proposed]
    return {"A follow-up meeting is proposed (new, pending approval)": len(proposed) == 1,
            "...on a Friday at 14:00, still in the future": len(when) == 1 and when[0].weekday() == 4 and (when[0].hour, when[0].minute) == (14, 0) and when[0] > datetime.now(),
            "...with Nirmal, Ram and Rucheek invited": len(proposed) == 1 and real <= set(proposed[0]["attendee_emails"])}


EXTRA_CHECKS = {"halvane": check_halvane_followup}  # checks that look at more than the suggestions

PROJECTS = {"forloop": ("timesheet", check_forloop), "tarnwood": ("meeting", check_tarnwood), "halvane": ("meeting", check_halvane), "marrowfield": ("plan", check_marrowfield)}


def run_once(pid: str, approve: bool) -> dict:
    kind, check = PROJECTS[pid]
    golden.restore_project(pid)
    folders = call("GET", f"/projects/{pid}/folders")
    before = len(folders["plan"]["files"] if kind == "plan" else folders["meetings"]["files"])
    t0 = time.time()
    drop(pid)
    if kind == "timesheet":
        for _ in range(WAIT // 2):
            if call("GET", f"/projects/{pid}/hours")["logged_hours"] > 400:
                break
            time.sleep(2)
        rec = {"status": "applied"}
    else:
        rec = wait_for(pid, "plan" if kind == "plan" else "meeting", before)
    took = time.time() - t0
    sugs = call("GET", f"/projects/{pid}/suggestions")["suggestions"] if kind == "meeting" else []
    result = {"read": bool(rec and rec["status"] in ("applied",)), "seconds": round(took), "suggestions": len(sugs), "checks": {**check(sugs), **(EXTRA_CHECKS[pid](pid) if pid in EXTRA_CHECKS else {})} if rec else {}}
    if approve and kind == "meeting" and sugs:
        out = call("POST", f"/projects/{pid}/suggestions/approve", {"ids": [s["suggestion_id"] for s in sugs]})
        states = Counter(s["state"] for s in call("GET", f"/projects/{pid}/suggestions")["suggestions"])
        result["approve"] = {**out, "states": dict(states)}
    verify = golden.restore_project(pid)
    result["restored_ok"] = verify["ok"]
    result["restore_differences"] = verify["differences"]
    return result


def main(argv):
    ids = [a for a in argv if not a.startswith("--")] or ["all"]
    ids = list(PROJECTS) if ids == ["all"] else ids
    runs = int(argv[argv.index("--runs") + 1]) if "--runs" in argv else 1
    ids = [i for i in ids if i in PROJECTS]
    approve = "--approve" in argv
    ok = True
    for pid in ids:
        tally, restored = Counter(), 0
        for n in range(1, runs + 1):
            r = run_once(pid, approve)
            restored += r["restored_ok"]
            for name, passed in r["checks"].items():
                tally[name] += bool(passed)
            if runs == 1 or not r["read"] or not r["restored_ok"]:
                print(f"{pid} run {n}: read={r['read']} in {r['seconds']} s, {r['suggestions']} suggestions, restored={r['restored_ok']}", r.get("approve", ""), r["restore_differences"] or "")
            ok &= r["read"] and r["restored_ok"]
        print(f"\n{pid}: {runs} run(s), restored exactly {restored}/{runs}")
        for name, n in tally.items():
            print(f"   {n}/{runs}  {name}")
            ok &= n == runs or runs > 1
        print()
    raise SystemExit(0 if ok else 1)


main(sys.argv[1:])
