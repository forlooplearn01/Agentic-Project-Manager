# Golden demo projects

Four projects, each loaded once, frozen as a golden export, and restorable to exactly that state in seconds. Run everything inside the api container
(`docker compose exec api python -m ...`).

| Project | Client (fictional) | Story | The one ready-to-run file (`<project>/drop/`) |
|---|---|---|---|
| `forloop` | Calderwick Retail Group | Week 4 of 8, **Watch**: VPN blocked, forecast 9 h over; Jira key FLP | `forloop-timesheet-week5.csv` into `projects/forloop/timesheets/`: 363 to 463 h logged, 237 h left, forecast 737 h |
| `tarnwood` | Tarnwood Industrial | AI analytics on VMware data, week 6 of 8, **On track**; 53 tasks; weeks run Wed to Tue | `meeting-06.txt` (a meeting) into `projects/tarnwood/meetings/`: about a dozen suggestions |
| `halvane` | Halvane Financial | Customer onboarding portal, week 7 of 12, **At risk**: two blocked tasks, a change request, a stretched front-end team | `drop-week7-sync.txt` into `projects/halvane/meetings/`: five suggestions (status, owner, week, completion, one new task) AND a proposed follow-up meeting (this Friday 2:00 PM Eastern, 30 min, the three real people), waiting in Scheduled meetings. Nothing reaches the calendar until you approve it |
| `marrowfield` | Marrowfield Logistics | Warehouse and carrier integration, just kicked off (week 2 of 8), **On track** | `marrowfield-plan.xlsx` into `projects/marrowfield/plan/` (a new version of the plan): history and numbers move |

All names are invented (searched for real companies with the same names: none found). The three real people (Nirmal Babu, Ram Anandkumar, Rucheek) are on every
project with their email; every other person is simulated and has none. Each project has one meeting waiting for approval (halvane gets a second one when its demo file is dropped): approving it creates a REAL calendar
event and sends invites to those three, so do it once to see it work, not on every rehearsal.

## Commands

```
python -m scripts.golden status            does each live project match its golden state?
python -m scripts.golden restore all       put every project (and clear chat history and the activity feed) back to golden
python -m scripts.golden restore halvane   one project
python -m scripts.golden drop halvane      copy the ready-to-run file into its folder (what you do in front of a client)
python -m scripts.rehearse_demo all        the whole demo for each project, then restore and verify
python -m scripts.rehearse_demo halvane --runs 10   the same drop ten times (stability of what the model suggests)
python -m scripts.chat_check all           the 10 AI Chat questions of every project, asked to the real chat
python -m scripts.golden export halvane    save the live project as the new golden state (only when you mean to)
```

A restore does not undo Jira issues or calendar events. Rehearse with `PLAN_JIRA_SYNC=0` (forloop is the only project with a Jira key).

## What is where
- `<project>/golden.json` and `files/`: the export (rows of every table, the plan / meeting / timesheet files, a manifest of key numbers). Real email addresses are NOT in it:
  they are `<<email:Name>>`, filled in from `emails.local.json` (gitignored, made by an export). On a new machine with no such file, a restore uses `first.last@example.com` placeholders, so nothing real can be emailed; to use real addresses create the file by hand as `{"Name": "address"}` (a file that exists but misses someone is still an error).
- `<project>/drop/`: the ready-to-run file. `<project>/questions.json`: the 10 chat questions with answers worked out from the data.
- `../golden-src/<project>/`: the source texts (SOW, meetings) the build used. `tarnwood` is made from `../simulation-ai-x-vmware` by `scripts/golden_build/transform_sim.py`.
- Rebuild a project from its spec: `python -m scripts.golden_build.<project>` (then re-export). `python -m scripts.golden_build.questions` regenerates the questions.
- The demo date is frozen at 2026-10-29 (`DEMO_TODAY` in docker-compose.yml; empty = real date), so "who is free next week" and "what is late" never drift.
