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
  email: string;
  role: string | null;
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
}
