"use client";

import { useEffect, useState } from "react";
import { fetchPlanChanges } from "@/lib/api";
import { formatDate, formatDateTime } from "@/lib/format";
import type { PlanChange } from "@/lib/types";
import { errorText } from "@/components/hours/shared";

const UNIT: Record<string, string> = { estimate_hours: " h", hours_still_needed: " h", budget_hours: " h" };

function value(c: PlanChange, v: string | null): string {
  if (v === null || v === "") return "none";
  if (c.field === "kickoff_date") return formatDate(v);
  return `${v}${UNIT[c.field] ?? ""}`;
}

// One plain sentence per change: what, on which task, from what to what.
function describe(c: PlanChange): string {
  const task = c.task_text ? `"${c.task_text}"` : "";
  switch (c.field) {
    case "task_added":
      return `Task added: ${task}${c.new_value ? ` (${c.new_value} h)` : ""}`;
    case "task_removed":
      return `Task removed: ${task}${c.old_value ? ` (was ${c.old_value} h)` : ""}`;
    case "baseline_set":
      return `Baseline set: ${c.new_value}`;
    case "plan_imported":
      return `Plan imported: ${c.new_value}`;
    case "import_undone":
      return `Plan import undone: ${c.new_value}`;
    case "budget_hours":
    case "kickoff_date":
      return `${c.label}: ${value(c, c.old_value)} to ${value(c, c.new_value)}`;
    default:
      return `${c.label} on ${task}: ${value(c, c.old_value)} to ${value(c, c.new_value)}`;
  }
}

export default function ChangeHistoryPanel({ projectId, refreshKey }: { projectId: string; refreshKey: number }) {
  const [changes, setChanges] = useState<PlanChange[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchPlanChanges(projectId)
      .then((r) => !cancelled && setChanges(r.changes))
      .catch((e) => !cancelled && setError(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [projectId, refreshKey]);

  return (
    <section className="glass-panel rounded-xl p-4" aria-label="Plan change history">
      <h3 className="mb-2 text-[13px] font-semibold text-text-primary">Change history</h3>
      {error && <p className="text-[13px] text-danger">{error}</p>}
      {!changes && !error && <p className="text-[13px] text-text-faint">Loading…</p>}
      {changes && changes.length === 0 && <p className="text-[13px] text-text-faint">Nothing has changed in the plan yet.</p>}
      {changes && changes.length > 0 && (
        <ul className="flex max-h-72 flex-col overflow-y-auto">
          {changes.map((c, i) => (
            <li key={`${c.changed_at}-${i}`} className="grid grid-cols-[150px_1fr] gap-3 border-b border-line py-1.5 text-[12.5px] last:border-0">
              <span className="tabular-nums text-text-faint">{formatDateTime(c.changed_at)}</span>
              <span className="text-text-primary">{describe(c)}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-[11.5px] text-text-faint">The history records when and what changed, not who: the app has no sign-in yet.</p>
    </section>
  );
}
