export type Priority = "high" | "medium" | "low";
export type Status = "not_started" | "in_progress" | "completed" | "blocked" | "on_hold";

export interface ProjectRollup {
  project_id: string;
  total_tasks: number;
  blocked_tasks: number;
  completed_tasks: number;
  in_progress_tasks: number;
  open_tasks: number;
  high_priority_tasks: number;
  unique_owners: number;
  last_updated: string | null;
}

export interface TaskRef {
  task_id: string;
  task_text: string | null;
}

export interface ProjectPerson {
  name: string;
  // null when auto-added as a task owner but not yet resolved to a real address -
  // see ensure_person_placeholder in apps/db/db.py.
  email: string | null;
  role: string | null;
  // Hours: what the person can give this project per week, and what they are good at (see apps/utils/hours.py).
  weekly_capacity_hours?: number | null;
  skills?: string[];
}

export interface Task {
  task_id: string;
  task_text: string;
  owner: string | null;
  status: Status | null;
  deadline: string | null;
  blocker: string | null;
  priority: Priority;
  updated_at: string;
  depends_on: TaskRef[];
  blocks: TaskRef[];
  // Set when the matcher couldn't tell whether this is a new task or a repeat of an existing
  // one, so it created a new task and flagged the existing one for a person to resolve.
  possible_duplicate_of?: TaskRef | null;
  // The deliverable this task contributes to (null = not assigned).
  deliverable?: { deliverable_id: string; number: string | null; title: string } | null;
  // Hours (same rules as the Hours page): remaining_hours is null when it cannot be known (open task with no estimate).
  estimate_hours?: number | null;
  logged_hours?: number;
  remaining_hours?: number | null;
  hours_still_needed?: number | null;
  plan_start_week?: number | null;
  plan_finish_week?: number | null;
  // The plan (Workspace > Plan): the phase it sits in, the baseline it started from, and whether it was added after that baseline.
  phase?: string | null;
  baseline_estimate_hours?: number | null;
  baseline_start_week?: number | null;
  baseline_finish_week?: number | null;
  added_after_baseline?: boolean;
}

export interface TraceEntry {
  tool: string;
  args: Record<string, unknown>;
  result: string;
}

export interface AgentTrace {
  agent: "planning" | "sync_reporting" | "meeting_coordinator";
  triggered: boolean;
  trace: TraceEntry[];
  scheduled_meeting?: Record<string, unknown> | null;
}

export interface JobTraceResponse {
  status: string;
  document_type?: string;
  agents: AgentTrace[];
}

export interface RunResult {
  tasks_count: number;
  tasks: { task: string; priority: Priority; deadline: string | null }[];
  event_signals: Record<string, unknown>[];
  unresolved_events: Record<string, unknown>[];
  document_type?: string;
  status: string;
}

export interface JobPollResponse {
  status: "running" | "completed" | "failed";
  result?: RunResult;
  error?: string;
  from_cache?: boolean;
}

export interface ActivityEvent {
  job_id: string;
  project_id: string | null;
  agent: "planning" | "sync_reporting" | "meeting_coordinator";
  tool: string;
  result: string;
  created_at: string;
}

export type MeetingStatus = "pending_approval" | "scheduled" | "rejected";

export interface Meeting {
  meeting_id: string;
  project_id: string;
  title: string;
  description: string | null;
  attendee_emails: string[];
  unresolved_attendees: string[];
  start_iso: string;
  end_iso: string;
  status: MeetingStatus;
  reason: string | null;
  google_event_id: string | null;
  meeting_link: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  // The data the assistant looked at for this answer (only known for answers received in this browser session).
  trace?: TraceEntry[];
}

export type RaidType = "risk" | "assumption" | "issue" | "dependency";
export type RaidStatus = "open" | "mitigating" | "closed";
export type RaidLevel = "low" | "medium" | "high";
export type RaidReviewState = "suggested" | "confirmed" | "rejected";

