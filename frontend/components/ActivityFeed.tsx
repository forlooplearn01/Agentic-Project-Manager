"use client";

import { useEffect, useState } from "react";
import { fetchActivity } from "@/lib/api";
import type { ActivityEvent } from "@/lib/types";
import { relativeTime } from "@/lib/relativeTime";

const AGENT_META: Record<ActivityEvent["agent"], { label: string; color: string }> = {
  planning: { label: "Planning", color: "var(--color-stat-indigo)" },
  sync_reporting: { label: "Sync/Reporting", color: "var(--color-stat-blue)" },
  meeting_coordinator: { label: "Meeting Coordinator", color: "var(--color-stat-purple)" },
};

// Turns a raw tool call into a short human sentence - the point of this feed is to
// read like "here's what the AI just did," not like a debug log.
function describe(event: ActivityEvent): string {
  let result: Record<string, unknown> = {};
  try {
    result = JSON.parse(event.result);
  } catch {
    // leave empty - fall through to generic phrasing below
  }
  switch (event.tool) {
    case "check_owner_availability":
      if (result.status === "available" || result.status === "busy") {
        return `Checked calendar availability for ${result.owner ?? "an owner"}`;
      }
      if (result.status === "not_configured") {
        return "Checked owner availability (calendar not connected)";
      }
      // status "unavailable_to_check" - e.g. a plain name rather than a resolved
      // email, or a calendar that isn't shared with the connected account. This is
      // the common case, not a connectivity problem, so it gets its own honest label
      // rather than being lumped in with "not connected" or claimed as a success.
      return `Checked owner availability for ${result.owner ?? "an owner"} (couldn't verify)`;
    case "search_tasks":
      return "Searched existing project tasks";
    case "get_task_by_id":
      return "Looked up a task's full detail";
    case "record_dependency":
      return "Recorded a dependency between two tasks";
    case "sync_task_to_jira":
      if (result.status === "created") {
        const warning = typeof result.assignee_warning === "string" ? " - unassigned, no matching Jira user" : "";
        return `Synced task to Jira (${result.jira_issue_key ?? "created"})${warning}`;
      }
      if (result.status === "already_synced") {
        return `Already in Jira (${result.jira_issue_key ?? "existing issue"}) - skipped`;
      }
      if (typeof result.message === "string" && result.message.includes("no Jira project key mapped")) {
        return "Attempted a Jira sync (no Jira project key set for this project)";
      }
      if (result.status === "not_configured") {
        return "Attempted a Jira sync (Jira not connected)";
      }
      return "Attempted a Jira sync (failed)";
    case "generate_report":
      return "Generated a project report export";
    case "resolve_attendees":
      return `Resolved meeting attendees${Array.isArray(result.unresolved) && result.unresolved.length ? ` (${result.unresolved.length} unresolved)` : ""}`;
    case "propose_meeting":
      return result.status === "proposed" ? "Proposed a follow-up meeting - pending approval" : "Attempted to propose a meeting";
    case "schedule_meeting":
      // Legacy trace entries from before the approval gate was added.
      return result.status === "scheduled" ? "Scheduled a follow-up meeting" : "Attempted to schedule a meeting (calendar not connected)";
    default:
      return `Called ${event.tool}`;
  }
}

export default function ActivityFeed({ projectId }: { projectId?: string | null }) {
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchActivity(projectId, 20)
      .then((r) => !cancelled && setEvents(r.events))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  return (
    <div className="glass-panel rounded-2xl p-5">
      <div className="mb-4 flex items-center gap-2">
        <span className="relative flex h-2 w-2">
          <span className="pulse-dot absolute inline-flex h-full w-full rounded-full bg-success opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
        </span>
        <h2 className="text-sm font-semibold tracking-tight text-text-primary">Agent activity</h2>
      </div>

      {error && <p className="text-[13px] text-danger">{error}</p>}
      {!events && !error && <p className="text-[13px] text-text-faint">Loading…</p>}
      {events && events.length === 0 && (
        <p className="text-[13px] text-text-faint">No agent activity yet - run a document to see agents act here.</p>
      )}

      {events && events.length > 0 && (
        <ol className="activity-rail flex flex-col gap-4 pl-1">
          {events.map((e, i) => {
            const meta = AGENT_META[e.agent];
            return (
              <li key={`${e.job_id}-${i}`} className="animate-activity-in relative flex gap-3 pl-8" style={{ animationDelay: `${i * 30}ms` }}>
                <span
                  className="absolute left-2.5 top-1 h-2.5 w-2.5 rounded-full ring-4 ring-background"
                  style={{ background: meta.color }}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] leading-snug text-text-primary">{describe(e)}</p>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-text-faint">
                    <span style={{ color: meta.color }}>{meta.label}</span>
                    {!projectId && e.project_id && (
                      <>
                        <span>·</span>
                        <span>{e.project_id}</span>
                      </>
                    )}
                    <span>·</span>
                    <span>{relativeTime(e.created_at)}</span>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
