import type { TraceEntry } from "./types";

// What the assistant looked at, in words a client recognises (the tool names are internal).
const LABELS: Record<string, string> = {
  list_projects: "Project list",
  get_project_overview: "Project overview",
  search_tasks: "Tasks",
  get_task_by_id: "Task details",
  get_owner_workload: "Workload",
  get_project_hours: "Hours",
  find_available_people: "Who is free",
  get_project_dependencies: "Dependencies",
  get_recent_activity: "Recent activity",
  get_project_people: "People",
  get_project_meetings: "Meetings",
  search_documents: "SOW and meeting minutes",
};

// Distinct sources in the order the assistant used them; a tool the app does not know is left out rather than shown by its internal name.
export function sourcesOf(trace: TraceEntry[] | undefined): string[] {
  const seen: string[] = [];
  for (const t of trace ?? []) {
    const label = LABELS[t.tool];
    if (label && !seen.includes(label)) seen.push(label);
  }
  return seen;
}
