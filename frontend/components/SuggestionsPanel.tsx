"use client";

import { useCallback, useEffect, useState } from "react";
import type { FolderFile, PlanSuggestion, ProjectFolders, SuggestionState } from "@/lib/types";
import { applyHeldPlanFile, approveSuggestions, fetchProjectFolders, fetchSuggestions, rejectSuggestions } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { BTN_PRIMARY, BTN_QUIET, errorText } from "./hours/shared";

const POLL_MS = 5000;

const FIELD_LABEL: Record<string, string> = { owner: "Owner", status: "Status", finish_week: "Finish week" };
const STATUS_TEXT: Record<string, string> = { not_started: "Not started", in_progress: "In progress", completed: "Completed", blocked: "Blocked", on_hold: "On hold" };
const word = (field: string, v: string | number | null | undefined) => (v === null || v === undefined || v === "" ? "none" : field === "status" ? STATUS_TEXT[String(v)] ?? String(v) : String(v));

const STATE: Record<SuggestionState, { text: string; cls: string }> = {
  suggested: { text: "Waiting for you", cls: "bg-warning-bg text-warning" },
  approved: { text: "Approved", cls: "bg-info-bg text-info" },
  waiting: { text: "Waiting for the plan", cls: "bg-warning-bg text-warning" },
  blocked: { text: "Not added", cls: "bg-danger-bg text-danger" },
  merged: { text: "Written to the plan", cls: "bg-info-bg text-info" },
  confirmed: { text: "In the plan", cls: "bg-success-bg text-success" },
  missing: { text: "Missing from the plan", cls: "bg-danger-bg text-danger" },
  applied: { text: "Added to the task list", cls: "bg-success-bg text-success" },
  rejected: { text: "Rejected", cls: "bg-surface text-text-muted" },
};

function ago(iso: string | null): string {
  if (!iso) return "not yet";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return formatDateTime(iso);
}

function What({ s }: { s: PlanSuggestion }) {
  if (s.kind === "new_task") {
    const f = s.fields;
    const bits = [f.owner && `Owner ${f.owner}`, f.finish_week && `finish week ${f.finish_week}`, f.status && STATUS_TEXT[f.status] && STATUS_TEXT[f.status]].filter(Boolean);
    return <span className="text-text-muted">{bits.length ? bits.join(" · ") : "no owner or date mentioned"}</span>;
  }
  return (
    <span className="text-text-muted">
      {Object.entries(s.changes).map(([field, c], i) => (
        <span key={field}>
          {i > 0 && " · "}
          {FIELD_LABEL[field] ?? field}: {word(field, c.from)} <span aria-hidden="true">&rarr;</span><span className="sr-only"> to </span> <span className="text-text-primary">{word(field, c.to)}</span>
        </span>
      ))}
    </span>
  );
}

function FolderCard({ title, path, children }: { title: string; path: string; children: React.ReactNode }) {
  return (
    <div className="glass-panel flex min-w-[260px] flex-1 flex-col gap-1 rounded-xl p-4">
      <h3 className="text-[13px] font-semibold text-text-primary">{title}</h3>
      <code className="w-fit rounded bg-surface px-1.5 py-0.5 text-[12px] text-text-primary">{path}</code>
      <div className="text-[12.5px] text-text-muted">{children}</div>
    </div>
  );
}

function fileLine(f: FolderFile) {
  const s = f.summary as Record<string, number>;
  if (f.status === "applied" && f.kind === "plan") return `${s.tasks ?? 0} tasks${s.added ? `, ${s.added} added` : ""}${s.changed ? `, ${s.changed} changed` : ""}${s.removed ? `, ${s.removed} removed` : ""}`;
  if (f.status === "applied" && f.kind === "meeting") return `${s.suggestions ?? 0} suggestion${s.suggestions === 1 ? "" : "s"}`;
  return f.reason ?? "";
}

