"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { InboxItem, TimesheetImportPreview, TimesheetInbox } from "@/lib/types";
import { fetchImportReview, fetchTimesheetInbox, reassignImportGroup, undoHoursImport } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { TimesheetReview } from "./ImportReview";
import { INPUT, errorText, h } from "./shared";

const OTHER = "__other__";
const POLL_MS = 5000;

// Polls the project's timesheet folder status. onNewHours runs when a file has been picked up (or undone) since the last look,
// so the report and the grid can reload without the person doing anything.
export function useTimesheetInbox(projectId: string, onNewHours: () => void) {
  const [inbox, setInbox] = useState<TimesheetInbox | null>(null);
  const seen = useRef<string | null | undefined>(undefined);
  const notify = useRef(onNewHours);
  notify.current = onNewHours;

  const load = useCallback(() => {
    if (!projectId) return;
    fetchTimesheetInbox(projectId)
      .then((i) => {
        if (seen.current !== undefined && i.last_applied_at !== seen.current) notify.current();
        seen.current = i.last_applied_at;
        setInbox(i);
      })
      .catch(() => {});
  }, [projectId]);

  useEffect(() => {
    seen.current = undefined;
    setInbox(null);
    load();
    const timer = setInterval(() => {
      if (!document.hidden) load();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  return { inbox, reload: load };
}

const STATUS: Record<string, { text: string; cls: string }> = {
  applied: { text: "Imported", cls: "bg-success-bg text-success" },
  held: { text: "Needs review", cls: "bg-warning-bg text-warning" },
  duplicate: { text: "Already imported", cls: "bg-surface text-text-muted" },
  failed: { text: "Failed", cls: "bg-danger-bg text-danger" },
  undone: { text: "Undone", cls: "bg-surface text-text-muted" },
};

function ago(iso: string | null): string {
  if (!iso) return "not yet";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return formatDateTime(iso);
}

export default function IncomingTimesheets({ projectId, inbox, reload, onChanged }: { projectId: string; inbox: TimesheetInbox | null; reload: () => void; onChanged: () => void }) {
  const [review, setReview] = useState<TimesheetImportPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      reload();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function openReview(item: InboxItem) {
    setError(null);
    try {
      setReview(await fetchImportReview(item.import_id));
    } catch (e) {
      setError(errorText(e));
    }
  }

  const items = inbox?.items ?? [];
  const toCheck = inbox?.to_check ?? [];

  return (
    <section className="glass-panel flex flex-col gap-3 rounded-xl p-4" aria-label="Incoming timesheets">
      <div>
        <h3 className="text-[13px] font-semibold text-text-primary">Incoming timesheets</h3>
        {inbox && !inbox.enabled ? (
          <p className="text-[12.5px] text-warning">Automatic pickup is switched off on this server, so nothing is read from the folder.</p>
        ) : (
          <p className="text-[12.5px] text-text-muted">
            Drop a timesheet (Excel or CSV) into <code className="rounded bg-surface px-1 py-0.5 text-[12px]">{inbox?.folder ?? `timesheets/${projectId}`}</code>. Its name must start
            with <b>{projectId}</b>. It is read within a few seconds{inbox ? `; last checked ${ago(inbox.last_checked_at)}` : ""}. Files for unknown people or with
            unreadable rows wait here for review and nothing is saved from them.
          </p>
        )}
      </div>

      {error && <p className="text-[13px] text-danger">{error}</p>}
      {message && <p className="text-[13px] text-success">{message}</p>}

      {review && (
        <TimesheetReview
          preview={review}
          onCancel={() => setReview(null)}
          onDone={(m) => {
            setReview(null);
            setMessage(m);
            reload();
            onChanged();
          }}
        />
      )}

      {inbox && items.length === 0 && <p className="text-[12.5px] text-text-faint">No files picked up yet.</p>}
      {items.length > 0 && (
        <ul className="flex flex-col divide-y divide-line rounded-lg border border-line text-[12.5px]">
          {items.slice(0, 8).map((i) => {
            const st = STATUS[i.status] ?? { text: i.status, cls: "bg-surface text-text-muted" };
            return (
              <li key={i.import_id} className="flex flex-wrap items-start justify-between gap-2 px-3 py-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate font-medium text-text-primary">{i.filename ?? "file"}</span>
                    <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${st.cls}`}>{st.text}</span>
                  </div>
                  <p className="text-text-faint">
                    {formatDateTime(i.applied_at ?? i.created_at)}
                    {i.status === "applied" && i.hours !== null ? ` · ${h(i.hours)} h in ${i.entries ?? 0} entries${i.replaced ? ` (${i.replaced} replaced)` : ""}` : ""}
                    {i.to_check_count > 0 ? ` · ${i.to_check_count} to check` : ""}
                  </p>
                  {i.reason && i.status !== "applied" && <p className="text-text-muted">{i.reason}</p>}
                </div>
                <div className="flex items-center gap-3">
                  {i.can_review && (
                    <button type="button" onClick={() => openReview(i)} className="text-[12px] text-text-muted underline hover:text-text-primary">
                      Review
                    </button>
                  )}
                  {i.status === "applied" && (
                    <button
                      type="button"
                      disabled={busy === i.import_id}
                      onClick={() => run(i.import_id, () => undoHoursImport(i.import_id))}
                      className="text-[12px] text-text-muted underline hover:text-text-primary disabled:opacity-50"
                    >
                      Undo
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {toCheck.length > 0 && (
        <div className="flex flex-col gap-2">
          <div>
            <h4 className="text-[12.5px] font-semibold text-text-primary">Check these matches</h4>
            <p className="text-[12px] text-text-faint">
              These lines were counted, but the task was a guess. Pick a different task to move the hours; the totals do not change.
            </p>
          </div>
          <div className="overflow-x-auto rounded-lg border border-line">
            <table className="w-full min-w-[640px] text-left text-[12.5px]">
              <thead>
                <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
                  <th className="px-3 py-2 font-medium">As written</th>
                  <th className="px-3 py-2 font-medium">Person</th>
                  <th className="px-3 py-2 text-right font-medium">Hours</th>
                  <th className="px-3 py-2 font-medium">Counted toward</th>
                </tr>
              </thead>
              <tbody>
                {toCheck.map((c) => {
                  const key = `${c.import_id}|${c.label}|${c.person}`;
                  return (
                    <tr key={key} className="border-b border-line last:border-0">
                      <td className="max-w-[260px] px-3 py-1.5 text-text-primary">{c.label}</td>
                      <td className="px-3 py-1.5">{c.person}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{h(c.hours)}</td>
                      <td className="px-3 py-1.5">
                        <select
                          aria-label={`Task for "${c.label}" (${c.person})`}
                          value={c.task_id ?? OTHER}
                          disabled={busy === key}
                          onChange={(e) => run(key, () => reassignImportGroup(c.import_id, c.label, c.person, e.target.value === OTHER ? null : e.target.value))}
                          className={`${INPUT} w-full max-w-[340px]`}
                        >
                          <option value={OTHER}>Other (no listed task)</option>
                          {(inbox?.tasks ?? []).map((t) => (
                            <option key={t.task_id} value={t.task_id}>{t.task_text.length > 70 ? `${t.task_text.slice(0, 70)}…` : t.task_text}</option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
