import type { Priority, Task } from "@/lib/types";
import StatusBadge from "./StatusBadge";
import { IconClock, IconLink, IconUsers } from "./icons";

const COLUMNS: { priority: Priority; label: string; color: string }[] = [
  { priority: "high", label: "High Priority", color: "var(--color-danger)" },
  { priority: "medium", label: "Medium Priority", color: "var(--color-warning)" },
  { priority: "low", label: "Low Priority", color: "var(--color-success)" },
];

function TaskCard({ task }: { task: Task }) {
  const priorityColor =
    task.priority === "high" ? "var(--color-danger)" : task.priority === "medium" ? "var(--color-warning)" : "var(--color-success)";

  return (
    <div
      className="glass-panel animate-card-in rounded-xl p-3 transition hover:-translate-y-0.5"
      style={{ borderLeft: `3px solid ${priorityColor}` }}
    >
      <div className="mb-2 text-[13px] leading-snug text-text-primary">{task.task_text}</div>

      <div className="flex flex-wrap items-center gap-1.5">
        {task.owner && (
          <span className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-1.5 py-0.5 text-[11px] text-text-muted">
            <IconUsers className="h-2.5 w-2.5" />
            {task.owner}
          </span>
        )}
        {task.deadline && (
          <span className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-1.5 py-0.5 text-[11px] text-text-muted">
            <IconClock className="h-2.5 w-2.5" />
            {task.deadline}
          </span>
        )}
        {task.depends_on.length > 0 && (
          <span
            className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-1.5 py-0.5 text-[11px] text-text-muted"
            title={task.depends_on.map((d) => d.task_text).join(", ") ?? undefined}
          >
            <IconLink className="h-2.5 w-2.5" />
            {task.depends_on.length}
          </span>
        )}
        <span className="ml-auto">
          <StatusBadge status={task.status} />
        </span>
      </div>

      {task.blocker && (
        <div className="mt-2 rounded-md border-l-2 border-danger bg-danger-bg px-2 py-1 text-[11px] text-danger">
          {task.blocker}
        </div>
      )}
    </div>
  );
}

export default function TaskBoard({ tasks }: { tasks: Task[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
      {COLUMNS.map((col) => {
        const colTasks = tasks.filter((t) => t.priority === col.priority);
        return (
          <div key={col.priority} className="flex flex-col gap-2.5">
            <div
              className="flex items-center gap-2 rounded-lg border px-3 py-2"
              style={{
                borderColor: `color-mix(in oklab, ${col.color} 30%, var(--color-line))`,
                background: `color-mix(in oklab, ${col.color} 6%, transparent)`,
              }}
            >
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: col.color }} />
              <span className="text-[11.5px] font-semibold uppercase tracking-wide text-text-primary">{col.label}</span>
              <span className="ml-auto rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-text-muted">
                {colTasks.length}
              </span>
            </div>

            <div className="flex flex-col gap-2">
              {colTasks.length === 0 ? (
                <p className="px-1 py-2 text-[12.5px] text-text-faint">No tasks</p>
              ) : (
                colTasks.map((t) => <TaskCard key={t.task_id} task={t} />)
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
