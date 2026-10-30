import type {
  ActivityEvent,
  AvailabilityReport,
  HoursImportRecord,
  TimesheetInbox,
  TimesheetImportPreview,
  HoursReport,
  HoursSettings,
  HoursSummaryRow,
  PlanChange,
  PlanSummary,
  ImportPreview,
  TimesheetWeek,
  JobPollResponse,
  JobTraceResponse,
  Meeting,
  ProjectPerson,
  ProjectRollup,
  AssignmentProposal,
  Deliverable,
  RaciMatrix,
  RaciRole,
  RaciSubject,
  RaidItem,
  RaidLevel,
  RaidStatus,
  RaidType,
  SimilarDeliverable,
  Status,
  Task,
  TraceEntry,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8010";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json();
}

export function fetchProjects(): Promise<{ projects: ProjectRollup[] }> {
  return request("/projects");
}

export function fetchProjectTasks(projectId: string): Promise<{ tasks: Task[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/tasks`);
}

export function submitRun(inputText: string, projectId: string, forceRun = false): Promise<{ job_id: string; from_cache: boolean }> {
  return request("/run", {
    method: "POST",
    body: JSON.stringify({ input_text: inputText, project_id: projectId, force_run: forceRun }),
  });
}

export function pollJob(jobId: string): Promise<JobPollResponse> {
  return request(`/jobs/${jobId}`);
}

export function fetchJobTrace(jobId: string): Promise<JobTraceResponse> {
  return request(`/jobs/${jobId}/trace`);
}

export function patchTask(taskId: string, field: string, value: string): Promise<Task> {
  return request(`/tasks/${taskId}`, {
    method: "PATCH",
    body: JSON.stringify({ field, value }),
  });
}

export function fetchActivity(projectId?: string | null, limit = 20): Promise<{ events: ActivityEvent[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (projectId) params.set("project_id", projectId);
  return request(`/activity?${params.toString()}`);
}

export function addDependency(taskId: string, dependsOnTaskId: string): Promise<{ status: string }> {
  return request(`/tasks/${taskId}/dependencies`, {
    method: "POST",
    body: JSON.stringify({ depends_on_task_id: dependsOnTaskId }),
  });
}

export function mergeTask(
  taskId: string,
  intoTaskId: string
): Promise<{ filled_fields: Record<string, string | null>; source_jira_issue_key_not_transferred: string | null }> {
  return request(`/tasks/${taskId}/merge`, {
    method: "POST",
    body: JSON.stringify({ into_task_id: intoTaskId }),
  });
}

export function markNotDuplicate(taskId: string): Promise<{ status: string }> {
  return request(`/tasks/${taskId}/not-duplicate`, { method: "POST" });
}

export function removeDependency(taskId: string, dependsOnTaskId: string): Promise<{ status: string }> {
  return request(`/tasks/${taskId}/dependencies/${dependsOnTaskId}`, {
    method: "DELETE",
  });
}

// Not a fetch() call - used directly as an <a href> so the browser handles the
// file download natively, streamed straight from the FileResponse on the backend.
export function registersExportUrl(projectId: string): string {
  return `${API_BASE}/projects/${encodeURIComponent(projectId)}/registers/export`;
}

export function projectExportUrl(projectId: string): string {
  return `${API_BASE}/projects/${encodeURIComponent(projectId)}/export`;
}

export function fetchProjectIntegrations(projectId: string): Promise<{ jira_project_key: string | null }> {
  return request(`/projects/${encodeURIComponent(projectId)}/integrations`);
}

export function setProjectJiraKey(projectId: string, jiraProjectKey: string): Promise<{ jira_project_key: string | null }> {
  return request(`/projects/${encodeURIComponent(projectId)}/integrations`, {
    method: "PUT",
    body: JSON.stringify({ jira_project_key: jiraProjectKey || null }),
  });
}

export async function extractFileText(file: File): Promise<{ text: string; filename: string; char_count: number }> {
  const formData = new FormData();
  formData.append("file", file);

  // Not using the shared request() helper: it always sets Content-Type: application/json,
  // which would break multipart upload - the browser must set its own boundary header,
  // which only happens when Content-Type is left unset for a FormData body.
  const res = await fetch(`${API_BASE}/extract-text`, { method: "POST", body: formData });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function fetchChatHistory(sessionId: string): Promise<{ messages: { role: "user" | "assistant"; content: string; created_at: string }[] }> {
  return request(`/chat/history?session_id=${encodeURIComponent(sessionId)}`);
}

export function sendChatMessage(sessionId: string, message: string): Promise<{ text: string; trace?: TraceEntry[] }> {
  return request("/chat", { method: "POST", body: JSON.stringify({ session_id: sessionId, message }) });
}

export function deleteProject(projectId: string): Promise<{ project_id: string; deleted_tasks: number; status: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
}

export function fetchProjectPeople(projectId: string): Promise<{ people: ProjectPerson[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/people`);
}

