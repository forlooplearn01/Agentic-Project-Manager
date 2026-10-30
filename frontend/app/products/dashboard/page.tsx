"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchHoursSummary, fetchPendingMeetings, fetchProjects } from "@/lib/api";
import { getActivityFeedCollapsed, setActivityFeedCollapsed } from "@/lib/preferences";
import { formatDateTime } from "@/lib/format";
import type { HoursSummaryRow, ProjectRollup } from "@/lib/types";
import { StatusPill, h as hoursText } from "@/components/hours/shared";
import StatCard from "@/components/StatCard";
import ActivityFeed from "@/components/ActivityFeed";
import { IconAlertTriangle, IconCalendar, IconChecklist, IconClock, IconFlame, IconFolder, IconUsers } from "@/components/icons";

export default function DashboardPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectRollup[] | null>(null);
  const [pendingMeetingsCount, setPendingMeetingsCount] = useState<number | null>(null);
  const [hoursRows, setHoursRows] = useState<HoursSummaryRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [activityCollapsed, setActivityCollapsedState] = useState(() => getActivityFeedCollapsed());

  function toggleActivityCollapsed() {
    setActivityCollapsedState((prev) => {
      const next = !prev;
      setActivityFeedCollapsed(next);
      return next;
    });
  }

  useEffect(() => {
    fetchProjects()
      .then((r) => setProjects(r.projects))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    fetchPendingMeetings()
      .then((r) => setPendingMeetingsCount(r.meetings.length))
      .catch(() => setPendingMeetingsCount(null));
    fetchHoursSummary()
      .then((r) => setHoursRows(r.projects))
      .catch(() => setHoursRows([]));
  }, []);

  const totals = (projects ?? []).reduce(
    (acc, p) => ({
      total: acc.total + p.total_tasks,
      open: acc.open + p.open_tasks,
      blocked: acc.blocked + p.blocked_tasks,
      completed: acc.completed + p.completed_tasks,
      highPriority: acc.highPriority + p.high_priority_tasks,
    }),
    { total: 0, open: 0, blocked: 0, completed: 0, highPriority: 0 }
  );

  return (
    <main className="mx-auto max-w-[1800px] px-6 py-8">
      <header className="mb-7">
        <h1 className="text-lg font-semibold tracking-tight text-text-primary">Dashboard</h1>
        <p className="text-[12.5px] text-text-muted">Portfolio-wide view across every project.</p>
      </header>

      {error && <p className="mb-4 text-[13px] text-danger">{error}</p>}

      <div className={`grid grid-cols-1 gap-6 ${activityCollapsed ? "lg:grid-cols-[1fr_auto]" : "lg:grid-cols-[1fr_320px]"}`}>
        <div className="flex min-w-0 flex-col gap-6">
          <section>
            <h2 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-text-faint">All projects: overview</h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 2xl:grid-cols-6">
              <StatCard label="Projects" value={projects?.length ?? 0} color="indigo" icon={<IconFolder className="h-4.5 w-4.5" />} />
              <StatCard label="Total Tasks" value={totals.total} color="blue" icon={<IconChecklist className="h-4.5 w-4.5" />} />
              <StatCard label="Open" value={totals.open} color="purple" icon={<IconClock className="h-4.5 w-4.5" />} />
              <StatCard label="Completed" value={totals.completed} color="emerald" icon={<IconChecklist className="h-4.5 w-4.5" />} />
              <StatCard label="Blocked" value={totals.blocked} color="red" icon={<IconAlertTriangle className="h-4.5 w-4.5" />} />
              <StatCard label="High Priority" value={totals.highPriority} color="amber" icon={<IconFlame className="h-4.5 w-4.5" />} />
              {pendingMeetingsCount !== null && pendingMeetingsCount > 0 && (
                <StatCard label="Meetings Pending" value={pendingMeetingsCount} color="purple" icon={<IconCalendar className="h-4.5 w-4.5" />} />
              )}
            </div>
          </section>

          {hoursRows.length > 0 && (
            <section aria-label="Hours">
              <h2 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-text-faint">Hours left</h2>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
                {hoursRows.map((r) => (
                  <button
                    key={r.project_id}
                    onClick={() => router.push(`/products/hours?project=${encodeURIComponent(r.project_id)}`)}
                    className="hover-tint glass-panel animate-card-in rounded-xl p-4 text-left transition"
                  >
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <h3 className="truncate text-sm font-semibold text-text-primary">{r.project_id}</h3>
                      <StatusPill status={r.status} />
                    </div>
                    <p className="text-2xl font-semibold tracking-tight tabular-nums text-text-primary">
                      {r.hours_left_in_budget === null ? "–" : `${hoursText(r.hours_left_in_budget)} h`}
                      <span className="ml-1.5 text-[12px] font-normal text-text-muted">left{r.budget_hours !== null && ` of ${hoursText(r.budget_hours)} h`}</span>
                    </p>
                    <p className="mt-1 text-[12px] text-text-faint">{hoursText(r.logged_hours)} h logged · forecast {hoursText(r.forecast_at_completion)} h</p>
                  </button>
                ))}
              </div>
            </section>
          )}

          <section>
            <h2 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-text-faint">Projects</h2>
            {!projects && !error && <p className="text-[13px] text-text-faint">Loading…</p>}
            {projects && projects.length === 0 && (
              <p className="text-[13px] text-text-faint">No projects yet - submit an SOW in Extractor to create one.</p>
            )}
            {projects && projects.length > 0 && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
                {projects.map((p) => (
                  <button
                    key={p.project_id}
                    onClick={() => router.push(`/products/task-manager?project=${encodeURIComponent(p.project_id)}`)}
                    className="hover-tint glass-panel animate-card-in rounded-xl p-4 text-left transition"
                  >
                    <h3 className="mb-2 truncate text-sm font-semibold text-text-primary">{p.project_id}</h3>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-text-muted">
                      <span>{p.total_tasks} tasks</span>
                      <span className="text-text-faint">·</span>
                      <span>{p.open_tasks} open</span>
                      {p.blocked_tasks > 0 && (
                        <>
                          <span className="text-text-faint">·</span>
                          <span className="text-danger">{p.blocked_tasks} blocked</span>
                        </>
                      )}
                      {p.unique_owners > 0 && (
                        <span className="ml-auto inline-flex items-center gap-1 text-text-faint">
                          <IconUsers className="h-3 w-3" />
                          {p.unique_owners}
                        </span>
                      )}
                    </div>
                    {p.last_updated && (
                      <p className="mt-2 text-[11px] text-text-faint">Updated {formatDateTime(p.last_updated)}</p>
                    )}
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>

        <div className="lg:sticky lg:top-8 lg:self-start">
          <ActivityFeed projectId={null} collapsed={activityCollapsed} onToggleCollapsed={toggleActivityCollapsed} />
        </div>
      </div>
    </main>
  );
}