export interface RaidItem {
  item_id: string;
  project_id: string;
  item_type: RaidType;
  title: string;
  description: string | null;
  owner: string | null;
  status: RaidStatus;
  probability: RaidLevel | null;
  impact: RaidLevel | null;
  review_date: string | null;
  // "suggested" = proposed by the assistant, waiting for a person to accept or reject it.
  review_state: RaidReviewState;
  // document = imported from a SOW's numbered Assumptions / Dependencies list
  origin: "manual" | "agent" | "document";
  // Set on a risk that happened: the Issue it was promoted to (the risk is closed).
  promoted_to: string | null;
  // The sentence in the source document that backs an item the assistant suggested (checked verbatim by the server).
  evidence: string | null;
  created_at: string;
  updated_at: string;
}

export type RaciRole = "R" | "A" | "C" | "I";
export type RaciWarning = "multiple_accountable" | "no_responsible" | "no_accountable";

export interface RaciAssignment {
  person: string;
  roles: RaciRole[];
  // true = not assigned yet, inferred from the task's owner (shown as a Responsible).
  implied: boolean;
}

// One RACI row: a deliverable (default) or a task - the matrix works the same way for both.
export interface RaciRow {
  subject_id: string;
  number: string | null; // deliverable number such as "4.6"; always null for tasks
  title: string;
  owner: string | null;
  status: string | null;
  assignments: RaciAssignment[];
  warnings: RaciWarning[];
}

export interface RaciMatrix {
  people: string[];
  rows: RaciRow[];
}

export type RaciSubject = "deliverable" | "task";