export function addProjectPerson(
  projectId: string,
  body: { name: string; email: string; role?: string }
): Promise<{ people: ProjectPerson[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/people`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function removeProjectPerson(projectId: string, name: string): Promise<{ people: ProjectPerson[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/people/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

export function scheduleMeeting(
  projectId: string,
  body: { title: string; attendee_emails: string[]; start_iso: string; end_iso: string; description?: string }
): Promise<{ status: string; event_id: string; meeting_link: string | null }> {
  return request(`/projects/${encodeURIComponent(projectId)}/schedule-meeting`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchProjectMeetings(projectId: string): Promise<{ meetings: Meeting[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/meetings`);
}

export function fetchPendingMeetings(): Promise<{ meetings: Meeting[] }> {
  return request(`/meetings/pending`);
}

export function approveMeeting(
  meetingId: string,
  overrides?: { title?: string; attendee_emails?: string[]; start_iso?: string; end_iso?: string; description?: string }
): Promise<Meeting> {
  return request(`/meetings/${encodeURIComponent(meetingId)}/approve`, {
    method: "POST",
    body: JSON.stringify(overrides ?? {}),
  });
}

export function rejectMeeting(meetingId: string): Promise<Meeting> {
  return request(`/meetings/${encodeURIComponent(meetingId)}/reject`, { method: "POST" });
}

export interface RaidItemInput {
  item_type: RaidType;
  title: string;
  description?: string | null;
  owner?: string | null;
  status?: RaidStatus;
  probability?: RaidLevel | null;
  impact?: RaidLevel | null;
  review_date?: string | null;
}

