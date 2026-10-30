"use client";

import { useMemo, useRef, useState } from "react";
import type { ImportPreview, PlanImportPreview, TimesheetImportPreview } from "@/lib/types";
import { confirmHoursImport, previewHoursImport, type ImportDecisionInput } from "@/lib/api";
import { IconAlertTriangle, IconDownload } from "@/components/icons";
import { BTN_PRIMARY, BTN_QUIET, INPUT, errorText, formatWeek, h } from "./shared";

const OTHER = "__other__";
const SKIP = "__skip__";
const CREATE = "__create__";

const MATCH_LABEL: Record<string, { text: string; cls: string }> = {
  exact: { text: "exact", cls: "bg-success-bg text-success" },
  suggested: { text: "check", cls: "bg-warning-bg text-warning" },
  llm: { text: "check", cls: "bg-warning-bg text-warning" },
  other: { text: "other", cls: "bg-surface text-text-muted" },
  none: { text: "no match", cls: "bg-surface text-text-muted" },
};

function MatchBadge({ match }: { match: string }) {
  const m = MATCH_LABEL[match] ?? MATCH_LABEL.none;
  return <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${m.cls}`}>{m.text}</span>;
}

// The upload button for a plan or a timesheet; shows the review panel once the file has been read.
export default function ImportButton({
  projectId,
  kind,
  label,
  onImported,
}: {
  projectId: string;
  kind: "timesheet" | "plan";
  label: string;
  onImported: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  async function onFile(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setFileName(file.name);
    try {
      setPreview(await previewHoursImport(projectId, kind, file));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <input ref={input} type="file" accept=".xlsx,.xlsm,.csv" className="sr-only" id={`upload-${kind}`} onChange={(e) => onFile(e.target.files?.[0])} />
        <label htmlFor={`upload-${kind}`} className={`${BTN_QUIET} inline-flex cursor-pointer items-center gap-1.5 ${busy ? "pointer-events-none opacity-60" : ""}`}>
          <IconDownload className="h-3.5 w-3.5 rotate-180" />
          {busy ? "Reading the file…" : label}
        </label>
        <span className="text-[12px] text-text-faint">Excel (.xlsx) or CSV. Nothing is saved until you confirm.</span>
      </div>
      {error && (
        <p className="flex items-start gap-1.5 text-[13px] text-danger">
          <IconAlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {fileName ? `${fileName}: ` : ""}
          {error}
        </p>
      )}
      {result && <p className="text-[13px] text-success">{result}</p>}
      {preview && (
        <ImportReview
          preview={preview}
          onCancel={() => setPreview(null)}
          onDone={(message) => {
            setPreview(null);
            setResult(message);
            onImported();
          }}
        />
      )}
    </div>
  );
}

function ImportReview({ preview, onCancel, onDone }: { preview: ImportPreview; onCancel: () => void; onDone: (message: string) => void }) {
  return preview.kind === "timesheet" ? (
    <TimesheetReview preview={preview} onCancel={onCancel} onDone={onDone} />
  ) : (
    <PlanReview preview={preview} onCancel={onCancel} onDone={onDone} />
  );
}

// ---------------------------------------------------------------- timesheet

interface Group {
  key: string;
  label: string | null;
  personRaw: string;
  person: string | null;
  rowIdx: number[];
  hours: number;
  proposed: string; // task_id, OTHER
  match: string;
}

export function TimesheetReview({ preview, onCancel, onDone }: { preview: TimesheetImportPreview; onCancel: () => void; onDone: (m: string) => void }) {
  const groups = useMemo<Group[]>(() => {
    const map = new Map<string, Group>();
    for (const r of preview.rows) {
      const key = `${r.label ?? ""}\u0000${r.person_raw}`;
      const g = map.get(key) ?? { key, label: r.label, personRaw: r.person_raw, person: r.person, rowIdx: [], hours: 0, proposed: r.task_id ?? OTHER, match: r.match };
      g.rowIdx.push(r.idx);
      g.hours += r.hours;
      map.set(key, g);
    }
    return Array.from(map.values()).sort((a, b) => (a.label ?? "").localeCompare(b.label ?? "") || a.personRaw.localeCompare(b.personRaw));
  }, [preview.rows]);

  const [taskChoice, setTaskChoice] = useState<Record<string, string>>({});
  const [personChoice, setPersonChoice] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const s = preview.summary;
  const skippedHours = groups.filter((g) => (taskChoice[g.key] ?? g.proposed) === SKIP || (!g.person && !personChoice[g.key])).reduce((a, g) => a + g.hours, 0);

  async function confirm() {
    setSaving(true);
    setError(null);
    const decisions: ImportDecisionInput[] = [];
    for (const g of groups) {
      const task = taskChoice[g.key];
      const person = personChoice[g.key];
      for (const idx of g.rowIdx) {
        const d: ImportDecisionInput = { idx };
        if (task === SKIP) d.skip = true;
        else if (task !== undefined) d.task_id = task === OTHER ? null : task;
        if (person) d.person = person;
        if (d.skip || d.person || "task_id" in d) decisions.push(d);
      }
    }
    try {
      const r = await confirmHoursImport(preview.import_id, decisions);
      onDone(`Imported ${h(r.hours_total)} hours in ${r.entries_written} entries${r.replaced ? ` (${r.replaced} replaced earlier values)` : ""}${r.skipped_rows ? `, ${r.skipped_rows} row(s) skipped` : ""}.`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="glass-panel flex flex-col gap-3 rounded-xl p-4" aria-label="Review the timesheet import">
      <div>
        <h3 className="text-[14px] font-semibold text-text-primary">Review before importing</h3>
        <p className="text-[12.5px] text-text-muted">
          {h(s.hours)} hours in {s.rows} rows, weeks {formatWeek(s.weeks[0])} to {formatWeek(s.weeks[1])}. Layout read as <b>{preview.layout}</b>
          {preview.mapping ? ` (columns: ${Object.entries(preview.mapping).map(([k, v]) => `${k} = ${v}`).join(", ")})` : ""}.
        </p>
        {s.will_replace > 0 && (
          <p className="mt-1 text-[12.5px] text-warning">
            {s.will_replace} entries already exist for the same person, week and task. Importing replaces them; it does not add to them.
          </p>
        )}
        {preview.warnings.map((w) => (
          <p key={w} className="mt-1 text-[12px] text-text-muted">• {w}</p>
        ))}
      </div>

      <div className="max-h-[420px] overflow-auto rounded-lg border border-line">
        <table className="w-full min-w-[760px] text-left text-[12.5px]">
          <thead className="sticky top-0 bg-surface-solid">
            <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
              <th className="px-3 py-2 font-medium">Task as written</th>
              <th className="px-3 py-2 font-medium">Person</th>
              <th className="px-3 py-2 text-right font-medium">Rows</th>
              <th className="px-3 py-2 text-right font-medium">Hours</th>
              <th className="px-3 py-2 font-medium">Counts toward</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.key} className="border-b border-line last:border-0 align-top">
                <td className="max-w-[260px] px-3 py-1.5 text-text-primary">{g.label ?? <span className="italic text-text-faint">no task detail in this file</span>}</td>
                <td className="px-3 py-1.5">
                  {g.person ? (
                    <span className="text-text-primary">{g.person}</span>
                  ) : (
                    <select
                      aria-label={`Who is "${g.personRaw}"?`}
                      value={personChoice[g.key] ?? ""}
                      onChange={(e) => setPersonChoice({ ...personChoice, [g.key]: e.target.value })}
                      className={`${INPUT} w-full`}
                    >
                      <option value="">&ldquo;{g.personRaw}&rdquo;: unknown, pick a person</option>
                      {preview.people.map((p) => (
                        <option key={p} value={p}>{p}</option>
                      ))}
                    </select>
                  )}
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums">{g.rowIdx.length}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{h(g.hours)}</td>
                <td className="px-3 py-1.5">
                  <div className="flex items-center gap-2">
                    <select
                      aria-label={`Task for "${g.label ?? "no task detail"}"`}
                      value={taskChoice[g.key] ?? g.proposed}
                      onChange={(e) => setTaskChoice({ ...taskChoice, [g.key]: e.target.value })}
                      className={`${INPUT} w-full max-w-[320px]`}
                    >
                      <option value={OTHER}>Other (no listed task)</option>
                      {preview.tasks.map((t) => (
                        <option key={t.task_id} value={t.task_id}>{t.task_text.length > 70 ? `${t.task_text.slice(0, 70)}…` : t.task_text}</option>
                      ))}
                      <option value={SKIP}>Skip these rows</option>
                    </select>
                    {taskChoice[g.key] === undefined && <MatchBadge match={g.match} />}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {s.unknown_people.length > 0 && (
        <p className="text-[12.5px] text-warning">
          Not on the people list: {s.unknown_people.join(", ")}. Pick who they are above, or their rows are skipped
          {skippedHours > 0 ? ` (${h(skippedHours)} hours currently skipped)` : ""}.
        </p>
      )}
      {error && <p className="text-[13px] text-danger">{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className={BTN_QUIET}>Cancel</button>
        <button type="button" onClick={confirm} disabled={saving} className={BTN_PRIMARY}>
          {saving ? "Importing…" : `Import ${h(s.hours - skippedHours)} hours`}
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- plan

function PlanReview({ preview, onCancel, onDone }: { preview: PlanImportPreview; onCancel: () => void; onDone: (m: string) => void }) {
  const [choice, setChoice] = useState<Record<number, string>>({});
  const [applyBudget, setApplyBudget] = useState(true);
  const [applyKickoff, setApplyKickoff] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cur = preview.settings_current;
  const next = preview.settings_new;
  const hasSettings = next.budget_hours !== null || next.kickoff_date !== null || Object.keys(next.role_budgets).length > 0;

  function selected(r: PlanImportPreview["rows"][number]): string {
    if (choice[r.idx] !== undefined) return choice[r.idx];
    if (r.action === "update" && r.task_id) return r.task_id;
    return r.action === "create" ? CREATE : SKIP;
  }

  async function confirm() {
    setSaving(true);
    setError(null);
    const decisions: ImportDecisionInput[] = preview.rows.map((r) => {
      const v = selected(r);
      return v === SKIP ? { idx: r.idx, action: "skip" } : v === CREATE ? { idx: r.idx, action: "create" } : { idx: r.idx, action: "update", task_id: v };
    });
    try {
      const r = await confirmHoursImport(preview.import_id, decisions, { apply_budget: applyBudget, apply_kickoff: applyKickoff });
      onDone(`Plan imported: ${r.tasks_updated} task(s) updated, ${r.tasks_created} created.`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="glass-panel flex flex-col gap-3 rounded-xl p-4" aria-label="Review the plan import">
      <div>
        <h3 className="text-[14px] font-semibold text-text-primary">Review before importing</h3>
        <p className="text-[12.5px] text-text-muted">
          {preview.summary.rows} plan rows with {h(preview.summary.estimated_hours)} estimated hours: {preview.summary.to_update} match an existing task,{" "}
          {preview.summary.to_create} would be created as new tasks.
        </p>
        {preview.warnings.map((w) => (
          <p key={w} className="mt-1 text-[12px] text-text-muted">• {w}</p>
        ))}
      </div>

      {hasSettings && (
        <div className="rounded-lg border border-line bg-surface p-3 text-[12.5px]">
          <p className="mb-1.5 font-medium text-text-primary">Also found in the file</p>
          {next.budget_hours !== null && (
            <label className="flex items-center gap-2 text-text-muted">
              <input type="checkbox" checked={applyBudget} onChange={(e) => setApplyBudget(e.target.checked)} />
              Hours budget: <b className="text-text-primary">{h(next.budget_hours)} h</b>
              {cur.budget_hours !== null && <span>(now {h(cur.budget_hours)} h)</span>}
              {Object.keys(next.role_budgets).length > 0 && <span>and a budget for {Object.keys(next.role_budgets).length} roles</span>}
            </label>
          )}
          {next.kickoff_date && (
            <label className="mt-1 flex items-center gap-2 text-text-muted">
              <input type="checkbox" checked={applyKickoff} onChange={(e) => setApplyKickoff(e.target.checked)} />
              Kickoff date: <b className="text-text-primary">{formatWeek(next.kickoff_date)}</b>
              {cur.kickoff_date && <span>(now {formatWeek(cur.kickoff_date)})</span>}
            </label>
          )}
        </div>
      )}

      <div className="max-h-[420px] overflow-auto rounded-lg border border-line">
        <table className="w-full min-w-[820px] text-left text-[12.5px]">
          <thead className="sticky top-0 bg-surface-solid">
            <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
              <th className="px-3 py-2 font-medium">Plan row</th>
              <th className="px-3 py-2 font-medium">Owner</th>
              <th className="px-3 py-2 text-right font-medium">Estimate (h)</th>
              <th className="px-3 py-2 text-right font-medium">Weeks</th>
              <th className="px-3 py-2 font-medium">Becomes</th>
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((r) => (
              <tr key={r.idx} className="border-b border-line last:border-0 align-top">
                <td className="max-w-[300px] px-3 py-1.5 text-text-primary">{r.label}</td>
                <td className="px-3 py-1.5 text-text-muted">{r.owner ?? "–"}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{h(r.estimate)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{r.start_wk ? `${r.start_wk}–${r.finish_wk ?? r.start_wk}` : "–"}</td>
                <td className="px-3 py-1.5">
                  <div className="flex items-center gap-2">
                    <select aria-label={`What "${r.label}" becomes`} value={selected(r)} onChange={(e) => setChoice({ ...choice, [r.idx]: e.target.value })} className={`${INPUT} w-full max-w-[320px]`}>
                      <option value={CREATE}>Create as a new task</option>
                      <option value={SKIP}>Skip this row</option>
                      {preview.tasks.map((t) => (
                        <option key={t.task_id} value={t.task_id}>Update: {t.task_text.length > 60 ? `${t.task_text.slice(0, 60)}…` : t.task_text}</option>
                      ))}
                    </select>
                    {choice[r.idx] === undefined && <MatchBadge match={r.match} />}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {error && <p className="text-[13px] text-danger">{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className={BTN_QUIET}>Cancel</button>
        <button type="button" onClick={confirm} disabled={saving} className={BTN_PRIMARY}>{saving ? "Importing…" : "Import plan"}</button>
      </div>
    </section>
  );
}
