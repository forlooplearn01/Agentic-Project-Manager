"use client";

import type { PlanSummary } from "@/lib/types";
import { formatDate } from "@/lib/format";
import { BTN_QUIET, h } from "@/components/hours/shared";

// The line above the plan table: where the plan stands against its baseline (the first version of the plan file that was read).
export default function PlanSummaryStrip({
  summary,
  historyOpen,
  onToggleHistory,
}: {
  summary: PlanSummary;
  historyOpen: boolean;
  onToggleHistory: () => void;
}) {
  const change = summary.change_hours ?? 0;
  return (
    <div className="glass-panel flex flex-wrap items-center justify-between gap-3 rounded-xl px-4 py-3">
      <div className="min-w-0 text-[13px] text-text-muted">
        {summary.baseline_set_at ? (
          <>
            <span className="font-medium text-text-primary">Baseline {formatDate(summary.baseline_set_at)}</span>
            {": "}
            <span className="tabular-nums">{h(summary.baseline_hours)} h</span> planned, now <span className="tabular-nums text-text-primary">{h(summary.current_hours)} h</span>{" "}
            <span className={`rounded px-1.5 py-0.5 text-[11.5px] font-medium tabular-nums ${change > 0 ? "bg-warning-bg text-warning" : change < 0 ? "bg-success-bg text-success" : "bg-surface text-text-muted"}`}>
              {change > 0 ? "+" : ""}
              {h(change)} h
            </span>
            {summary.tasks_added > 0 && <span className="ml-2">{summary.tasks_added} task{summary.tasks_added === 1 ? "" : "s"} added</span>}
            {summary.tasks_removed > 0 && <span className="ml-2">{summary.tasks_removed} removed</span>}
          </>
        ) : summary.has_plan ? (
          <>This plan has no baseline.</>
        ) : (
          <>No plan has been read yet.</>
        )}
      </div>
      <button type="button" onClick={onToggleHistory} aria-expanded={historyOpen} className={BTN_QUIET}>
        Change history
      </button>
    </div>
  );
}
