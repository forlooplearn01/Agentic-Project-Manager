"use client";

import { Fragment, useState } from "react";
import { h as hoursText } from "./hours/shared";
import type { Status, Task } from "@/lib/types";
import PriorityBadge from "./PriorityBadge";
import StatusBadge from "./StatusBadge";

const STATUS_OPTIONS: Status[] = ["not_started", "in_progress", "completed", "blocked", "on_hold"];

// Read-only: the plan comes from the plan file in the project's plan folder, so nothing here is edited. Changes are made in that file, or
// suggested from a meeting and approved under Suggestions.
function Text({ value }: { value: string | null | undefined }) {
  return value ? <span className="text-text-primary">{value}</span> : <span className="text-text-faint">-</span>;
}

function Dependencies({ task }: { task: Task }) {
  if (task.depends_on.length === 0) return <span className="text-text-faint">-</span>;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {task.depends_on.map((d) => (
        <span key={d.task_id} className="inline-flex max-w-[160px] items-center rounded-md border border-line bg-surface px-1.5 py-0.5 text-[11px] text-text-muted">
          <span className="truncate" title={d.task_text ?? undefined}>{d.task_text ?? "unknown task"}</span>
        </span>
      ))}
    </div>
  );
}

// Phases in the order they start (earliest planned week first); tasks with no phase last. "" stands for no phase.
function phaseRanks(all: Task[]): Map<string, number> {
  const first = new Map<string, number>();
  for (const t of all) {
    const key = t.phase ?? "";
    first.set(key, Math.min(first.get(key) ?? 9999, t.plan_start_week ?? 9999));
  }
  const keys = Array.from(first.keys()).sort((a, b) => Number(a === "") - Number(b === "") || first.get(a)! - first.get(b)! || a.localeCompare(b));
  return new Map(keys.map((k, i) => [k, i]));
}

// What this task's estimate is against the plan as first agreed: the baseline hours and the change, or a tag if it came later.
function BaselineCell({ task, hasBaseline }: { task: Task; hasBaseline: boolean }) {
  if (!hasBaseline) return <span className="text-text-faint">-</span>;
  if (task.added_after_baseline) {
    return <span className="whitespace-nowrap rounded-md bg-warning-bg px-1.5 py-0.5 text-[11px] font-medium text-warning">Added after baseline</span>;
  }
  const base = task.baseline_estimate_hours;
  const now = task.estimate_hours;
  if (base === null || base === undefined) {
    return now ? <span className="whitespace-nowrap text-[11.5px] text-text-faint">not in baseline (+{hoursText(now)})</span> : <span className="text-text-faint">-</span>;
  }
  const diff = (now ?? 0) - base;
  return (
    <span className="whitespace-nowrap tabular-nums text-text-muted">
      {hoursText(base)}
      {diff !== 0 && (
        <span className={`ml-1.5 rounded px-1 text-[11px] font-medium ${diff > 0 ? "bg-warning-bg text-warning" : "bg-success-bg text-success"}`}>
          {diff > 0 ? "+" : ""}
          {hoursText(diff)}
        </span>
      )}
    </span>
  );
}

const COLUMNS = 14;

