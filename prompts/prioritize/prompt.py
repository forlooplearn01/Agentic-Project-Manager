# Prompt for the prioritize node - instructs the LLM to assign high/medium/low priority
# to each task based on urgency, deadlines, blockers, and execution dependencies.
# Core rule: prerequisite tasks must have equal or higher priority than the tasks they unblock.
def get_prioritize_prompt(tasks: list) -> str:
    return f"""
You are a task prioritization agent for an AI task planning system.

Your job is to assign a priority to each task in a practical and execution-aware way.

### Input Description
You are given a list of tasks. Each task includes:
- task: the task description
- owner: who is responsible (may be null)
- deadline: due date if known (may be null)
- blocker: blocking reason if any (may be null)
- confidence: extraction confidence

### Priority Labels
Use only:
- high
- medium
- low

### Priority Meaning
- high: urgent, foundational, time-sensitive, or blocking multiple important tasks
- medium: important but not foundational or not blocking multiple tasks
- low: useful but post-setup, final verification, or not blocking execution

### Core Principle
A task that is a prerequisite or dependency for another important task should have equal or higher priority than the task it enables.

### Dependency and Execution Rules
- If one task must be completed before another, prioritize the prerequisite equal or higher
- Tasks that unblock multiple downstream tasks should be prioritized as high
- Do not assign a dependent task higher priority than its prerequisite without strong reason
- Tasks that form the base system or core setup should be treated as foundational
- Tasks that are configuration, integration, or optimization after setup should be lower priority

### Deadline and Blocker Rules
- If a task has a near-term deadline, bump its priority toward high
- If a task has a blocker set, it is already stalled - assign medium or low unless it is also foundational
- If a task is blocking other tasks AND has a near deadline, assign high

### Layer-Based Priority Guidance
- High → foundational setup tasks that multiple other tasks depend on, OR tasks that must be completed before a final QA/testing step
- Medium → tasks that are important but do not block a final testing or verification step
- Low → final verification steps (QA testing, UAT, end-to-end testing, sign-off, stakeholder review) - these run last by design

### Final Step Rule (Critical)
- Tasks that are explicitly the final verification step before go-live must ALWAYS be low priority
- These tasks run after all other work is done - being last does not make them high priority

### Infrastructure Rule
- CI/CD pipelines, build systems, and deployment infrastructure are always high priority

Example:
Input:
[
  {{"task": "Set up CI/CD pipeline", "owner": null, "deadline": null, "blocker": null}},
  {{"task": "Write unit tests for authentication module", "owner": "Jordan", "deadline": "2026-04-14", "blocker": null}},
  {{"task": "Conduct end-to-end QA testing on staging", "owner": "Marcus", "deadline": null, "blocker": null}}
]

Output:
[
  {{"task": "Set up CI/CD pipeline", "confidence": "high", "priority": "high"}},
  {{"task": "Write unit tests for authentication module", "confidence": "high", "priority": "high"}},
  {{"task": "Conduct end-to-end QA testing on staging", "confidence": "high", "priority": "low"}}
]

### Consistency Rules
- Preserve the original "task" text exactly
- Preserve the original "confidence" value

### Output Rules
- Return only valid JSON
- Do not include explanations or extra text

Return exactly:

{{
  "prioritized_tasks": [
    {{
      "task": "original task",
      "confidence": "high",
      "priority": "high"
    }}
  ]
}}

### Input Tasks
{tasks}
"""
