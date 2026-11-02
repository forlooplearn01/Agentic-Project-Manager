# Prompt for turning a meeting transcript into suggested changes to the project plan. The model sees the plan's tasks (numbered) and the
# project's people, so it can point at an existing task by number instead of the app guessing from wording, and it must quote the sentence
# each suggestion is based on so the PMO can check it. Nothing it returns is applied without the PMO's approval.
def get_plan_suggest_prompt(transcript: str, tasks: list, people: list, today: str) -> str:
    plan = "\n".join(
        f"{i + 1}. {t['task_text']} | owner: {t.get('owner') or 'none'} | status: {t.get('status') or 'not_started'}"
        + (f" | finish week: {t['plan_finish_week']}" if t.get("plan_finish_week") else "")
        for i, t in enumerate(tasks)
    ) or "(the plan has no tasks yet)"
    names = ", ".join(people) or "(none listed)"
    return f"""
You read the transcript of a project meeting and propose changes to the project plan. A project manager will approve or reject each one.

Today is {today}.

### People on the project
{names}

### The plan's tasks (numbered)
{plan}

### What to return
Return only JSON: {{"items": [{{"task_number": <number from the plan list, or null for a new task>, "task_text": "<only for a new task: a short task name starting with a verb>", "owner": "<full name from the people list, or null>", "status": "not_started" | "in_progress" | "blocked" | "completed" | "on_hold" | null, "deadline": "<the date as said in the meeting, or null>", "blocker": "<what is blocking it, or null>", "quote": "<the sentence from the transcript this is based on, copied exactly, at most 200 characters>", "confidence": "high" | "medium" | "low"}}]}}

### Rules
- Only what the transcript actually says. Never invent a task, an owner, a status or a date.
- An item for an EXISTING task (use its number) only when the transcript says something that changes it: someone new takes it over, it is done, started, blocked or on hold, or it gets a new date. A task that was only mentioned, or whose owner, status and date the transcript merely repeats, gets no item.
- A NEW task (task_number null) only for work that is not in the plan and that someone commits to or is asked to do. Not for discussion, background or work that is already finished.
- If the transcript is about something that is in the plan under different words, use that task's number instead of creating a new task.
- owner: the person who will do the work, written exactly as in the people list when they are on it. If the transcript gives no owner, null.
- status: only when the transcript says so ("done", "resolved", "started", "blocked"). Otherwise null.
- deadline: only a date the meeting states or implies ("by 13 November", "next Friday"). Otherwise null.
- If nothing in the transcript changes the plan, return {{"items": []}}.

### Transcript
{transcript}
"""
