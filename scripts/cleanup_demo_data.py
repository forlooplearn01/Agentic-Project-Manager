"""Removes every project except the ones to keep, and the chat and activity history, from the database and from projects/.

  python -m scripts.cleanup_demo_data forloop            dry run: prints what would be deleted
  python -m scripts.cleanup_demo_data forloop --yes      does it (take a database dump first: docker compose exec postgres pg_dump ...)

The folder watcher is paused while it runs (apps/utils/timesheet_watcher.pause), so it cannot rebuild a project from its folder halfway.
Test code, eval code and datasets are not touched; only data."""
import shutil
import sys

from apps.db.db import get_connection
from apps.utils import timesheet_watcher as tw

args = [a for a in sys.argv[1:] if not a.startswith("--")]
KEEP = args or ["forloop"]
DO = "--yes" in sys.argv
WIPE_ALL = ("chat_messages", "agent_jobs", "unresolved_events")  # history from past test runs: wiped whatever the project


def main():
    if DO and not tw.pause():
        tw.resume()
        raise SystemExit("The folder watcher is still scanning; try again in a minute.")
    try:
        run()
    finally:
        if DO:
            tw.resume()


def run():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' AND column_name = 'project_id' ORDER BY 1")
    tables = [r[0] for r in cur.fetchall()]
    report = []
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {t} WHERE project_id IS NULL OR project_id <> ALL(%s)", (KEEP,))
        report.append((t, cur.fetchone()[0]))
    for t in WIPE_ALL:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        report.append((t + " (all rows)", cur.fetchone()[0]))
    for name, n in report:
        if n:
            print(f"  {name}: {n}")
    folders = [p for p in tw.ROOT.iterdir() if p.is_dir() and p.name not in KEEP and not p.name.startswith(".")]
    print("  folders:", ", ".join(sorted(p.name for p in folders)) or "none")
    if not DO:
        print("\nDry run. Nothing was deleted. Add --yes to delete.")
        return
    # events point at documents and tasks: they go first
    cur.execute("""DELETE FROM task_events WHERE source_doc_id IN (SELECT doc_id FROM source_documents WHERE project_id IS NULL OR project_id <> ALL(%s))
                   OR task_id IN (SELECT task_id FROM tasks WHERE project_id IS NULL OR project_id <> ALL(%s))""", (KEEP, KEEP))
    for t in tables:
        cur.execute(f"DELETE FROM {t} WHERE project_id IS NULL OR project_id <> ALL(%s)", (KEEP,))
    for t in WIPE_ALL:
        cur.execute(f"DELETE FROM {t}")
    # rows that hang off the deleted ones
    cur.execute("DELETE FROM task_dependencies WHERE task_id NOT IN (SELECT task_id FROM tasks) OR depends_on_task_id NOT IN (SELECT task_id FROM tasks)")
    cur.execute("DELETE FROM task_events WHERE task_id NOT IN (SELECT task_id FROM tasks)")
    cur.execute("DELETE FROM task_raci WHERE task_id NOT IN (SELECT task_id FROM tasks)")
    cur.execute("DELETE FROM deliverable_raci WHERE deliverable_id NOT IN (SELECT deliverable_id FROM deliverables)")
    conn.commit()
    cur.close()
    conn.close()
    for p in folders:
        shutil.rmtree(p)
    shutil.rmtree(tw.ROOT / tw.ARCHIVE, ignore_errors=True)
    print("\nDone.")


main()
