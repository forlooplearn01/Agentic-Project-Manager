"""A meeting transcript becomes SUGGESTIONS for the PMO, never changes to the plan.

One model call reads the transcript against the plan's numbered tasks (prompts/plan_suggest/prompt.py), and nothing is written to the tasks
(no Jira, no calendar, no registers): each thing the meeting says about the plan becomes either
  - a "new_task" suggestion (nothing in the plan matches it), or
  - a "change" suggestion (it matches a task in the plan and the meeting says something different: another owner, a new status, a new
    finish date), listing only the fields that would actually change.
The PMO approves or rejects each; approved ones are merged into the plan file (apps/utils/plan_merge.py). DECISIONS.md "Plan and meetings by folder"."""
import io
import json
import re
from datetime import date

from apps.db.hours_db import fetch_active_tasks_for_hours, get_hours_settings
from apps.db.suggestions_db import OPEN_STATES, create_suggestion, list_suggestions
from apps.logger import get_logger
from apps.utils.hours import week_start
from apps.utils.hours_import import HoursImportError, map_status, resolve_person

logger = get_logger("MEETING_SUGGEST")

TRANSCRIPT_SUFFIXES = (".txt", ".md", ".vtt", ".srt", ".docx")
MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024
_TIMESTAMP = re.compile(r"^\s*(?:\d+\s*)?\d{1,2}:\d{2}(?::\d{2})?[.,]?\d*\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?[.,]?\d*.*$")


