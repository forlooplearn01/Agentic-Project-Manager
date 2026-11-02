"use client";

import { useState } from "react";
import Link from "next/link";
import type { HoursReport } from "@/lib/types";
import { saveHoursSettings, suggestHoursBudget } from "@/lib/api";
import { IconAlertTriangle } from "@/components/icons";
import { BTN_PRIMARY, BTN_QUIET, INPUT, StatusPill, errorText, formatWeek, h } from "./shared";

function Metric({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "good" | "bad" }) {
  return (
    <div className="glass-panel rounded-xl p-4">
      <div className="text-[11px] font-medium uppercase tracking-wide text-text-faint">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight tabular-nums text-text-primary">{value}</div>
      {sub && <div className={`mt-0.5 text-[12px] ${tone === "bad" ? "text-danger" : tone === "good" ? "text-success" : "text-text-muted"}`}>{sub}</div>}
    </div>
  );
}

function Bar({ label, pct, tone }: { label: string; pct: number | null; tone: string }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-[12px]">
        <span className="text-text-muted">{label}</span>
        <span className="tabular-nums text-text-primary">{pct === null ? "–" : `${pct}%`}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-surface" role="img" aria-label={`${label}: ${pct === null ? "not available" : `${pct} percent`}`}>
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.min(pct ?? 0, 100)}%` }} />
      </div>
    </div>
  );
}

export default function HoursOverview({ projectId, report, onChanged }: { projectId: string; report: HoursReport; onChanged: () => void }) {
  const [budget, setBudget] = useState<string>(report.budget_hours === null ? "" : String(report.budget_hours));
  const [kickoff, setKickoff] = useState<string>(report.kickoff_date ?? "");
  const [saving, setSaving] = useState(false);
  const [finding, setFinding] = useState(false);
  const [suggestion, setSuggestion] = useState<{ budget_hours: number | null; evidence: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const b = report.burn_check;
  const over = report.budget_minus_forecast !== null && report.budget_minus_forecast < 0;

  async function saveSettings(next?: { budget?: number | null }) {
    setSaving(true);
    setError(null);
    try {
      const budgetValue = next?.budget !== undefined ? next.budget : budget.trim() === "" ? null : Number(budget);
      if (budgetValue !== null && (!Number.isFinite(budgetValue) || budgetValue < 0)) throw new Error("The budget must be a number, 0 or more.");
      await saveHoursSettings(projectId, { budget_hours: budgetValue, kickoff_date: kickoff || null });
      setSuggestion(null);
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  async function findBudget() {
    setFinding(true);
    setError(null);
    try {
      setSuggestion(await suggestHoursBudget(projectId));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setFinding(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {error && <p className="text-[13px] text-danger">{error}</p>}

      <section className="glass-panel flex flex-col gap-3 rounded-xl p-4" aria-label="Budget and kickoff">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Hours budget
            <input type="number" min={0} value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="e.g. 700" className={`${INPUT} w-32 normal-case`} />
          </label>
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Kickoff date
            <input type="date" value={kickoff} onChange={(e) => setKickoff(e.target.value)} className={`${INPUT} normal-case`} />
          </label>
          <button type="button" onClick={() => saveSettings()} disabled={saving} className={BTN_PRIMARY}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" onClick={findBudget} disabled={finding} className={BTN_QUIET} title="Look for a total number of hours in the project's SOW">
            {finding ? "Reading the SOW…" : "Find budget in SOW"}
          </button>
        </div>
        {suggestion && (
          <div className="rounded-lg border border-line bg-surface p-3 text-[12.5px]">
            {suggestion.budget_hours === null ? (
              <p className="text-text-muted">The SOW does not state a total number of hours. Enter the budget by hand.</p>
            ) : (
              <div className="flex flex-col gap-2">
                <p className="text-text-primary">
                  The SOW says <b>{h(suggestion.budget_hours)} hours</b>: <span className="italic text-text-muted">&ldquo;{suggestion.evidence}&rdquo;</span>
                </p>
                <div>
                  <button
                    type="button"
                    className={BTN_PRIMARY}
                    onClick={() => {
                      setBudget(String(suggestion.budget_hours));
                      saveSettings({ budget: suggestion.budget_hours });
                    }}
                  >
                    Use {h(suggestion.budget_hours)} hours
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
        <p className="text-[12px] text-text-faint">The kickoff date turns a task&apos;s planned weeks into dates, which the &quot;due by now&quot; check needs.</p>
      </section>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="Budget" value={report.budget_hours === null ? "Not set" : `${h(report.budget_hours)} h`} />
        <Metric label="Logged" value={`${h(report.logged_hours)} h`} sub={report.hours_not_tied_to_a_task ? `${h(report.hours_not_tied_to_a_task)} h not on a task` : undefined} />
        <Metric
          label="Left in budget"
          value={report.hours_left_in_budget === null ? "–" : `${h(report.hours_left_in_budget)} h`}
          sub={report.weeks_of_budget_left_at_that_burn !== null ? `about ${h(report.weeks_of_budget_left_at_that_burn)} weeks at ${h(report.recent_weekly_burn)} h/week` : undefined}
          tone={report.hours_left_in_budget !== null && report.hours_left_in_budget < 0 ? "bad" : undefined}
        />
        <Metric
          label="Forecast at completion"
          value={`${h(report.forecast_at_completion)} h`}
          sub={report.budget_minus_forecast === null ? undefined : over ? `${h(-report.budget_minus_forecast)} h over budget` : `${h(report.budget_minus_forecast)} h under budget`}
          tone={report.budget_minus_forecast === null ? undefined : over ? "bad" : "good"}
        />
      </div>

      <section className="glass-panel flex flex-col gap-3 rounded-xl p-4" aria-label="Burn check">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-[13px] font-semibold text-text-primary">Burn check</h3>
          <StatusPill status={b.status} />
        </div>
        <Bar label="Budget used" pct={b.budget_used_pct} tone="bg-accent-to" />
        <Bar label="Work due by now (from the plan)" pct={b.work_due_by_now_pct} tone="bg-info" />
        <Bar label="Work done (in-progress tasks count in proportion, up to 90%)" pct={b.work_done_pct} tone="bg-success" />
        <p className="text-[12px] text-text-muted">
          {b.schedule === "unknown"
            ? "Work done versus work due needs a kickoff date and planned weeks on the estimated tasks."
            : b.schedule === "behind"
              ? "Less work is done than the plan says should be by now."
              : "Work done is in line with what the plan says should be done by now."}{" "}
          {b.cost === "over" ? "The forecast is over budget." : b.cost === "tight" ? "The forecast is within 5% of the budget." : b.cost === "within" ? "The forecast is within budget." : ""}
          {b.weeks_elapsed !== null && ` Compared as of week ${h(b.weeks_elapsed)} of the plan, over ${b.tasks_compared} task(s).`}
        </p>
        {report.hours_not_tied_to_a_task > 0 && (
          <p className="text-[12px] text-text-faint">
            Budget used counts every hour logged, including {h(report.hours_not_tied_to_a_task)} h not on any task (meetings, admin) that the task
            estimates do not cover, so it can run ahead of work done even when the plan is on schedule.
          </p>
        )}
      </section>

      {(report.open_tasks_over_their_estimate.length > 0 || report.open_tasks_without_estimate.length > 0) && (
        <section className="glass-panel flex flex-col gap-3 rounded-xl p-4" aria-label="Needs attention">
          <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-warning">
            <IconAlertTriangle className="h-3.5 w-3.5" />
            Needs attention
            <Link href={`/products/task-manager?project=${encodeURIComponent(projectId)}`} className="ml-auto text-[12px] font-normal text-text-muted underline hover:text-text-primary">
              Open the plan
            </Link>
          </h3>
          {report.tasks
            .filter((t) => t.status !== "completed" && t.over_estimate_by && t.hours_still_needed === null)
            .map((t) => (
              <div key={t.task_id} className="flex flex-wrap items-center justify-between gap-2 text-[12.5px]">
                <span className="min-w-0 text-text-primary">
                  {t.task_text}
                  <span className="ml-2 text-text-faint">
                    {h(t.logged_hours)} h logged of {h(t.estimate_hours)} estimated, still open
                  </span>
                </span>
                <span className="text-text-muted">Raise the estimate in the plan file</span>
              </div>
            ))}
          {report.tasks
            .filter((t) => t.status !== "completed" && t.remaining_hours === null)
            .map((t) => (
              <div key={t.task_id} className="flex flex-wrap items-center justify-between gap-2 text-[12.5px]">
                <span className="min-w-0 text-text-primary">
                  {t.task_text}
                  <span className="ml-2 text-text-faint">no estimate, left out of the forecast</span>
                </span>
                <span className="text-text-muted">Add an estimate in the plan file</span>
              </div>
            ))}
        </section>
      )}

      {report.notes.length > 0 && (
        <ul className="flex flex-col gap-1 text-[12.5px] text-text-muted">
          {report.notes.map((n) => (
            <li key={n}>• {n}</li>
          ))}
        </ul>
      )}

      {Object.keys(report.logged_by_person).length > 0 && (
        <section className="grid gap-4 md:grid-cols-2" aria-label="Hours logged">
          <div className="glass-panel overflow-x-auto rounded-xl">
            <table className="w-full text-left text-[13px]">
              <thead><tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint"><th className="px-3.5 py-2.5 font-medium">Person</th><th className="px-3.5 py-2.5 text-right font-medium">Logged (h)</th></tr></thead>
              <tbody>
                {Object.entries(report.logged_by_person).sort((a, b) => b[1] - a[1]).map(([p, v]) => (
                  <tr key={p} className="border-b border-line last:border-0"><td className="px-3.5 py-2 text-text-primary">{p}</td><td className="px-3.5 py-2 text-right tabular-nums">{h(v)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="glass-panel overflow-x-auto rounded-xl">
            <table className="w-full text-left text-[13px]">
              <thead><tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint"><th className="px-3.5 py-2.5 font-medium">Week starting</th><th className="px-3.5 py-2.5 text-right font-medium">Logged (h)</th></tr></thead>
              <tbody>
                {Object.entries(report.logged_by_week).map(([w, v]) => (
                  <tr key={w} className="border-b border-line last:border-0"><td className="px-3.5 py-2 text-text-primary">{formatWeek(w)}</td><td className="px-3.5 py-2 text-right tabular-nums">{h(v)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
