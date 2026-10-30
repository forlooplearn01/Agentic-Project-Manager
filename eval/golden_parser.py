"""Parses the two golden-output formats in datasets/simulation-ai-x-vmware/expected-outputs/
- the SOW format (numbered tasks with Owner/Deadline/Priority/Subtasks) and the MoM format
(NEW TASKS / UPDATE EVENTS / SHOULD NOT BE PICKED sections). Deliberately doesn't parse
Subtasks or UPDATE EVENTS' exact notes/dependency text into structured fields - those aren't
modeled as first-class concepts in this app (no subtask hierarchy, no per-event note field),
so there's nothing meaningful to compare them against. What's parsed is exactly what this
app's pipeline actually produces and can be checked against: task text, owner, deadline,
priority for new tasks, plus the "should not be picked" text for a false-positive check."""
import re
from dataclasses import dataclass, field


@dataclass
class GoldenTask:
    text: str
    owner: str = None
    deadline: str = None
    priority: str = None


@dataclass
class GoldenSow:
    task_count: int
    tasks: list


@dataclass
class GoldenMeeting:
    new_tasks: list
    should_not_be_picked: list = field(default_factory=list)
    # "UPDATE EVENTS" text - mentions that refer to an already-existing task and should be
    # recognized as a continuation (match_node should find them), never created as a new
    # task. Previously discarded entirely; now parsed because eval/nodes/eval_match.py needs
    # exactly this as its "should match something existing" ground truth, and it's already
    # present in every expected-output file - no new golden data to author.
    update_events: list = field(default_factory=list)


def parse_sow(path: str) -> GoldenSow:
    text = open(path).read()

    count_match = re.search(r"TASK COUNT:\s*\n(\d+)", text)
    task_count = int(count_match.group(1)) if count_match else None

    tasks_section = re.search(r"TASKS:\s*\n(.*?)\n\nEVENTS:", text, re.DOTALL)
    body = tasks_section.group(1) if tasks_section else ""

    # Split on top-level numbered items only (e.g. "1. " or "10. "), not sub-bullets
    # ("- ...") - re.split with a lookahead keeps each block starting at its own number.
    blocks = re.split(r"\n(?=\d+\.\s)", body.strip())

    tasks = []
    for block in blocks:
        title_match = re.match(r"\d+\.\s+(.+)", block)
        if not title_match:
            continue
        owner_match = re.search(r"Owner:\s*(.+)", block)
        deadline_match = re.search(r"Deadline:\s*(.+)", block)
        priority_match = re.search(r"Priority:\s*(.+)", block)
        tasks.append(GoldenTask(
            text=title_match.group(1).strip(),
            owner=owner_match.group(1).strip() if owner_match else None,
            deadline=deadline_match.group(1).strip() if deadline_match else None,
            priority=priority_match.group(1).strip().lower() if priority_match else None,
        ))

    return GoldenSow(task_count=task_count, tasks=tasks)


def parse_meeting(path: str) -> GoldenMeeting:
    text = open(path).read()

    new_tasks_section = re.search(
        r"--- NEW TASKS.*?---\s*\n(.*?)\n\n\n--- UPDATE EVENTS", text, re.DOTALL,
    )
    new_tasks = []
    if new_tasks_section:
        body = new_tasks_section.group(1).strip()
        if body and "none" not in body.lower()[:20]:
            blocks = re.split(r"\n(?=\d+\.\s*\n)", body)
            for block in blocks:
                task_match = re.search(r"task:\s*(.+)", block)
                owner_match = re.search(r"owner:\s*(.+)", block)
                deadline_match = re.search(r"deadline:\s*(.+)", block)
                if task_match:
                    new_tasks.append(GoldenTask(
                        text=task_match.group(1).strip(),
                        owner=owner_match.group(1).strip() if owner_match else None,
                        deadline=deadline_match.group(1).strip() if deadline_match else None,
                    ))

    update_events_section = re.search(
        r"--- UPDATE EVENTS.*?---\s*\n(.*?)\n\n\n--- SHOULD NOT BE PICKED", text, re.DOTALL,
    )
    update_events = []
    if update_events_section:
        body = update_events_section.group(1).strip()
        if body and "none" not in body.lower()[:20]:
            blocks = re.split(r"\n(?=\d+\.\s*\n)", body)
            for block in blocks:
                task_match = re.search(r"task:\s*(.+)", block)
                owner_match = re.search(r"owner:\s*(.+)", block)
                if task_match:
                    update_events.append(GoldenTask(
                        text=task_match.group(1).strip(),
                        owner=owner_match.group(1).strip() if owner_match else None,
                    ))

    # "SHOULD NOT BE PICKED" is free-form prose with category headers and a mix of
    # quoted/unquoted lines - extracted as a flat list of non-empty, non-header lines
    # for a best-effort leak check, not structured parsing.
    should_not_section = re.search(r"--- SHOULD NOT BE PICKED.*?---\s*\n(.*)", text, re.DOTALL)
    should_not_lines = []
    if should_not_section:
        for line in should_not_section.group(1).splitlines():
            stripped = line.strip().strip("-").strip().strip('"').strip()
            if stripped and not stripped.endswith(":"):
                should_not_lines.append(stripped)

    return GoldenMeeting(new_tasks=new_tasks, should_not_be_picked=should_not_lines, update_events=update_events)


@dataclass
class GoldenDeliverable:
    title: str
    subtasks: list = field(default_factory=list)


def parse_sow_hierarchy(path: str) -> list:
    """The SOW golden file's top-level tasks (the deliverables) each with their Subtasks list - the answer key for
    "which deliverable does this task belong to". parse_sow() deliberately ignores subtasks; this reads only them."""
    text = open(path).read()
    tasks_section = re.search(r"TASKS:\s*\n(.*?)\n\nEVENTS:", text, re.DOTALL)
    body = tasks_section.group(1) if tasks_section else ""
    out = []
    for block in re.split(r"\n(?=\d+\.\s)", body.strip()):
        title = re.match(r"\d+\.\s+(.+)", block)
        if not title:
            continue
        subs = re.search(r"Subtasks:\s*\n(.*)", block, re.DOTALL)
        items = [m.strip() for m in re.findall(r"^\s*-\s+(.+)$", subs.group(1), re.M)] if subs else []
        out.append(GoldenDeliverable(title=title.group(1).strip(), subtasks=items))
    return out
