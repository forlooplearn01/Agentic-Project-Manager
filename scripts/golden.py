"""Golden state of the demo projects (apps/utils/golden.py). Run inside the api container:

  python -m scripts.golden status                 each project: golden export present? does the live project match it?
  python -m scripts.golden restore forloop        put one project back exactly as exported   (restore all: every exported project)
  python -m scripts.golden verify all             compare live projects with their exports, change nothing
  python -m scripts.golden export forloop         save the live project as its new golden state (do this only when you mean to)
  python -m scripts.golden drop forloop           copy the project's ready-to-run demo file into the folder it belongs in

A restore does not undo Jira issues or calendar events a demo created."""
import shutil
import sys
import time

from apps.utils import golden
from apps.utils import timesheet_watcher as tw


def ids() -> list:
    return sorted(p.name for p in golden.GOLDEN_DIR.iterdir() if (p / "golden.json").exists()) if golden.GOLDEN_DIR.is_dir() else []


def targets(arg: str) -> list:
    return ids() if arg == "all" else [arg]


def main(argv: list):
    cmd = argv[0] if argv else "status"
    arg = argv[1] if len(argv) > 1 else "all"
    if cmd == "status":
        for pid in ids():
            r = golden.verify_project(pid)
            print(f"{pid:12} {'matches its golden state' if r['ok'] else str(len(r['differences'])) + ' difference(s)'}")
            for d in r["differences"][:6]:
                print("   -", d)
    elif cmd in ("restore", "verify"):
        bad = 0
        if cmd == "restore" and arg == "all":  # a full reset also clears what the demo left in AI Chat and the activity feed
            from apps.db.db import get_connection
            conn = get_connection()
            with conn.cursor() as cur:
                for table in ("chat_messages", "agent_jobs"):
                    cur.execute(f"DELETE FROM {table}")
            conn.commit()
            conn.close()
            print("chat history and activity feed cleared")
        for pid in targets(arg):
            t0 = time.time()
            r = golden.restore_project(pid) if cmd == "restore" else golden.verify_project(pid)
            print(f"{pid:12} {'ok' if r['ok'] else 'DIFFERENT'}  ({time.time() - t0:.1f} s)")
            for d in r["differences"]:
                print("   -", d)
            bad += not r["ok"]
        raise SystemExit(1 if bad else 0)
    elif cmd == "export":
        print(golden.export_project(arg))
    elif cmd == "drop":
        src = golden.GOLDEN_DIR / arg / "drop"
        for f in sorted(src.glob("*")):
            sub = "meetings" if f.suffix in (".txt", ".md", ".vtt", ".srt", ".docx") else "timesheets" if f.name.lower().find("timesheet") >= 0 else "plan"
            dest = tw.ROOT / arg / sub / f.name
            shutil.copy2(f, dest)
            print(f"copied {f.name} -> projects/{arg}/{sub}/")
    else:
        raise SystemExit(__doc__)


main(sys.argv[1:])
