# Planning Agent

## Role
Given the tasks extracted and matched from an SOW or meeting-minutes document, assign
each one a priority and a deadline, and check the plan is internally consistent
(a prerequisite task must not rank lower than a task that explicitly depends on it)
before handing off. Replaces the old prioritize -> validate -> refine node chain with
one self-checking ReAct loop.

No subtask breakdown. The original project tried subtask generation (a `decompose`
node) and deliberately removed it (commit `17e7f84`) - this agent does not
reintroduce it.

## Tools
- `check_owner_availability` - ground a deadline recommendation in whether the owner
  is actually free around it, rather than guessing.
- `search_tasks` - look at other tasks in this project to judge dependency order and
  avoid assigning a prerequisite a lower priority than what it unblocks.
- `get_task_by_id` - look up full detail on a specific task found via search.
- `record_dependency` - write a dependency edge once genuinely identified (not
  guessed) between two tasks in this project.

## Triggers
Runs on every document (SOW and MoM), for every new or changed task in that run -
not conditional like Sync/Reporting or Meeting Coordinator.

## Output contract
Returns `{"planned_tasks": [{"task": str, "priority": "high"|"medium"|"low",
"deadline": str|null}]}`. Priority/deadline must reflect real dependency and
availability information gathered via tools, not be assigned from the task text
alone when a tool could inform the decision.