export interface Deliverable {
  deliverable_id: string;
  project_id: string;
  number: string | null;
  title: string;
  owner: string | null;
  deadline: string | null; // free text: "Week 2 (April 14, 2026)", "end of Week 8"
  status: Status;
  // manual = typed in; task = promoted from an extracted task; document = parsed from a SOW's numbered section;
  // agent = suggested by the assistant (review_state "suggested" until a person accepts it)
  origin: "manual" | "task" | "document" | "agent";
  review_state: "suggested" | "confirmed" | "rejected";
  evidence: string | null; // verbatim SOW quote behind an assistant suggestion
  task_count: number; // active tasks assigned to this deliverable
  source_task_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface SimilarDeliverable {
  deliverable_id: string;
  number: string | null;
  title: string;
  score: number;
}

export interface AssignmentProposal {
  task_id: string;
  task_text: string;
  deliverable_id: string | null; // null = the assistant found no clear match
  number: string | null;
  title: string | null;
  method: "number" | "llm"; // number = the task cites exactly one known deliverable number
  confidence: "high" | "medium" | "low" | null;
}

// ---------------------------------------------------------------------------
// Hours
// ---------------------------------------------------------------------------

export type HoursStatus = "on_track" | "watch" | "at_risk" | "unknown";

export interface HoursTask {
  task_id: string;
  task_text: string;
  owner: string | null;
  status: string;
  estimate_hours: number | null;
  plan_start_week: number | null;
  plan_finish_week: number | null;
  logged_hours: number;
  remaining_hours: number | null;
  hours_still_needed: number | null;
  over_estimate_by: number | null;
  forecast_hours: number | null;
}

export interface BurnCheck {
  budget_used_pct: number | null;
  work_due_by_now_pct: number | null;
  work_done_pct: number | null;
  schedule: "in_line" | "behind" | "unknown";
  cost: "within" | "tight" | "over" | "unknown";
  status: HoursStatus;
  weeks_elapsed: number | null;
  tasks_compared: number;
}

export interface HoursReport {
  as_of: string;
  budget_hours: number | null;
  logged_hours: number;
  hours_left_in_budget: number | null;
  remaining_hours_on_open_tasks: number;
  forecast_at_completion: number;
  budget_minus_forecast: number | null;
  sum_of_task_estimates: number;
  hours_not_tied_to_a_task: number;
  burn_check: BurnCheck;
  recent_weekly_burn: number | null;
  weeks_of_budget_left_at_that_burn: number | null;
  open_tasks_without_estimate: string[];
  open_tasks_over_their_estimate: { task: string; estimate: number; logged: number; owner: string | null }[];
  logged_by_person: Record<string, number>;
  logged_by_week: Record<string, number>;
  tasks: HoursTask[];
  notes: string[];
  kickoff_date: string | null;
  role_budgets: Record<string, number>;
  week_start_day: number; // 0 = Monday ... 5 = Saturday, 6 = Sunday
}

export interface HoursSettings {
  budget_hours: number | null;
  kickoff_date: string | null;
  role_budgets: Record<string, number>;
  week_start_day: number;
}

export interface HoursSummaryRow {
  project_id: string;
  budget_hours: number | null;
  logged_hours: number;
  hours_left_in_budget: number | null;
  forecast_at_completion: number;
  status: HoursStatus;
}

export interface TimesheetTask {
  task_id: string;
  task_text: string;
  owner: string | null;
  status: string;
  estimate_hours: number | null;
  logged_hours: number;
  hours_still_needed: number | null;
  hours_this_week: number;
  note: string | null;
}

export interface TimesheetWeek {
  person: string;
  week_start: string;
  tasks: TimesheetTask[];
  other_hours: number;
  other_note: string | null;
}

export interface AvailabilityPerson {
  name: string;
  role: string | null;
  skills: string[];
  weekly_capacity_hours: number | null;
  capacity_in_window: number | null;
  planned_load_in_window: number;
  available_hours: number | null;
  unknown_items: { task: string; reason: string }[];
}

export interface AvailabilityReport {
  window: { from: string; to: string; working_days: number };
  skill_filter: string | null;
  people: AvailabilityPerson[];
  notes: string[];
}

export type ImportMatch = "exact" | "suggested" | "llm" | "other" | "none";

export interface TimesheetImportRow {
  idx: number;
  person_raw: string;
  person: string | null;
  week_start: string;
  label: string | null;
  hours: number;
  source_ref: string;
  task_id: string | null;
  task_text: string | null;
  match: ImportMatch;
  score: number;
}

export interface PlanImportRow {
  idx: number;
  label: string;
  owner: string | null;
  phase: string | null;
  start_wk: number | null;
  finish_wk: number | null;
  estimate: number | null;
  task_id: string | null;
  task_text: string | null;
  match: ImportMatch;
  action: "update" | "create" | "skip";
}

export interface ImportPreviewBase {
  import_id: string;
  layout: string;
  warnings: string[];
  tasks: { task_id: string; task_text: string }[];
}

export interface TimesheetImportPreview extends ImportPreviewBase {
  kind: "timesheet";
  mapping: Record<string, string> | null;
  rows: TimesheetImportRow[];
  people: string[];
  summary: {
    rows: number;
    hours: number;
    unknown_people: string[];
    by_match: Record<string, number>;
    will_replace: number;
    weeks: [string, string];
  };
}

export interface PlanImportPreview extends ImportPreviewBase {
  kind: "plan";
  rows: PlanImportRow[];
  settings_new: HoursSettings;
  settings_current: HoursSettings;
  summary: { rows: number; estimated_hours: number; to_update: number; to_create: number };
}

export type ImportPreview = TimesheetImportPreview | PlanImportPreview;

export interface HoursImportRecord {
  import_id: string;
  kind: "timesheet" | "plan";
  filename: string | null;
  layout: string | null;
  status: "applied" | "undone";
  summary: Record<string, unknown>;
  created_at: string;
  applied_at: string | null;
}

export interface InboxItem {
  import_id: string;
  filename: string | null;
  status: "applied" | "undone" | "held" | "duplicate" | "failed" | "preview";
  reason: string | null;
  hours: number | null;
  entries: number | null;
  replaced: number | null;
  to_check_count: number;
  can_review: boolean;
  created_at: string;
  applied_at: string | null;
}

export interface InboxCheck {
  import_id: string;
  filename: string | null;
  label: string;
  person: string;
  hours: number;
  task_id: string | null;
  task_text: string | null;
  match: string;
}

export interface TimesheetInbox {
  enabled: boolean;
  folder: string;
  poll_seconds: number;
  last_checked_at: string | null;
  last_applied_at: string | null;
  items: InboxItem[];
  to_check: InboxCheck[];
  tasks: { task_id: string; task_text: string }[];
}

export interface PlanSummary {
  has_plan: boolean;
  baseline_set_at: string | null;
  baseline_hours: number | null;
  current_hours: number;
  change_hours: number | null;
  tasks_added: number;
  tasks_removed: number;
}

export interface PlanChange {
  task_id: string | null;
  task_text: string | null;
  field: string;
  label: string;
  old_value: string | null;
  new_value: string | null;
  source: string;
  changed_at: string;
}
