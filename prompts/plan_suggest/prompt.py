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
Return only JSON: {{"items": [{{"task_number": <number from the plan list, or null for a new task>, "task_text": "<only for a new task: a short task name starting with a verb>", "owner": "<full name from the people list, or null>", "status": "not_started" | "in_progress" | "blocked" | "completed" | "on_hold" | null, "deadline": "<the date as said in the meeting, or null>", "finish_week": <the plan week number when the meeting names one ("Week 10"), else null>, "blocker": "<what is blocking it, or null>", "quote": "<the sentence from the transcript this is based on, copied exactly, at most 200 characters>", "confidence": "high" | "medium" | "low"}}]}}

### Rules
- Only what the transcript actually says. Never invent a task, an owner, a status or a date.
- An item for an EXISTING task (use its number) only when the transcript says something that changes it: someone new takes it over, it is done, started, blocked or on hold, or it gets a new date. A task that was only mentioned, or whose owner, status and date the transcript merely repeats, gets no item.
- A NEW task (task_number null) only for work that is not in the plan and that someone commits to or is asked to do. Not for discussion, background or work that is already finished.
- Two tasks are the SAME piece of work only if finishing one would finish the other. If the transcript is about the same work as a plan task under
  different words, use that task's number. But a concrete step towards a bigger plan task (a fix, a review, a hand-over, a brief, a report, a
  draft, a regression test) is a NEW task even when it is part of a deliverable: bigger plan tasks such as "Deliverable 4.6: ..." are umbrellas,
  and their status is changed only when the transcript says the whole deliverable itself is done, started or late.
- A date or a status stated for a concrete step belongs to that step, never to the umbrella task above it.
- Do not repeat yourself: one item per task and per thing said.
- owner: the person who will do the work, written exactly as in the people list when they are on it. If the transcript gives no owner, null.
- status: only when the transcript says so. "completed" when the work is finished, delivered, approved, received, confirmed or resolved (for example "we
  got the approval", "the access was granted", "marking it resolved"); "in_progress" when it has started; "blocked" when something is stopping it. Otherwise null.
  Look for these in the whole transcript, including answers to questions and risks being closed: each one is an item for the plan task it is about.
- deadline: only a date the meeting states or implies ("by 13 November", "next Friday"). Otherwise null.
- If nothing in the transcript changes the plan, return {{"items": []}}.

### Transcript
{transcript}
"""