export default function TaskTracker({
  tasks,
  hasBaseline,
}: {
  tasks: Task[];
  // Whether the project has a baseline (the Baseline column and the "was" markers only mean something then).
  hasBaseline: boolean;
}) {
  const [query, setQuery] = useState("");
  const [ownerFilter, setOwnerFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [attentionOnly, setAttentionOnly] = useState(false);

  const needsAttention = (t: Task) =>
    t.status !== "completed" &&
    (t.remaining_hours === null || t.remaining_hours === undefined || (t.estimate_hours != null && (t.logged_hours ?? 0) > t.estimate_hours && t.hours_still_needed == null));

  const filtered = tasks.filter((t) => {
    if (query && !t.task_text.toLowerCase().includes(query.toLowerCase())) return false;
    if (ownerFilter && !(t.owner ?? "").toLowerCase().includes(ownerFilter.toLowerCase())) return false;
    if (statusFilter !== "all" && (t.status ?? "not_started") !== statusFilter) return false;
    if (attentionOnly && !needsAttention(t)) return false;
    return true;
  });

  // Group by phase, phases in the order they start, tasks inside a phase in the order they start.
  const ranks = phaseRanks(tasks);
  const sorted = [...filtered].sort(
    (a, b) =>
      (ranks.get(a.phase ?? "") ?? 0) - (ranks.get(b.phase ?? "") ?? 0) ||
      (a.plan_start_week ?? 9999) - (b.plan_start_week ?? 9999) ||
      a.task_text.localeCompare(b.task_text)
  );
  const groups: { phase: string; tasks: Task[] }[] = [];
  for (const t of sorted) {
    const phase = t.phase ?? "";
    if (!groups.length || groups[groups.length - 1].phase !== phase) groups.push({ phase, tasks: [] });
    groups[groups.length - 1].tasks.push(t);
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search tasks..."
          className="min-w-[160px] flex-1 rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[13px] text-text-primary outline-none focus:border-accent-to"
        />
        <input
          value={ownerFilter}
          onChange={(e) => setOwnerFilter(e.target.value)}
          placeholder="Filter by owner..."
          className="w-40 rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[13px] text-text-primary outline-none focus:border-accent-to"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[13px] text-text-primary outline-none focus:border-accent-to"
        >
          <option value="all">All statuses</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-[12.5px] text-text-muted">
          <input type="checkbox" checked={attentionOnly} onChange={(e) => setAttentionOnly(e.target.checked)} />
          Only open tasks with no estimate or over their estimate
        </label>
      </div>

      <div className="glass-panel overflow-x-auto rounded-xl">
        <table className="w-full min-w-[1600px] text-left text-[13px]">
          <thead>
            <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
              <th className="px-3.5 py-2.5 font-medium">Task</th>
              <th className="px-3.5 py-2.5 font-medium">Phase</th>
              <th className="px-3.5 py-2.5 font-medium">Owner</th>
              <th className="px-3.5 py-2.5 font-medium">Status</th>
              <th className="px-3.5 py-2.5 font-medium">Priority</th>
              <th className="px-3.5 py-2.5 font-medium">Deadline</th>
              <th className="px-3.5 py-2.5 font-medium" title="Planned start and finish week, counted from the kickoff date">Weeks</th>
              <th className="px-3.5 py-2.5 text-right font-medium" title="Estimated hours">Est. (h)</th>
              <th className="px-3.5 py-2.5 font-medium" title="The estimate when the baseline was set, and how far it has moved">Baseline (h)</th>
              <th className="px-3.5 py-2.5 text-right font-medium" title="Hours logged on this task">Logged (h)</th>
              <th className="px-3.5 py-2.5 text-right font-medium" title="Hours left: estimate minus logged, or the hours still needed if someone set them">Left (h)</th>
              <th className="px-3.5 py-2.5 font-medium">Blocker</th>
              <th className="px-3.5 py-2.5 font-medium">Deliverable</th>
              <th className="px-3.5 py-2.5 font-medium">Depends on</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <Fragment key={g.phase || "__none"}>
                <tr className="border-b border-line bg-surface">
                  <td colSpan={COLUMNS} className="px-3.5 py-1.5 text-[11.5px] font-semibold uppercase tracking-wide text-text-muted">
                    {g.phase || "No phase"}
                    <span className="ml-2 font-normal normal-case tracking-normal text-text-faint">
                      {g.tasks.length} task{g.tasks.length === 1 ? "" : "s"} · {hoursText(g.tasks.reduce((a, t) => a + (t.estimate_hours ?? 0), 0))} h estimated
                    </span>
                  </td>
                </tr>
                {g.tasks.map((t) => {
                  const weeksChanged =
                    hasBaseline &&
                    t.baseline_start_week != null &&
                    (t.plan_start_week !== t.baseline_start_week || t.plan_finish_week !== t.baseline_finish_week);
                  return (
                    <tr key={t.task_id} className="border-b border-line last:border-0 hover-tint">
                      <td className="max-w-[320px] px-3.5 py-2 text-text-primary">
                        <div className="truncate" title={t.task_text}>{t.task_text}</div>
                        {t.possible_duplicate_of && <div className="mt-0.5 truncate text-[11.5px] text-warning">Possible duplicate of {t.possible_duplicate_of.task_text ?? "another task"}</div>}
                      </td>
                      <td className="max-w-[150px] truncate px-3.5 py-2" title={t.phase ?? undefined}>
                        <Text value={t.phase} />
                      </td>
                      <td className="px-3.5 py-2">
                        <Text value={t.owner} />
                      </td>
                      <td className="px-3.5 py-2">
                        <StatusBadge status={t.status} />
                      </td>
                      <td className="px-3.5 py-2">
                        <PriorityBadge priority={t.priority} />
                      </td>
                      <td className="px-3.5 py-2">
                        <Text value={t.deadline} />
                      </td>
                      <td className="px-3.5 py-2 text-text-muted">
                        <span className="inline-flex items-center gap-1" title={weeksChanged ? `Baseline: weeks ${t.baseline_start_week} to ${t.baseline_finish_week}` : undefined}>
                          {t.plan_start_week ?? "-"}
                          <span aria-hidden="true">to</span>
                          {t.plan_finish_week ?? "-"}
                          {weeksChanged && <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-label="Weeks changed since the baseline" />}
                        </span>
                      </td>
                      <td className="px-3.5 py-2 text-right">
                        <span className="tabular-nums text-text-primary">{t.estimate_hours == null ? "-" : hoursText(t.estimate_hours)}</span>
                      </td>
                      <td className="px-3.5 py-2">
                        <BaselineCell task={t} hasBaseline={hasBaseline} />
                      </td>
                      <td className="px-3.5 py-2 text-right tabular-nums text-text-muted">{hoursText(t.logged_hours ?? 0)}</td>
                      <td className="px-3.5 py-2 text-right tabular-nums text-text-muted">
                        {t.remaining_hours === null || t.remaining_hours === undefined ? <span className="italic text-text-faint">unknown</span> : hoursText(t.remaining_hours)}
                      </td>
                      <td className="max-w-[200px] truncate px-3.5 py-2" title={t.blocker ?? undefined}>
                        <Text value={t.blocker} />
                      </td>
                      <td className="max-w-[170px] truncate px-3.5 py-2" title={t.deliverable?.title ?? undefined}>
                        <Text value={t.deliverable ? `${t.deliverable.number ? `${t.deliverable.number} ` : ""}${t.deliverable.title}` : null} />
                      </td>
                      <td className="px-3.5 py-2">
                        <Dependencies task={t} />
                      </td>
                    </tr>
                  );
                })}
              </Fragment>
            ))}
            {groups.length === 0 && (
              <tr>
                <td colSpan={COLUMNS} className="px-3.5 py-6 text-center text-text-faint">
                  No tasks match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="text-[12px] text-text-faint">{filtered.length} of {tasks.length} task(s). This is a view of the plan file; changes are made there or approved under Suggestions.</p>
    </div>
  );
}
