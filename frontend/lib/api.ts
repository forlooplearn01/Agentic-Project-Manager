import type { ActivityEvent, JobPollResponse, JobTraceResponse, Meeting, ProjectPerson, ProjectRollup, Task } from "./types";

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

export function removeDependency(taskId: string, dependsOnTaskId: string): Promise<{ status: string }> {
  return request(`/tasks/${taskId}/dependencies/${dependsOnTaskId}`, {
    method: "DELETE",
  });
}

// Not a fetch() call - used directly as an <a href> so the browser handles the
// file download natively, streamed straight from the FileResponse on the backend.
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

export function sendChatMessage(sessionId: string, message: string): Promise<{ text: string }> {
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