export default function SuggestionsPanel({ projectId, onChanged }: { projectId: string; onChanged: () => void }) {
  const [items, setItems] = useState<PlanSuggestion[] | null>(null);
  const [folders, setFolders] = useState<ProjectFolders | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchSuggestions(projectId).then((r) => setItems(r.suggestions)).catch(() => {});
    fetchProjectFolders(projectId).then(setFolders).catch(() => {});
  }, [projectId]);

  useEffect(() => {
    setItems(null);
    setFolders(null);
    load();
    const timer = setInterval(() => !document.hidden && load(), POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  async function act(key: string, fn: () => Promise<Record<string, unknown>>, done: (r: Record<string, unknown>) => string) {
    setBusy(key);
    setError(null);
    setMessage(null);
    try {
      const r = await fn();
      setMessage(done(r));
      load();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const approve = (ids: string[], key: string) =>
    act(key, () => approveSuggestions(projectId, ids), (r) => {
      const merged = Number(r.merged ?? 0) + Number(r.applied_to_app ?? 0);
      return `Approved ${r.approved}. ${merged ? `${merged} written to the plan.` : ""}${r.waiting ? ` ${r.waiting} waiting for the plan file (see below).` : ""}${r.blocked ? ` ${r.blocked} could not be added (see below).` : ""}`.trim();
    });
  const reject = (ids: string[], key: string) => act(key, () => rejectSuggestions(projectId, ids), (r) => `Rejected ${r.rejected}.`);

  const pending = (items ?? []).filter((s) => s.state === "suggested");
  const onTheWay = (items ?? []).filter((s) => ["approved", "waiting", "blocked", "merged", "missing"].includes(s.state));
  const done = (items ?? []).filter((s) => ["confirmed", "applied", "rejected"].includes(s.state));
  const groups = Array.from(new Set(pending.map((s) => s.source_name ?? "A meeting")));
  const heldPlan = folders?.plan.files[0]?.status === "held" ? folders.plan.files[0] : null;
  const recent = [...(folders?.plan.files ?? []), ...(folders?.meetings.files ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 8);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-3">
        <FolderCard title="Project plan" path={folders?.plan.folder ?? `projects/${projectId}/plan`}>
          {folders?.plan.current_file ? (
            <>
              <b className="font-medium text-text-primary">{folders.plan.current_file}</b> is the plan. Edit it in Excel; changes are read within seconds.
              {folders.plan.open_in_excel && <span className="mt-1 block text-warning">It is open in Excel now, so approved changes wait until it is closed.</span>}
              {!folders.plan.writable && <span className="mt-1 block text-warning">Only .xlsx plans can be updated automatically; approved changes will not be added to this one.</span>}
            </>
          ) : (
            <>No plan file yet. Copy the plan (Excel) into this folder.</>
          )}
        </FolderCard>
        <FolderCard title="Meeting transcripts" path={folders?.meetings.folder ?? `projects/${projectId}/meetings`}>
          Drop a transcript here (.txt, .md, .vtt, .srt or .docx). The tasks in it come back below as suggestions for you to approve. Nothing changes until you do.
        </FolderCard>
      </div>

      {error && <p className="text-[13px] text-danger">{error}</p>}
      {message && <p className="text-[13px] text-success">{message}</p>}
      {heldPlan && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning-line bg-warning-bg px-3 py-2 text-[12.5px] text-warning">
          <span>The latest version of {heldPlan.filename} was not applied: {heldPlan.reason}</span>
          <button type="button" disabled={busy === heldPlan.file_id} onClick={() => act(heldPlan.file_id, () => applyHeldPlanFile(projectId, heldPlan.file_id), () => "Plan applied.")} className={BTN_QUIET}>
            Apply anyway
          </button>
        </div>
      )}

      <section className="flex flex-col gap-3" aria-label="Waiting for your decision">
        <h2 className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Waiting for your decision{pending.length ? ` (${pending.length})` : ""}</h2>
        {items && pending.length === 0 && <p className="text-[13px] text-text-faint">Nothing waiting. Suggestions appear here after a transcript is dropped in the meetings folder.</p>}
        {groups.map((g) => {
          const rows = pending.filter((s) => (s.source_name ?? "A meeting") === g);
          const ids = rows.map((s) => s.suggestion_id);
          return (
            <div key={g} className="glass-panel overflow-hidden rounded-xl">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
                <p className="text-[12.5px] text-text-muted">
                  From <b className="font-medium text-text-primary">{g}</b>, {rows.length} suggestion{rows.length === 1 ? "" : "s"}
                </p>
                <div className="flex items-center gap-2">
                  <button type="button" disabled={!!busy} onClick={() => reject(ids, g)} className={BTN_QUIET}>Reject all</button>
                  <button type="button" disabled={!!busy} onClick={() => approve(ids, g)} className={BTN_PRIMARY}>{busy === g ? "Working…" : "Approve all"}</button>
                </div>
              </div>
              <ul className="divide-y divide-line">
                {rows.map((s) => (
                  <li key={s.suggestion_id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 text-[13px]">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${s.kind === "new_task" ? "bg-info-bg text-info" : "bg-surface text-text-muted"}`}>{s.kind === "new_task" ? "New task" : "Change"}</span>
                        <span className="font-medium text-text-primary">{s.task_text}</span>
                      </div>
                      <p className="mt-0.5 text-[12.5px]"><What s={s} /></p>
                      {s.fields.evidence && <p className="mt-0.5 text-[12px] italic text-text-faint">&ldquo;{s.fields.evidence}&rdquo;</p>}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <button type="button" disabled={!!busy} onClick={() => reject([s.suggestion_id], s.suggestion_id)} className="text-[12.5px] text-text-muted underline hover:text-text-primary disabled:opacity-50">Reject</button>
                      <button type="button" disabled={!!busy} onClick={() => approve([s.suggestion_id], s.suggestion_id)} className={BTN_QUIET}>{busy === s.suggestion_id ? "Working…" : "Approve"}</button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </section>

      {onTheWay.length > 0 && (
        <section className="flex flex-col gap-2" aria-label="On their way into the plan">
          <h2 className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Approved, on their way into the plan</h2>
          <ul className="glass-panel divide-y divide-line rounded-xl">
            {onTheWay.map((s) => (
              <li key={s.suggestion_id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-2.5 text-[13px]">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${STATE[s.state].cls}`}>{STATE[s.state].text}</span>
                    <span className="font-medium text-text-primary">{s.task_text}</span>
                  </div>
                  <p className="mt-0.5 text-[12.5px]"><What s={s} /></p>
                  {s.fields.evidence && <p className="mt-0.5 text-[12px] italic text-text-faint">&ldquo;{s.fields.evidence}&rdquo;</p>}
                  {s.reason && <p className="mt-0.5 text-[12.5px] text-text-muted">{s.reason}</p>}
                </div>
                {(s.state === "blocked" || s.state === "missing") && (
                  <button type="button" disabled={!!busy} onClick={() => approve([s.suggestion_id], s.suggestion_id)} className={BTN_QUIET}>Apply again</button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {done.length > 0 && (
        <details className="text-[12.5px]">
          <summary className="cursor-pointer text-text-muted">Decided ({done.length})</summary>
          <ul className="mt-2 flex flex-col gap-1.5">
            {done.slice(0, 30).map((s) => (
              <li key={s.suggestion_id} className="flex flex-wrap items-center gap-2">
                <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${STATE[s.state].cls}`}>{STATE[s.state].text}</span>
                <span className="text-text-primary">{s.task_text}</span>
                <span className="text-text-faint">{s.jira_key ? `Jira ${s.jira_key}` : ""}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {recent.length > 0 && (
        <details className="text-[12.5px]">
          <summary className="cursor-pointer text-text-muted">Files read recently ({recent.length}){folders ? `, last checked ${ago(folders.last_checked_at)}` : ""}</summary>
          <ul className="mt-2 flex flex-col gap-1.5">
            {recent.map((f) => (
              <li key={f.file_id} className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-surface px-1.5 py-0.5 text-[11px] text-text-muted">{f.kind === "plan" ? "Plan" : "Meeting"}</span>
                <span className="text-text-primary">{f.filename}</span>
                <span className={f.status === "applied" ? "text-success" : f.status === "held" || f.status === "failed" ? "text-warning" : "text-text-faint"}>
                  {f.status === "applied" ? "read" : f.status}
                </span>
                <span className="text-text-faint">{fileLine(f)} · {formatDateTime(f.created_at)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
