# Synthetic hours data for the forloop project

Made to test the planned Hours feature (plan estimates, timesheet import, hours-left and availability answers in AI Chat).
Everything is generated from one table of ground-truth hours by
[`scripts/generate_forloop_hours_dataset.py`](../../../scripts/generate_forloop_hours_dataset.py), so every layout adds up to the same
totals. Re-running the script rewrites identical files. Task texts, people and roles are the real ones from the forloop
SOW and database; the hours, budget and capacities are invented.

Story, matching the Week 3 sync minutes: kickoff Monday 2026-09-28, hours cover Weeks 1-4. ETL, CRM sync, schema and data
mapping are done, the core layout finishes in Week 4, VPN access is blocked and keeps accruing hours past its estimate, and
Returns & Refunds was added as a change request.

| File | Layout | What it tests |
|---|---|---|
| `forloop-plan.xlsx` | Title rows above the header; Plan sheet (22 tasks, WBS ids, estimate hours, owner, start/finish week, Status at the end of Week 4) and Budget sheet (700 h total, by role) | Header detection; task names worded differently from the database; a plan-only task (project management); owner "Team"; two database tasks with no estimate (Follow up, Review VPN) |
| `forloop-timesheet-flat.csv` | One row per entry: week ending, employee, project, activity, hours. Elena, Marcus, Sofia, Nirmal, Rucheek | Free-text activities; names written as "Ruiz, Elena" and "M. Chen"; decimal hours; blank project; a TOTAL footer row to ignore; "Admin" / meetings time that matches no task |
| `forloop-timesheet-grid.xlsx` | One sheet per person, a Mon-Fri grid per week with totals. Priya, David, Ram | Stacked weekly blocks; day columns that must be summed; hours not tied to a task |
| `forloop-timesheet-summary.xlsx` | Person by week, no task detail. Everyone | The "project-level hours only" case; cross-check against the other two |
| `forloop-timesheet-week5.csv` | One more week (Week 5, 100 h), one row per entry, everyone, clearly worded labels | The "one more timesheet arrives" step of a live demo (copy it into `projects/forloop/timesheets/`; there is no upload button): after it, 463 h logged, 237 h left in budget, forecast 737 h (37 h over); the derivation is in the expected file and asserted in `tests/unit/test_hours.py`. Not part of the base load; `--existing` clears it and moves the file out of `projects/forloop/timesheets/` |
| `forloop-meeting-week5.txt` | A Week 5 sync transcript | The "meeting arrives" step of a live demo: copy it into `projects/forloop/meetings/`. Suggestions: Review VPN access resolution completed, Inventory Turnover finishing week 7 (in progress), a new task "data migration runbook" (Marcus Chen, week 8), QA test plans in progress. Approving writes them into the plan file. Approving the new task creates a Jira issue under the key FLP unless the API runs with `PLAN_JIRA_SYNC=0` |
| `forloop-hours-expected.json` | The answers | Budget, per-task estimate / actual / remaining, label-to-task mapping, forecast, per-person weekly hours |

**Use the flat CSV and the grid together** (they cover different people, so they add up to everything). The summary file
repeats the same hours at person level, so importing it as well would double count; use it on its own.

To try the whole flow through the real API (plan, then both timesheets, each previewed and confirmed): 
`docker compose exec api python -m scripts.seed_forloop_hours_demo` loads it into a disposable demo project `forloop-hours-demo`.
`docker compose exec api python -m scripts.seed_forloop_hours_demo forloop --existing` loads it onto the LIVE `forloop` project (the golden
client-demo dataset): re-runnable, keeps the tasks, sets statuses to the end-of-Week-4 state and applies the answer key's task wherever the
importer's proposal differs (the reviewer step). It also resets the plan: baseline and change history cleared and set again by the plan import,
tasks added by hand in the Plan view deleted, tasks removed there restored. See the script's docstring and DECISIONS.md.
`eval/nodes/eval_hours_import.py` measures how well the importer matches the labels in these files to the right task.

Expected at the end of Week 4: 363 h logged of a 700 h budget (337 left), 346 h of estimated work still open, forecast 709 h
(9 h over budget). 90 h are not tied to any task. Two open tasks are over their estimate (VPN access 16 vs 12, QA test plans
14 vs 12) and one open task has no estimate (Review VPN access resolution). Burn check as of 2026-10-23: 51.9% of the budget used,
44.3% of the work due by now, 43.1% done, so on schedule but 9 h over budget in the forecast (status Watch).

The statuses in the expected file are the assumed state at the end of Week 4. The live `forloop` rows were all `not_started`; the
`--existing` loader sets them to this state (originals saved under `logs/`). Automated tests never use the live project: they seed a
disposable one from these files.

Plain numbers are written for all totals, because a spreadsheet written by `openpyxl` has no cached formula results and
an importer would read the totals back empty.
