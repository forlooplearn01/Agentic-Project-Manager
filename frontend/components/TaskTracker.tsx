"use client";

import { Fragment, useState, type Dispatch, type SetStateAction } from "react";
import { setTaskHours } from "@/lib/api";
import { NumberCell, h as hoursText } from "./hours/shared";
import RemoveTaskButton from "./plan/RemoveTaskButton";
import TextCell from "./TextCell";
import { addDependency, markNotDuplicate, mergeTask, patchTask, removeDependency, setTaskDeliverable } from "@/lib/api";
import type { Deliverable, Status, Task, TaskRef } from "@/lib/types";
import InlineEditShell, { focusWithoutScrolling } from "./InlineEditShell";
import PriorityBadge from "./PriorityBadge";
import StatusBadge from "./StatusBadge";

const STATUS_OPTIONS: Status[] = ["not_started", "in_progress", "completed", "blocked", "on_hold"];

function InlineEditCell({
  task,
  field,
  onSaved,
}: {
  task: Task;
  field: "status" | "priority" | "owner" | "deadline" | "blocker";
  onSaved: (task_id: string, field: string, value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const currentValue = (task[field] as string) ?? "";

  async function save(value: string) {
    if (value === currentValue) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await patchTask(task.task_id, field, value);
      onSaved(task.task_id, field, value);
    } catch {
      // Silently keep the previous value visible - the table's next refresh will
      // reflect true DB state either way, no need for a blocking error here.
    } finally {
      setSaving(false);
      setEditing(false);
    }
  }

  const display = (
    <button type="button" onClick={() => setEditing(true)} className="hover-tint -mx-1.5 rounded px-1.5 py-0.5 text-left" disabled={saving}>
      {field === "status" ? (
        <StatusBadge status={task.status} />
      ) : field === "priority" ? (
        <PriorityBadge priority={task.priority} />
      ) : field === "blocker" ? (
        currentValue ? <span className="text-danger">{currentValue}</span> : <span className="text-text-faint">-</span>
      ) : (
        <span className="text-text-primary">{currentValue || "-"}</span>
      )}
    </button>
  );

  const options = field === "status" ? STATUS_OPTIONS : ["high", "medium", "low"];
  const editor =
    field === "status" || field === "priority" ? (
      <select
        ref={focusWithoutScrolling}
        defaultValue={currentValue}
        onBlur={(e) => save(e.target.value)}
        onChange={(e) => save(e.target.value)}
        className="w-full rounded-md border border-line bg-surface-solid px-1.5 py-1 text-[12.5px] text-text-primary shadow-lg outline-none focus:border-accent-to"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    ) : (
      <input
        ref={focusWithoutScrolling}
        defaultValue={currentValue}
        placeholder={field === "blocker" ? "No blocker" : undefined}
        onBlur={(e) => save(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && save((e.target as HTMLInputElement).value)}
        className="w-full rounded-md border border-line bg-surface-solid px-1.5 py-1 text-[12.5px] text-text-primary shadow-lg outline-none focus:border-accent-to"
      />
    );

  return <InlineEditShell editing={editing} display={display} editor={editor} minWidth={field === "status" || field === "priority" ? "8rem" : "12rem"} />;
}

function DependencyCell({
  task,
  allTasks,
  onChange,
}: {
  task: Task;
  allTasks: Task[];
  onChange: (task_id: string, depends_on: TaskRef[]) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);

  const candidates = allTasks.filter(
    (t) => t.task_id !== task.task_id && !task.depends_on.some((d) => d.task_id === t.task_id)
  );

  async function handleAdd(dependsOnTaskId: string) {
    if (!dependsOnTaskId) {
      setAdding(false);
      return;
    }
    setBusy(true);
    try {
      await addDependency(task.task_id, dependsOnTaskId);
      const target = allTasks.find((t) => t.task_id === dependsOnTaskId);
      onChange(task.task_id, [...task.depends_on, { task_id: dependsOnTaskId, task_text: target?.task_text ?? null }]);
    } finally {
      setBusy(false);
      setAdding(false);
    }
  }

  async function handleRemove(dependsOnTaskId: string) {
    setBusy(true);
    try {
      await removeDependency(task.task_id, dependsOnTaskId);
      onChange(task.task_id, task.depends_on.filter((d) => d.task_id !== dependsOnTaskId));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1">
      {task.depends_on.map((d) => (
        <span
          key={d.task_id}
          className="inline-flex max-w-[160px] items-center gap-1 rounded-md border border-line bg-surface px-1.5 py-0.5 text-[11px] text-text-muted"
        >
          <span className="truncate">{d.task_text ?? "unknown task"}</span>
          <button
            type="button"
            onClick={() => handleRemove(d.task_id)}
            disabled={busy}
            className="shrink-0 text-text-faint hover:text-danger"
            title="Remove dependency"
          >
            x
          </button>
        </span>
      ))}

      <InlineEditShell
        editing={adding}
        minWidth="14rem"
        display={
          <button
            type="button"
            onClick={() => setAdding(true)}
            disabled={busy || candidates.length === 0}
            className="hover-tint rounded-md border border-dashed border-line px-1.5 py-0.5 text-[11px] text-text-faint disabled:opacity-40"
          >
            + Add
          </button>
        }
        editor={
          <select
            ref={focusWithoutScrolling}
            disabled={busy}
            defaultValue=""
            onBlur={() => setAdding(false)}
            onChange={(e) => handleAdd(e.target.value)}
            className="w-full rounded-md border border-line bg-surface-solid px-1.5 py-1 text-[11px] text-text-primary shadow-lg outline-none focus:border-accent-to"
          >
            <option value="">Select a task...</option>
            {candidates.map((c) => (
              <option key={c.task_id} value={c.task_id}>
                {c.task_text.slice(0, 50)}
              </option>
            ))}
          </select>
        }
      />
    </div>
  );
}

function DuplicateNotice({
  task,
  onResolved,
}: {
  task: Task;
  onResolved: (task_id: string, merged: boolean, filled: Record<string, string | null>) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const other = task.possible_duplicate_of;
  if (!other) return null;

  async function resolve(merge: boolean) {
    if (!other) return;
    setBusy(true);
    setError(null);
    try {
      if (merge) {
        const result = await mergeTask(task.task_id, other.task_id);
        onResolved(task.task_id, true, result.filled_fields);
      } else {
        await markNotDuplicate(task.task_id);
        onResolved(task.task_id, false, {});
      }
    } catch {
      setError("Could not save - try again");
      setBusy(false);
    }
  }

  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 rounded-md border border-warning-line bg-warning-bg px-2 py-1 text-[11.5px]">
      <span className="text-warning">Possible duplicate of</span>
      <span className="max-w-[200px] truncate text-text-primary" title={other.task_text ?? undefined}>
        {other.task_text ?? "another task"}
      </span>
      <button
        type="button"
        onClick={() => resolve(true)}
        disabled={busy}
        className="rounded border border-line bg-surface-solid px-1.5 py-0.5 text-text-primary hover:border-accent-to disabled:opacity-50"
        title="Merge this task into the existing one"
      >
        Merge
      </button>
      <button
        type="button"
        onClick={() => resolve(false)}
        disabled={busy}
        className="rounded border border-line bg-surface-solid px-1.5 py-0.5 text-text-muted hover:border-accent-to disabled:opacity-50"
        title="They are different tasks - keep both"
      >
        Keep both
      </button>
      {error && <span className="text-danger">{error}</span>}
    </div>
  );
}

function DeliverableCell({
  task,
  deliverables,
  onAssigned,
}: {
  task: Task;
  deliverables: Deliverable[];
  onAssigned: (task_id: string, deliverable: Task["deliverable"]) => void;
}) {
  const [saving, setSaving] = useState(false);
  if (deliverables.length === 0) return <span className="text-text-faint">-</span>;

  async function change(id: string) {
    setSaving(true);
    try {
      await setTaskDeliverable(task.task_id, id || null);
      const d = deliverables.find((x) => x.deliverable_id === id);
      onAssigned(task.task_id, d ? { deliverable_id: d.deliverable_id, number: d.number, title: d.title } : null);
    } catch {
      // keep the previous value; the next refresh shows the true state
    } finally {
      setSaving(false);
    }
  }

  return (
    <select
      value={task.deliverable?.deliverable_id ?? ""}
      disabled={saving}
      onChange={(e) => change(e.target.value)}
      title={task.deliverable?.title ?? "Not assigned to a deliverable"}
      className="max-w-[170px] rounded-md border border-line bg-surface-solid px-1.5 py-0.5 text-[12px] text-text-primary outline-none focus:border-accent-to"
    >
      <option value="">-</option>
      {deliverables.map((d) => (
        <option key={d.deliverable_id} value={d.deliverable_id}>
          {d.number ? `${d.number} ` : ""}
          {d.title.slice(0, 40)}
        </option>
      ))}
    </select>
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

const COLUMNS = 15;

export default function TaskTracker({
  tasks,
  deliverables = [],
  onTasksChange,
  hasBaseline,
  onPlanChange,
}: {
  tasks: Task[];
  // Confirmed deliverables of this project, for the Deliverable column (empty = the column shows nothing to pick).
  deliverables?: Deliverable[];
  // Writes through to the parent's task list (not a local copy) so an edit made here
  // is immediately visible in the Board view too, without a refetch. Typed against the
  // parent's actual (nullable, pre-load) state shape rather than a narrowed local one.
  onTasksChange: Dispatch<SetStateAction<Task[] | null>>;
  // Whether the project has a baseline (the Baseline column and the "was" markers only mean something then).
  hasBaseline: boolean;
  // Called after a plan edit (estimate, weeks, phase, remove): the parent reloads the tasks, the summary and the history.
  onPlanChange: () => void | Promise<void>;
}) {
  const setTasks = onTasksChange;
  const [query, setQuery] = useState("");
  const [ownerFilter, setOwnerFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [attentionOnly, setAttentionOnly] = useState(false);

  function handleSaved(taskId: string, field: string, value: string) {
    setTasks((prev) => (prev ?? []).map((t) => (t.task_id === taskId ? { ...t, [field]: value } : t)));
  }

  async function savePlan(taskId: string, patch: Parameters<typeof setTaskHours>[1]) {
    await setTaskHours(taskId, patch);
    await onPlanChange();
  }

  function handleDuplicateResolved(taskId: string, merged: boolean, filled: Record<string, string | null>) {
    setTasks((prev) => {
      const all = prev ?? [];
      const flagged = all.find((t) => t.task_id === taskId);
      if (!merged) return all.map((t) => (t.task_id === taskId ? { ...t, possible_duplicate_of: null } : t));
      const intoId = flagged?.possible_duplicate_of?.task_id;
      return all
        .filter((t) => t.task_id !== taskId)
        .map((t) => {
          if (t.task_id === intoId) return { ...t, ...filled };
          // Anything else that was flagged against the merged-away task is no longer meaningful.
          return t.possible_duplicate_of?.task_id === taskId ? { ...t, possible_duplicate_of: null } : t;
        });
    });
  }

  function handleAssigned(taskId: string, deliverable: Task["deliverable"]) {
    setTasks((prev) => (prev ?? []).map((t) => (t.task_id === taskId ? { ...t, deliverable } : t)));
  }

  function handleDependencyChange(taskId: string, depends_on: TaskRef[]) {
    setTasks((prev) => (prev ?? []).map((t) => (t.task_id === taskId ? { ...t, depends_on } : t)));
  }

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
        <table className="w-full min-w-[1760px] text-left text-[13px]">
          <thead>
            <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
              <th className="px-3.5 py-2.5 font-medium">Task</th>
              <th className="px-3.5 py-2.5 font-medium">Phase</th>
              <th className="px-3.5 py-2.5 font-medium">Owner</th>
              <th className="px-3.5 py-2.5 font-medium">Status</th>
              <th className="px-3.5 py-2.5 font-medium">Priority</th>
              <th className="px-3.5 py-2.5 font-medium">Deadline</th>
              <th className="px-3.5 py-2.5 font-medium" title="Planned start and finish week, counted from the kickoff date. Click to edit.">Weeks</th>
              <th className="px-3.5 py-2.5 text-right font-medium" title="Estimated hours. Click to edit.">Est. (h)</th>
              <th className="px-3.5 py-2.5 font-medium" title="The estimate when the baseline was set, and how far it has moved">Baseline (h)</th>
              <th className="px-3.5 py-2.5 text-right font-medium" title="Hours logged on this task">Logged (h)</th>
              <th className="px-3.5 py-2.5 text-right font-medium" title="Hours left: estimate minus logged, or the hours still needed if someone set them">Left (h)</th>
              <th className="px-3.5 py-2.5 font-medium">Blocker</th>
              <th className="px-3.5 py-2.5 font-medium">Deliverable</th>
              <th className="px-3.5 py-2.5 font-medium">Depends on</th>
              <th className="w-10 px-2 py-2.5" aria-label="Remove" />
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
                        <DuplicateNotice task={t} onResolved={handleDuplicateResolved} />
                      </td>
                      <td className="px-3.5 py-2 [&_button]:block [&_button]:max-w-[150px] [&_button]:truncate" title={t.phase ?? undefined}>
                        <TextCell value={t.phase ?? null} placeholder="-" onSave={(v) => savePlan(t.task_id, { phase: v })} />
                      </td>
                      <td className="px-3.5 py-2">
                        <InlineEditCell task={t} field="owner" onSaved={handleSaved} />
                      </td>
                      <td className="px-3.5 py-2">
                        <InlineEditCell task={t} field="status" onSaved={handleSaved} />
                      </td>
                      <td className="px-3.5 py-2">
                        <InlineEditCell task={t} field="priority" onSaved={handleSaved} />
                      </td>
                      <td className="px-3.5 py-2">
                        <InlineEditCell task={t} field="deadline" onSaved={handleSaved} />
                      </td>
                      <td className="px-3.5 py-2 text-text-muted">
                        <span className="inline-flex items-center gap-1" title={weeksChanged ? `Baseline: weeks ${t.baseline_start_week} to ${t.baseline_finish_week}` : undefined}>
                          <NumberCell value={t.plan_start_week} integer step={1} placeholder="start" onSave={(v) => savePlan(t.task_id, { plan_start_week: v })} />
                          <span aria-hidden="true">to</span>
                          <NumberCell value={t.plan_finish_week} integer step={1} placeholder="finish" onSave={(v) => savePlan(t.task_id, { plan_finish_week: v })} />
                          {weeksChanged && <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-label="Weeks changed since the baseline" />}
                        </span>
                      </td>
                      <td className="px-3.5 py-2 text-right">
                        <NumberCell value={t.estimate_hours} placeholder="-" onSave={(v) => savePlan(t.task_id, { estimate_hours: v })} />
                      </td>
                      <td className="px-3.5 py-2">
                        <BaselineCell task={t} hasBaseline={hasBaseline} />
                      </td>
                      <td className="px-3.5 py-2 text-right tabular-nums text-text-muted">{hoursText(t.logged_hours ?? 0)}</td>
                      <td className="px-3.5 py-2 text-right tabular-nums text-text-muted">
                        {t.remaining_hours === null || t.remaining_hours === undefined ? <span className="italic text-text-faint">unknown</span> : hoursText(t.remaining_hours)}
                      </td>
                      <td className="px-3.5 py-2">
                        <InlineEditCell task={t} field="blocker" onSaved={handleSaved} />
                      </td>
                      <td className="px-3.5 py-2">
                        <DeliverableCell task={t} deliverables={deliverables} onAssigned={handleAssigned} />
                      </td>
                      <td className="px-3.5 py-2">
                        <DependencyCell task={t} allTasks={tasks} onChange={handleDependencyChange} />
                      </td>
                      <td className="px-2 py-2 text-right">
                        <RemoveTaskButton task={t} onRemoved={() => onPlanChange()} />
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
      <p className="text-[12px] text-text-faint">{filtered.length} of {tasks.length} task(s) - click any cell to edit</p>
    </div>
  );
}