def read_transcript(filename: str, content: bytes) -> str:
    """The text of a transcript file (.txt, .md, .vtt, .srt, .docx); subtitle timestamps and cue numbers are dropped."""
    if len(content) > MAX_TRANSCRIPT_BYTES:
        raise HoursImportError("The transcript is larger than 2 MB.")
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in TRANSCRIPT_SUFFIXES:
        raise HoursImportError("A transcript should be a .txt, .md, .vtt, .srt or .docx file.")
    if ext == ".docx":
        try:
            from docx import Document
            text = "\n".join(p.text for p in Document(io.BytesIO(content)).paragraphs)
        except Exception as e:
            raise HoursImportError("The Word file could not be opened.") from e
    else:
        for enc in ("utf-8-sig", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    if ext in (".vtt", ".srt"):
        lines = [l for l in text.splitlines() if l.strip() and not _TIMESTAMP.match(l) and not re.fullmatch(r"\s*\d+\s*", l) and l.strip().upper() != "WEBVTT"]
        text = "\n".join(lines)
    text = text.strip()
    if len(text) < 20:
        raise HoursImportError("The transcript is empty or too short to read.")
    return text


def finish_week_of(deadline, kickoff, start_day: int):
    """An ISO date -> the plan's week number (week 1 = the week of kickoff); None when it cannot be worked out."""
    if not deadline or not kickoff:
        return None
    try:
        d = date.fromisoformat(str(deadline)[:10])
    except ValueError:
        return None
    n = (week_start(d, start_day) - week_start(kickoff, start_day)).days // 7 + 1
    return n if n >= 1 else None


def _norm(t) -> str:
    return re.sub(r"[^\w\s]", "", (t or "").lower()).strip()


def _ask(prompt: str) -> dict:
    """One model call that must come back as JSON (fences around it are tolerated). Raises when it does not."""
    from apps.llm import complete
    response = complete(messages=[{"role": "user", "content": prompt}], temperature=0, response_format={"type": "json_object"})
    raw = (response.choices[0].message.content or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        return json.loads(raw)
    except ValueError as e:
        raise HoursImportError("The model's answer could not be read as suggestions.") from e


def ask_model(text: str, tasks: list, people: list, today: date = None) -> list:
    """One model call per chunk of the transcript, shown the plan's numbered tasks and the people on the project.
    -> items [{task_id | None, task_text, owner, status, deadline (ISO or as said), blocker, quote, confidence}]. Nothing is applied."""
    from apps.nodes.extract import CHUNK_THRESHOLD, _chunk_text
    from apps.utils.deadline import resolve_deadline
    from prompts.plan_suggest.prompt import get_plan_suggest_prompt
    today = today or date.today()
    chunks = [text] if len(text) <= CHUNK_THRESHOLD else _chunk_text(text)
    plan_tasks = tasks[:150]  # a plan longer than this is shown by its first tasks only
    items = []
    for chunk in chunks:
        result = _ask(get_plan_suggest_prompt(chunk, plan_tasks, people, today.isoformat()))  # a failure stops the file: it is not "no suggestions"
        for raw in result.get("items", []) if isinstance(result, dict) else []:
            if not isinstance(raw, dict):
                continue
            num = raw.get("task_number")
            task = plan_tasks[num - 1] if isinstance(num, int) and 1 <= num <= len(plan_tasks) else None
            name = (raw.get("task_text") or "").strip()
            if task is None and name:  # said to be new, but it is the plan's own wording: it is that task
                task = next((t for t in plan_tasks if _norm(t["task_text"]) == _norm(name)), None)
            if task is None and not name:
                continue
            deadline = (raw.get("deadline") or "").strip() or None
            if deadline:
                deadline = resolve_deadline(deadline, today)
            conf = raw.get("confidence") if raw.get("confidence") in ("high", "medium", "low") else "medium"
            items.append({"task_id": str(task["task_id"]) if task else None, "task_text": task["task_text"] if task else name,
                          "owner": (raw.get("owner") or "").strip() or None, "status": raw.get("status"), "deadline": deadline,
                          "blocker": (raw.get("blocker") or "").strip() or None, "quote": (raw.get("quote") or "").strip()[:240] or None, "confidence": conf})
    return items


def build_suggestions(items: list, tasks: list, kickoff, start_day: int = 0, people: list = None) -> list:
    """What the meeting said (ask_model's items) + the plan's current tasks -> suggestion dicts ({kind, task_text, task_id, fields, changes,
    confidence}). A change lists only fields that differ from the plan; nothing is suggested when the meeting just repeats what the plan says.
    Owners are written as the project's roster writes them ('David' -> 'David Osei')."""
    by_id = {str(t["task_id"]): t for t in tasks}
    out = []
    for e in items:
        text = (e.get("task_text") or "").strip()
        if not text:
            continue
        status = map_status(e.get("status")) or ("blocked" if e.get("blocker") else None)
        owner = (e.get("owner") or "").strip() or None
        if owner and people:
            owner = resolve_person(owner, people) or owner
        week = finish_week_of(e.get("deadline"), kickoff, start_day)
        evidence = {"evidence": e["quote"]} if e.get("quote") else {}
        task = by_id.get(str(e.get("task_id"))) if e.get("task_id") else None
        if e.get("task_id") and task is None:
            continue  # points at a task that is no longer in the plan
        if task is None:
            fields = {k: v for k, v in {"owner": owner, "status": status or "not_started", "finish_week": week, "deadline": e.get("deadline"),
                                         "blocker": e.get("blocker"), **evidence}.items() if v is not None}
            out.append({"kind": "new_task", "task_text": text, "task_id": None, "fields": fields, "changes": {}, "confidence": e.get("confidence")})
            continue
        changes = {}
        if owner and owner.lower() != (task.get("owner") or "").lower():
            changes["owner"] = {"from": task.get("owner"), "to": owner}
        if status and status != (task.get("status") or "not_started"):
            changes["status"] = {"from": task.get("status") or "not_started", "to": status}
        if week and week != task.get("plan_finish_week"):
            changes["finish_week"] = {"from": task.get("plan_finish_week"), "to": week}
        if changes:
            out.append({"kind": "change", "task_text": task["task_text"], "task_id": str(task["task_id"]),
                        "fields": {**({"blocker": e["blocker"]} if e.get("blocker") else {}), **evidence}, "changes": changes, "confidence": e.get("confidence")})
    return out


def _is_open_duplicate(s: dict, existing: list) -> bool:
    for x in existing:
        if x["state"] in OPEN_STATES and x["kind"] == s["kind"] and _norm(x["task_text"]) == _norm(s["task_text"]) and x["changes"] == s["changes"]:
            return True
    return False


def suggest_from_text(project_id: str, text: str, source_name: str, file_id: str = None) -> dict:
    """Read a transcript against the project's plan and store the suggestions. -> {"suggestions": n, "new": n, "changes": n, "mentions": n}.
    No task is created or changed, nothing goes to Jira or the calendar."""
    from apps.db.db import list_project_people
    settings = get_hours_settings(project_id)
    tasks = fetch_active_tasks_for_hours(project_id)
    people = [p["name"] for p in list_project_people(project_id)]
    items = ask_model(text, tasks, people)
    suggestions = build_suggestions(items, tasks, settings.get("kickoff_date"), int(settings.get("week_start_day") or 0), people)
    existing = list_suggestions(project_id, states=OPEN_STATES)
    made = []
    for s in suggestions:
        if _is_open_duplicate(s, existing):
            continue
        made.append(create_suggestion(project_id, s["kind"], s["task_text"], s["fields"], s["changes"], s["task_id"], file_id, source_name, s["confidence"]))
    logger.info(f"{project_id}/{source_name}: {len(made)} suggestion(s) from {len(items)} thing(s) the meeting said about the plan")
    return {"suggestions": len(made), "new": sum(1 for m in made if m["kind"] == "new_task"), "changes": sum(1 for m in made if m["kind"] == "change"),
            "mentions": len(items)}