export function fetchRaidItems(projectId: string): Promise<{ items: RaidItem[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/raid`);
}

export function createRaidItem(projectId: string, input: RaidItemInput): Promise<RaidItem> {
  return request(`/projects/${encodeURIComponent(projectId)}/raid`, { method: "POST", body: JSON.stringify(input) });
}

export function updateRaidItem(itemId: string, patch: Partial<RaidItemInput> & { review_state?: RaidItem["review_state"] }): Promise<RaidItem> {
  return request(`/raid/${itemId}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function promoteRiskToIssue(itemId: string): Promise<{ risk: RaidItem; issue: RaidItem }> {
  return request(`/raid/${itemId}/promote-to-issue`, { method: "POST" });
}

export function deleteRaidItem(itemId: string): Promise<{ status: string }> {
  return request(`/raid/${itemId}`, { method: "DELETE" });
}

export function fetchRaci(projectId: string, subject: RaciSubject): Promise<RaciMatrix> {
  return request(`/projects/${encodeURIComponent(projectId)}/raci?subject=${subject}`);
}

export function setRaci(subject: RaciSubject, subjectId: string, person: string, roles: RaciRole[]): Promise<{ roles: RaciRole[] }> {
  const path = subject === "deliverable" ? `/deliverables/${subjectId}/raci` : `/tasks/${subjectId}/raci`;
  return request(path, { method: "PUT", body: JSON.stringify({ person, roles }) });
}

export interface DeliverableInput {
  title: string;
  number?: string | null;
  owner?: string | null;
  deadline?: string | null;
  status?: Status;
}

export function fetchDeliverables(projectId: string): Promise<{ deliverables: Deliverable[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/deliverables`);
}

export function createDeliverable(projectId: string, input: DeliverableInput): Promise<Deliverable> {
  return request(`/projects/${encodeURIComponent(projectId)}/deliverables`, { method: "POST", body: JSON.stringify(input) });
}

export function updateDeliverable(
  id: string,
  patch: Partial<DeliverableInput> & { review_state?: Deliverable["review_state"] }
): Promise<Deliverable> {
  return request(`/deliverables/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function deleteDeliverable(id: string): Promise<{ status: string }> {
  return request(`/deliverables/${id}`, { method: "DELETE" });
}

export function createDeliverablesFromTasks(projectId: string, taskIds: string[]): Promise<{ created: Deliverable[]; skipped: string[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/deliverables/from-tasks`, { method: "POST", body: JSON.stringify({ task_ids: taskIds }) });
}

export function fetchSimilarTasks(projectId: string): Promise<{ similar: Record<string, SimilarDeliverable> }> {
  return request(`/projects/${encodeURIComponent(projectId)}/deliverables/similar-tasks`);
}

export interface RaidSuggestResult {
  created: RaidItem[];
  skipped_existing: number;
  documents_scanned: number;
  documents_failed: number;
}

export function suggestRaidItems(projectId: string, force = false): Promise<RaidSuggestResult> {
  return request(`/projects/${encodeURIComponent(projectId)}/raid/suggest`, { method: "POST", body: JSON.stringify({ force }) });
}

export function suggestDeliverables(projectId: string): Promise<{ created: Deliverable[]; skipped_existing: number }> {
  return request(`/projects/${encodeURIComponent(projectId)}/deliverables/suggest`, { method: "POST", body: JSON.stringify({}) });
}

export function suggestAssignments(projectId: string): Promise<{ proposals: AssignmentProposal[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/deliverables/suggest-assignments`, { method: "POST" });
}

export function assignTasks(projectId: string, assignments: { task_id: string; deliverable_id: string | null }[]): Promise<{ changed: number }> {
  return request(`/projects/${encodeURIComponent(projectId)}/deliverables/assign`, { method: "PUT", body: JSON.stringify({ assignments }) });
}

export function setTaskDeliverable(taskId: string, deliverableId: string | null): Promise<{ deliverable_id: string | null }> {
  return request(`/tasks/${taskId}/deliverable`, { method: "PUT", body: JSON.stringify({ deliverable_id: deliverableId }) });
}


// ---------------------------------------------------------------------------
// Hours
// ---------------------------------------------------------------------------

export function fetchHours(projectId: string): Promise<HoursReport> {
  return request(`/projects/${encodeURIComponent(projectId)}/hours`);
}

export function fetchHoursSummary(): Promise<{ projects: HoursSummaryRow[] }> {
  return request("/hours/summary");
}

export function saveHoursSettings(
  projectId: string,
  patch: { budget_hours?: number | null; kickoff_date?: string | null; role_budgets?: Record<string, number>; week_start_day?: number }
): Promise<HoursSettings> {
  return request(`/projects/${encodeURIComponent(projectId)}/hours/settings`, { method: "PUT", body: JSON.stringify(patch) });
}

export function suggestHoursBudget(projectId: string): Promise<{ budget_hours: number | null; evidence: string | null }> {
  return request(`/projects/${encodeURIComponent(projectId)}/hours/budget/suggest`, { method: "POST" });
}

export function setTaskHours(
  taskId: string,
  patch: { estimate_hours?: number | null; plan_start_week?: number | null; plan_finish_week?: number | null; hours_still_needed?: number | null; phase?: string | null }
): Promise<Record<string, unknown>> {
  return request(`/tasks/${taskId}/hours`, { method: "PUT", body: JSON.stringify(patch) });
}

export function fetchTimesheet(projectId: string, person: string, week: string): Promise<TimesheetWeek> {
  const q = new URLSearchParams({ person, week });
  return request(`/projects/${encodeURIComponent(projectId)}/timesheet?${q}`);
}

export function saveTimesheet(
  projectId: string,
  person: string,
  weekStart: string,
  entries: { task_id: string | null; hours: number; note?: string | null }[]
): Promise<{ week_start: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/timesheet`, {
    method: "PUT",
    body: JSON.stringify({ person, week_start: weekStart, entries }),
  });
}

export function setPersonCapacity(
  projectId: string,
  name: string,
  patch: { weekly_capacity_hours?: number | null; skills?: string[] }
): Promise<{ people: ProjectPerson[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/people/${encodeURIComponent(name)}/capacity`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

export function fetchAvailability(projectId: string, from: string, to: string, skill: string): Promise<AvailabilityReport> {
  const q = new URLSearchParams({ from, to });
  if (skill.trim()) q.set("skill", skill.trim());
  return request(`/projects/${encodeURIComponent(projectId)}/availability?${q}`);
}

// A file upload: the browser must set the multipart boundary itself, so this does not go through request().
export async function previewHoursImport(
  projectId: string,
  kind: "timesheet" | "plan",
  file: File,
  mapping?: Record<string, string>
): Promise<ImportPreview> {
  const form = new FormData();
  form.append("kind", kind);
  form.append("file", file);
  if (mapping) form.append("mapping", JSON.stringify(mapping));
  const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/hours/import/preview`, { method: "POST", body: form });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status line
    }
    throw new Error(detail);
  }
  return res.json();
}

export interface ImportDecisionInput {
  idx: number;
  task_id?: string | null;
  person?: string | null;
  action?: "update" | "create" | "skip";
  skip?: boolean;
}

export function confirmHoursImport(
  importId: string,
  decisions: ImportDecisionInput[],
  options?: { apply_budget?: boolean; apply_kickoff?: boolean }
): Promise<Record<string, number>> {
  return request(`/hours/imports/${importId}/confirm`, { method: "POST", body: JSON.stringify({ decisions, ...options }) });
}

export function undoHoursImport(importId: string): Promise<{ status: string }> {
  return request(`/hours/imports/${importId}/undo`, { method: "POST" });
}

export function fetchHoursImports(projectId: string): Promise<{ imports: HoursImportRecord[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/hours/imports`);
}

export function fetchTimesheetInbox(projectId: string): Promise<TimesheetInbox> {
  return request(`/projects/${encodeURIComponent(projectId)}/timesheet-inbox`);
}

export function fetchImportReview(importId: string): Promise<TimesheetImportPreview> {
  return request(`/hours/imports/${importId}/review`);
}

// Moves one group of a picked-up file's hours to another task (taskId null = Other). Totals do not change.
export function reassignImportGroup(importId: string, label: string, person: string, taskId: string | null): Promise<Record<string, unknown>> {
  return request(`/hours/imports/${importId}/reassign`, { method: "POST", body: JSON.stringify({ label, person, task_id: taskId }) });
}

export function fetchHoursSettings(projectId: string): Promise<HoursSettings> {
  return request(`/projects/${encodeURIComponent(projectId)}/hours/settings`);
}

// ---------------------------------------------------------------------------
// Plan (Workspace > Plan)
// ---------------------------------------------------------------------------

export function fetchPlanSummary(projectId: string): Promise<PlanSummary> {
  return request(`/projects/${encodeURIComponent(projectId)}/plan/summary`);
}

export function fetchPlanChanges(projectId: string, limit = 100): Promise<{ changes: PlanChange[] }> {
  return request(`/projects/${encodeURIComponent(projectId)}/plan/changes?limit=${limit}`);
}

export function setPlanBaseline(projectId: string): Promise<PlanSummary> {
  return request(`/projects/${encodeURIComponent(projectId)}/plan/baseline`, { method: "POST" });
}

export function createPlanTask(
  projectId: string,
  input: { task_text: string; owner?: string; phase?: string; estimate_hours?: number | null; plan_start_week?: number | null; plan_finish_week?: number | null }
): Promise<{ task_id: string; task_text: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/plan/tasks`, { method: "POST", body: JSON.stringify(input) });
}

export function removePlanTask(taskId: string): Promise<{ task_text: string; logged_hours: number }> {
  return request(`/tasks/${taskId}`, { method: "DELETE" });
}
