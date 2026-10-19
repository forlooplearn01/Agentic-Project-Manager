"use client";

import { useState, type Dispatch, type SetStateAction } from "react";
import { addDependency, markNotDuplicate, mergeTask, patchTask, removeDependency } from "@/lib/api";
import type { Status, Task, TaskRef } from "@/lib/types";
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

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="hover-tint -mx-1.5 rounded px-1.5 py-0.5 text-left"
        disabled={saving}
      >
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
  }

  if (field === "status" || field === "priority") {
    const options = field === "status" ? STATUS_OPTIONS : ["high", "medium", "low"];
    return (
      <select
        autoFocus
        defaultValue={currentValue}
        onBlur={(e) => save(e.target.value)}
        onChange={(e) => save(e.target.value)}
        className="rounded-md border border-line bg-surface-solid px-1.5 py-0.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }

  return (
    <input
      autoFocus
      defaultValue={currentValue}
      placeholder={field === "blocker" ? "No blocker" : undefined}
      onBlur={(e) => save(e.target.value)}
      onKeyDown={(e) => e.key === "Enter" && save((e.target as HTMLInputElement).value)}
      className="w-full rounded-md border border-line bg-surface-solid px-1.5 py-0.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to"
    />
  );
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

      {adding ? (
        <select
          autoFocus
          disabled={busy}
          defaultValue=""
          onBlur={() => setAdding(false)}
          onChange={(e) => handleAdd(e.target.value)}
          className="rounded-md border border-line bg-surface-solid px-1.5 py-0.5 text-[11px] text-text-primary outline-none focus:border-accent-to"
        >
          <option value="">Select a task...</option>
          {candidates.map((c) => (
            <option key={c.task_id} value={c.task_id}>
              {c.task_text.slice(0, 50)}
            </option>
          ))}
        </select>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          disabled={busy || candidates.length === 0}
          className="hover-tint rounded-md border border-dashed border-line px-1.5 py-0.5 text-[11px] text-text-faint disabled:opacity-40"
        >
          + Add
        </button>
      )}
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

export default function TaskTracker({
  tasks,
  onTasksChange,
}: {
  tasks: Task[];
  // Writes through to the parent's task list (not a local copy) so an edit made here
  // is immediately visible in the Board view too, without a refetch. Typed against the
  // parent's actual (nullable, pre-load) state shape rather than a narrowed local one.
  onTasksChange: Dispatch<SetStateAction<Task[] | null>>;
}) {
  const setTasks = onTasksChange;
  const [query, setQuery] = useState("");
  const [ownerFilter, setOwnerFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  function handleSaved(taskId: string, field: string, value: string) {
    setTasks((prev) => (prev ?? []).map((t) => (t.task_id === taskId ? { ...t, [field]: value } : t)));
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

  function handleDependencyChange(taskId: string, depends_on: TaskRef[]) {
    setTasks((prev) => (prev ?? []).map((t) => (t.task_id === taskId ? { ...t, depends_on } : t)));
  }

  const filtered = tasks.filter((t) => {
    if (query && !t.task_text.toLowerCase().includes(query.toLowerCase())) return false;
    if (ownerFilter && !(t.owner ?? "").toLowerCase().includes(ownerFilter.toLowerCase())) return false;
    if (statusFilter !== "all" && (t.status ?? "not_started") !== statusFilter) return false;
    return true;
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
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
      </div>

      <div className="glass-panel overflow-x-auto rounded-xl">
        <table className="w-full min-w-[960px] text-left text-[13px]">
          <thead>
            <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
              <th className="px-3.5 py-2.5 font-medium">Task</th>
              <th className="px-3.5 py-2.5 font-medium">Owner</th>
              <th className="px-3.5 py-2.5 font-medium">Status</th>
              <th className="px-3.5 py-2.5 font-medium">Priority</th>
              <th className="px-3.5 py-2.5 font-medium">Deadline</th>
              <th className="px-3.5 py-2.5 font-medium">Blocker</th>
              <th className="px-3.5 py-2.5 font-medium">Depends on</th>
              <th className="px-3.5 py-2.5 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => (
              <tr key={t.task_id} className="border-b border-line last:border-0 hover-tint">
                <td className="max-w-[320px] px-3.5 py-2 text-text-primary">
                  <div className="truncate" title={t.task_text}>{t.task_text}</div>
                  <DuplicateNotice task={t} onResolved={handleDuplicateResolved} />
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
                <td className="px-3.5 py-2">
                  <InlineEditCell task={t} field="blocker" onSaved={handleSaved} />
                </td>
                <td className="px-3.5 py-2">
                  <DependencyCell task={t} allTasks={tasks} onChange={handleDependencyChange} />
                </td>
                <td className="px-3.5 py-2 text-text-faint">{new Date(t.updated_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[12px] text-text-faint">{filtered.length} of {tasks.length} task(s) - click any cell to edit</p>
    </div>
  );
}
