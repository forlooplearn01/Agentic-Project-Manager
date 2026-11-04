"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchDashboardAttention, fetchHoursSummary, fetchPendingMeetings, fetchProjects } from "@/lib/api";
import { getActivityFeedCollapsed, setActivityFeedCollapsed } from "@/lib/preferences";
import { formatDateTime } from "@/lib/format";
import type { DashboardAttention, HoursSummaryRow, Meeting, ProjectRollup } from "@/lib/types";
import { StatusPill, h as hoursText } from "@/components/hours/shared";
import StatCard, { type StatColor, type StatPart } from "@/components/StatCard";
import ActivityFeed from "@/components/ActivityFeed";
import { IconAlertTriangle, IconCalendar, IconChecklist, IconClock, IconFlame, IconFolder, IconGauge, IconLayers, IconUsers } from "@/components/icons";

// Every tile counts something that needs attention and names who owns it. Nothing is summed or averaged across projects: a healthy project
// must not be able to offset a troubled one. A tile at zero turns green.
const sumOf = (m: Record<string, number>) => Object.values(m).reduce((a, b) => a + b, 0);
const tone = (count: number, problem: StatColor, loaded: boolean): StatColor => (loaded && count === 0 ? "emerald" : problem);

export default function DashboardPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectRollup[] | null>(null);
  const [pendingMeetingsCount, setPendingMeetingsCount] = useState<number | null>(null);
  const [hoursRows, setHoursRows] = useState<HoursSummaryRow[]>([]);
  const [pendingMeetings, setPendingMeetings] = useState<Meeting[]>([]);
  const [attention, setAttention] = useState<DashboardAttention | null>(null);
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
      .then((r) => {
        setPendingMeetingsCount(r.meetings.length);
        setPendingMeetings(r.meetings);
      })
      .catch(() => setPendingMeetingsCount(null));
    fetchDashboardAttention()
      .then(setAttention)
      .catch(() => setAttention(null));
    fetchHoursSummary()
      .then((r) => setHoursRows(r.projects))
      .catch(() => setHoursRows([]));
  }, []);

  // Ties go to the project in the worse state (at risk, then watch), then by name, so a tile always opens the same project.
  const severity = (pid: string) => {
    const status = hoursRows.find((r) => r.project_id === pid)?.status;
    return status === "at_risk" ? 0 : status === "watch" ? 1 : 2;
  };
  const ranked = (m: Record<string, number>) =>
    Object.entries(m).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1] || severity(a[0]) - severity(b[0]) || a[0].localeCompare(b[0]));
  const topOf = (m: Record<string, number>) => ranked(m)[0]?.[0];
  const toWorkspace = (pid: string) => router.push(`/products/task-manager?project=${encodeURIComponent(pid)}`);
  const toRegisters = (pid: string) => router.push(`/products/registers?project=${encodeURIComponent(pid)}`);
  const toHours = (pid: string) => router.push(`/products/hours?project=${encodeURIComponent(pid)}`);
  // "halvane 2 · forloop 1": each project is a link of its own; the tile itself opens the worst one.
  const partsOf = (m: Record<string, number>, go: (pid: string) => void, text = (p: string, n: number) => `${p} ${n}`): StatPart[] =>
    ranked(m).map(([p, n]) => ({ text: text(p, n), onClick: () => go(p) }));
  const openTop = (m: Record<string, number>, go: (pid: string) => void) => {
    const top = topOf(m);
    return top ? () => go(top) : undefined;
  };

  const blocked = Object.fromEntries((projects ?? []).map((p) => [p.project_id, p.blocked_tasks]));
  const openTasks = (projects ?? []).reduce((a, p) => a + p.open_tasks, 0);
  const totalTasks = (projects ?? []).reduce((a, p) => a + p.total_tasks, 0);
  const attentionRows = hoursRows
    .filter((r) => r.status === "at_risk" || r.status === "watch")
    .sort((a, b) => severity(a.project_id) - severity(b.project_id) || a.project_id.localeCompare(b.project_id));
  const overBudget = Object.fromEntries(
    hoursRows.filter((r) => r.budget_hours !== null && r.forecast_at_completion > r.budget_hours).map((r) => [r.project_id, Math.round(r.forecast_at_completion - (r.budget_hours as number))])
  );
  const overCapacity = attention?.people_over_capacity ?? [];
  const meetingsByProject: Record<string, number> = {};
  pendingMeetings.forEach((m) => (meetingsByProject[m.project_id] = (meetingsByProject[m.project_id] ?? 0) + 1));
  const overCapacityByProject: Record<string, number> = {};
  overCapacity.forEach((p) => p.projects.forEach((pid) => (overCapacityByProject[pid] = (overCapacityByProject[pid] ?? 0) + 1)));

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
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <StatCard
                label="Projects needing attention"
                value={hoursRows.length ? attentionRows.length : "–"}
                color={tone(attentionRows.length, attentionRows.some((r) => r.status === "at_risk") ? "red" : "amber", hoursRows.length > 0)}
                icon={<IconFolder className="h-4.5 w-4.5" />}
                parts={attentionRows.map((r) => ({ text: `${r.project_id} ${r.status === "at_risk" ? "at risk" : "watch"}`, onClick: () => toHours(r.project_id) }))}
                detail={hoursRows.length && !attentionRows.length ? `All ${hoursRows.length} on track` : undefined}
                onClick={attentionRows.length ? () => toHours(attentionRows[0].project_id) : undefined}
              />
              <StatCard
                label="Projects forecast over budget"
                value={hoursRows.length ? Object.keys(overBudget).length : "–"}
                color={tone(Object.keys(overBudget).length, "red", hoursRows.length > 0)}
                icon={<IconGauge className="h-4.5 w-4.5" />}
                parts={partsOf(overBudget, toHours, (p, n) => `${p} +${n} h`)}
                detail={hoursRows.length && !Object.keys(overBudget).length ? "None over" : undefined}
                onClick={openTop(overBudget, toHours)}
              />
              <StatCard
                label="Blocked tasks"
                value={projects ? sumOf(blocked) : "–"}
                color={tone(sumOf(blocked), "red", !!projects)}
                icon={<IconAlertTriangle className="h-4.5 w-4.5" />}
                parts={partsOf(blocked, toWorkspace)}
                detail={projects && !sumOf(blocked) ? "None blocked" : undefined}
                onClick={openTop(blocked, toWorkspace)}
              />
              <StatCard
                label="Overdue tasks"
                value={attention ? sumOf(attention.overdue) : "–"}
                color={tone(attention ? sumOf(attention.overdue) : 0, "amber", !!attention)}
                icon={<IconClock className="h-4.5 w-4.5" />}
                parts={attention ? partsOf(attention.overdue, toWorkspace) : undefined}
                detail={attention && !sumOf(attention.overdue) ? "None overdue" : undefined}
                onClick={attention ? openTop(attention.overdue, toWorkspace) : undefined}
              />
              <StatCard
                label="Open high-impact risks"
                value={attention ? sumOf(attention.high_impact_risks) : "–"}
                color={tone(attention ? sumOf(attention.high_impact_risks) : 0, "purple", !!attention)}
                icon={<IconFlame className="h-4.5 w-4.5" />}
                parts={attention ? partsOf(attention.high_impact_risks, toRegisters) : undefined}
                detail={attention && !sumOf(attention.high_impact_risks) ? "None open" : undefined}
                onClick={attention ? openTop(attention.high_impact_risks, toRegisters) : undefined}
              />
              <StatCard
                label="People over capacity"
                value={attention ? overCapacity.length : "–"}
                color={tone(overCapacity.length, "amber", !!attention)}
                icon={<IconUsers className="h-4.5 w-4.5" />}
                parts={[
                  ...overCapacity.slice(0, 3).map((p) => ({ text: p.name.split(" ")[0], onClick: () => toHours(p.projects[0]) })),
                  ...(overCapacity.length > 3 ? [{ text: `+${overCapacity.length - 3}` }] : []),
                ]}
                detail={attention && !overCapacity.length ? "Nobody over" : undefined}
                onClick={openTop(overCapacityByProject, toHours)}
              />
              <StatCard
                label="High-priority tasks still open"
                value={attention ? sumOf(attention.high_priority_open) : "–"}
                color={tone(attention ? sumOf(attention.high_priority_open) : 0, "amber", !!attention)}
                icon={<IconChecklist className="h-4.5 w-4.5" />}
                parts={attention ? partsOf(attention.high_priority_open, toWorkspace) : undefined}
                detail={attention && !sumOf(attention.high_priority_open) ? "None open" : undefined}
                onClick={attention ? openTop(attention.high_priority_open, toWorkspace) : undefined}
              />
              <StatCard
                label="Meetings awaiting approval"
                value={pendingMeetingsCount ?? "–"}
                color={tone(pendingMeetingsCount ?? 0, "purple", pendingMeetingsCount !== null)}
                icon={<IconCalendar className="h-4.5 w-4.5" />}
                parts={partsOf(meetingsByProject, toWorkspace)}
                detail={pendingMeetingsCount === 0 ? "Nothing waiting" : undefined}
                onClick={openTop(meetingsByProject, toWorkspace)}
              />
              <StatCard
                label="Open tasks"
                value={projects ? openTasks : "–"}
                color="indigo"
                icon={<IconLayers className="h-4.5 w-4.5" />}
                detail={projects ? `of ${totalTasks} across ${projects.length} projects` : undefined}
              />
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
