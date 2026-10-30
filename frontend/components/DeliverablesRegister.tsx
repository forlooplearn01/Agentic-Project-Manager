"use client";

import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import {
  assignTasks,
  createDeliverable,
  createDeliverablesFromTasks,
  deleteDeliverable,
  fetchDeliverables,
  fetchProjectTasks,
  fetchSimilarTasks,
  suggestAssignments,
  suggestDeliverables,
  updateDeliverable,
} from "@/lib/api";
import type { AssignmentProposal, Deliverable, SimilarDeliverable, Status, Task } from "@/lib/types";
import TextCell from "./TextCell";
import { IconPlus, IconTrash } from "./icons";

const STATUSES: { id: Status; label: string; style: string }[] = [
  { id: "not_started", label: "Not started", style: "border-line bg-surface text-text-muted" },
  { id: "in_progress", label: "In progress", style: "border-line bg-info-bg text-info" },
  { id: "completed", label: "Completed", style: "border-success/40 bg-success-bg text-success" },
  { id: "blocked", label: "Blocked", style: "border-danger/40 bg-danger-bg text-danger" },
  { id: "on_hold", label: "On hold", style: "border-warning-line bg-warning-bg text-warning" },
];
const ORIGIN_LABEL: Record<Deliverable["origin"], string> = {
  manual: "Added by hand",
  task: "From a task",
  document: "From the SOW",
  agent: "Suggested, accepted",
};

// The API reports problems as `400 Bad Request: {"detail":"..."}` - show just the human sentence.
function detailOf(e: unknown, fallback: string): string {
  const m = e instanceof Error ? e.message.match(/"detail":"([^"]+)"/) : null;
  return m ? m[1] : fallback;
}

const inputClass =
  "rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to";

function numberKey(d: Deliverable): [number, number[], string] {
  const ok = d.number && /^\d+(\.\d+)*$/.test(d.number);
  return [ok ? 0 : 1, ok ? d.number!.split(".").map(Number) : [], d.created_at];
}
function sortDeliverables(items: Deliverable[]): Deliverable[] {
  return [...items].sort((a, b) => {
    const [ga, na, ca] = numberKey(a);
    const [gb, nb, cb] = numberKey(b);
    if (ga !== gb) return ga - gb;
    for (let i = 0; i < Math.max(na.length, nb.length); i++) {
      if ((na[i] ?? -1) !== (nb[i] ?? -1)) return (na[i] ?? -1) - (nb[i] ?? -1);
    }
    return ca.localeCompare(cb);
  });
}

function TaskPicker({
  projectId,
  alreadyPromoted,
  onDone,
  onClose,
}: {
  projectId: string;
  alreadyPromoted: Set<string>;
  onDone: (created: Deliverable[]) => void;
  onClose: () => void;
}) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [similar, setSimilar] = useState<Record<string, SimilarDeliverable>>({});
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchProjectTasks(projectId)
      .then((r) => setTasks(r.tasks))
      .catch(() => setError("Could not load tasks."));
    fetchSimilarTasks(projectId)
      .then((r) => setSimilar(r.similar))
      .catch(() => {}); // the hints are a bonus - the picker works without them
  }, [projectId]);

  const available = (tasks ?? []).filter(
    (t) => !alreadyPromoted.has(t.task_id) && (!query || t.task_text.toLowerCase().includes(query.toLowerCase()))
  );

  async function add() {
    setSaving(true);
    setError(null);
    try {
      const r = await createDeliverablesFromTasks(projectId, [...picked]);
      onDone(r.created);
    } catch {
      setError("Could not add those - try again.");
      setSaving(false);
    }
  }

  return (
    <div className="glass-panel flex flex-col gap-2.5 rounded-xl p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12.5px] text-text-muted">
          Pick the tasks that are really deliverables. Title, owner and deadline are copied; the task itself stays a task.
        </p>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tasks..." className={`${inputClass} w-56`} />
      </div>
      {!tasks && !error && <p className="text-[13px] text-text-faint">Loading…</p>}
      {tasks && available.length === 0 && <p className="text-[13px] text-text-faint">No tasks to pick from.</p>}
      <div className="flex max-h-64 flex-col gap-0.5 overflow-y-auto">
        {available.map((t) => (
          <label key={t.task_id} className="hover-tint flex cursor-pointer items-start gap-2 rounded-md px-2 py-1 text-[13px]">
            <input
              type="checkbox"
              className="mt-1"
              checked={picked.has(t.task_id)}
              onChange={(e) =>
                setPicked((prev) => {
                  const next = new Set(prev);
                  if (e.target.checked) next.add(t.task_id);
                  else next.delete(t.task_id);
                  return next;
                })
              }
            />
            <span className="min-w-0 flex-1 text-text-primary">
              {t.task_text}
              {similar[t.task_id] && (
                <span
                  className="ml-2 inline-block rounded border border-warning-line bg-warning-bg px-1.5 py-0.5 text-[11px] text-warning"
                  title="Similar wording is not proof of a duplicate - related sub-tasks look similar too. You decide."
                >
                  Similar to {similar[t.task_id].number ?? "a deliverable"} {similar[t.task_id].title.slice(0, 32)}
                </span>
              )}
            </span>
            {t.owner && <span className="shrink-0 text-[12px] text-text-faint">{t.owner}</span>}
          </label>
        ))}
      </div>
      {error && <p className="text-[12.5px] text-danger">{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className="hover-tint rounded-lg px-3 py-1.5 text-[12.5px] text-text-muted">
          Cancel
        </button>
        <button
          type="button"
          onClick={add}
          disabled={saving || picked.size === 0}
          className="rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-3 py-1.5 text-[12.5px] font-medium text-white transition disabled:opacity-50"
        >
          {saving ? "Adding…" : `Add ${picked.size || ""} as deliverable${picked.size === 1 ? "" : "s"}`}
        </button>
      </div>
    </div>
  );
}

type ReviewRow = AssignmentProposal & { checked: boolean };

function AssignReview({
  projectId,
  deliverables,
  onApplied,
  onClose,
}: {
  projectId: string;
  deliverables: Deliverable[];
  onApplied: (n: number) => void;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<ReviewRow[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    suggestAssignments(projectId)
      // Only high-confidence proposals start checked: in testing they were right far more often than medium ones.
      .then((r) => setRows(r.proposals.map((p) => ({ ...p, checked: p.confidence === "high" && !!p.deliverable_id }))))
      .catch((e) => setError(detailOf(e, "Could not get proposals.")));
  }, [projectId]);

  function update(taskId: string, change: Partial<ReviewRow>) {
    setRows((prev) => (prev ?? []).map((r) => (r.task_id === taskId ? { ...r, ...change } : r)));
  }

  const selected = (rows ?? []).filter((r) => r.checked && r.deliverable_id);

  async function apply() {
    setSaving(true);
    setError(null);
    try {
      await assignTasks(projectId, selected.map((r) => ({ task_id: r.task_id, deliverable_id: r.deliverable_id })));
      onApplied(selected.length);
    } catch {
      setError("Could not apply those - try again.");
      setSaving(false);
    }
  }

  return (
    <div className="glass-panel flex flex-col gap-2.5 rounded-xl p-3.5">
      <p className="text-[12.5px] text-text-muted">
        Proposed deliverable for each task that has none yet. High-confidence proposals are pre-selected; check the others only if you agree, or pick a
        different deliverable. Nothing changes until you apply.
      </p>
      {!rows && !error && <p className="text-[13px] text-text-faint">Asking the assistant… this can take a few seconds.</p>}
      {rows && rows.length === 0 && <p className="text-[13px] text-text-faint">Every task already has a deliverable.</p>}
      <div className="flex max-h-80 flex-col gap-0.5 overflow-y-auto">
        {(rows ?? []).map((r) => (
          <div key={r.task_id} className="hover-tint flex items-center gap-2 rounded-md px-2 py-1 text-[13px]">
            <input type="checkbox" checked={r.checked} disabled={!r.deliverable_id} onChange={(e) => update(r.task_id, { checked: e.target.checked })} />
            <span className="min-w-0 flex-1 truncate text-text-primary" title={r.task_text}>{r.task_text}</span>
            <span className="shrink-0 text-[11px] text-text-faint">
              {r.deliverable_id ? (r.method === "number" ? "cites its number" : `${r.confidence} confidence`) : "no clear match"}
            </span>
            <select
              value={r.deliverable_id ?? ""}
              onChange={(e) => update(r.task_id, { deliverable_id: e.target.value || null, checked: !!e.target.value })}
              className="w-44 shrink-0 rounded-md border border-line bg-surface-solid px-1.5 py-0.5 text-[12px] text-text-primary outline-none focus:border-accent-to"
            >
              <option value="">-</option>
              {deliverables.map((d) => (
                <option key={d.deliverable_id} value={d.deliverable_id}>
                  {d.number ? `${d.number} ` : ""}
                  {d.title.slice(0, 32)}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>
      {error && <p className="text-[12.5px] text-danger">{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className="hover-tint rounded-lg px-3 py-1.5 text-[12.5px] text-text-muted">
          Cancel
        </button>
        <button
          type="button"
          onClick={apply}
          disabled={saving || selected.length === 0}
          className="rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-3 py-1.5 text-[12.5px] font-medium text-white transition disabled:opacity-50"
        >
          {saving ? "Applying…" : `Apply ${selected.length || ""} assignment${selected.length === 1 ? "" : "s"}`}
        </button>
      </div>
    </div>
  );
}

export default function DeliverablesRegister({
  projectId,
  items,
  onItemsChange,
}: {
  projectId: string;
  items: Deliverable[];
  onItemsChange: Dispatch<SetStateAction<Deliverable[] | null>>;
}) {
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState({ number: "", title: "", owner: "", deadline: "" });
  const [saving, setSaving] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function replace(updated: Deliverable) {
    onItemsChange((prev) => sortDeliverables((prev ?? []).map((d) => (d.deliverable_id === updated.deliverable_id ? updated : d))));
  }

  async function patch(d: Deliverable, change: Parameters<typeof updateDeliverable>[1]) {
    setError(null);
    try {
      replace(await updateDeliverable(d.deliverable_id, change));
    } catch {
      setError("Could not save that change - is the number already used?");
    }
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!draft.title.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const created = await createDeliverable(projectId, {
        title: draft.title.trim(),
        number: draft.number.trim() || null,
        owner: draft.owner.trim() || null,
        deadline: draft.deadline.trim() || null,
      });
      onItemsChange((prev) => sortDeliverables([...(prev ?? []), created]));
      setDraft({ number: "", title: "", owner: "", deadline: "" });
      setAdding(false);
    } catch {
      setError("Could not add it - is that number already used?");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(d: Deliverable) {
    setConfirmingId(null);
    setError(null);
    try {
      await deleteDeliverable(d.deliverable_id);
      onItemsChange((prev) => (prev ?? []).filter((x) => x.deliverable_id !== d.deliverable_id));
    } catch {
      setError("Could not delete it - try again.");
    }
  }

  async function handleSuggest() {
    setSuggesting(true);
    setError(null);
    setNotice(null);
    try {
      const r = await suggestDeliverables(projectId);
      onItemsChange((prev) => sortDeliverables([...(prev ?? []), ...r.created]));
      setNotice(
        r.created.length === 0
          ? `Nothing new to suggest${r.skipped_existing ? ` (${r.skipped_existing} already known or rejected).` : "."}`
          : `${r.created.length} suggested${r.skipped_existing ? `, ${r.skipped_existing} already known or rejected` : ""} - review them below.`
      );
    } catch (e) {
      setError(detailOf(e, "Could not get suggestions - try again."));
    } finally {
      setSuggesting(false);
    }
  }

  async function review(d: Deliverable, decision: "confirmed" | "rejected") {
    setError(null);
    try {
      const updated = await updateDeliverable(d.deliverable_id, { review_state: decision });
      onItemsChange((prev) =>
        sortDeliverables((prev ?? []).filter((x) => x.deliverable_id !== d.deliverable_id).concat(decision === "rejected" ? [] : [updated]))
      );
    } catch {
      setError("Could not save that - try again.");
    }
  }

  async function reload() {
    const r = await fetchDeliverables(projectId);
    onItemsChange(sortDeliverables(r.deliverables));
  }

  const confirmed = items.filter((d) => d.review_state === "confirmed");
  const suggested = items.filter((d) => d.review_state === "suggested");
  const promoted = new Set(confirmed.map((d) => d.source_task_id).filter((x): x is string => !!x));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          onClick={handleSuggest}
          disabled={suggesting}
          title="Experimental: the assistant reads the SOW and proposes deliverables for you to accept or reject"
          className="hover-tint rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary disabled:opacity-50"
        >
          {suggesting ? "Reading the SOW…" : "Suggest from the SOW (experimental)"}
        </button>
        <button
          type="button"
          onClick={() => {
            setAssigning((v) => !v);
            setPicking(false);
            setAdding(false);
          }}
          disabled={confirmed.length === 0}
          title={confirmed.length === 0 ? "Add a deliverable first" : "Propose which deliverable each task belongs to"}
          className="hover-tint rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary disabled:opacity-50"
        >
          Assign tasks
        </button>
        <button
          type="button"
          onClick={() => {
            setPicking((v) => !v);
            setAdding(false);
            setAssigning(false);
          }}
          className="hover-tint rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary"
        >
          Add from tasks
        </button>
        <button
          type="button"
          onClick={() => {
            setAdding((v) => !v);
            setPicking(false);
            setAssigning(false);
          }}
          className="hover-tint inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary"
        >
          <IconPlus className="h-3.5 w-3.5" />
          Add deliverable
        </button>
      </div>

      {notice && <p className="text-[12.5px] text-text-muted">{notice}</p>}

      {suggested.length > 0 && (
        <div className="rounded-xl border border-warning-line bg-warning-bg px-3.5 py-3">
          <p className="text-[12.5px] font-medium text-text-primary">
            {suggested.length === 1 ? "1 deliverable suggested by the assistant" : `${suggested.length} deliverables suggested by the assistant`}
            <span className="font-normal text-text-muted"> - experimental; accept what is right, reject the rest. Nothing joins the register or the RACI matrix until you accept it.</span>
          </p>
          <div className="mt-2 flex flex-col gap-1.5">
            {suggested.map((d) => (
              <div key={d.deliverable_id} className="flex flex-wrap items-start gap-2 rounded-lg border border-line bg-surface-solid px-2.5 py-1.5">
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] text-text-primary">
                    {d.title}
                    {d.owner && <span className="ml-2 text-[12px] text-text-faint">{d.owner}</span>}
                    {d.deadline && <span className="ml-2 text-[12px] text-text-faint">due {d.deadline}</span>}
                  </p>
                  {d.evidence ? (
                    <p className="mt-0.5 text-[12px] italic text-text-faint">&ldquo;{d.evidence}&rdquo;</p>
                  ) : (
                    <p className="mt-0.5 text-[12px] text-text-faint">No supporting quote found in the SOW.</p>
                  )}
                </div>
                <button type="button" onClick={() => review(d, "confirmed")} className="rounded border border-line bg-surface-solid px-2 py-0.5 text-[12px] text-text-primary hover:border-accent-to">
                  Accept
                </button>
                <button type="button" onClick={() => review(d, "rejected")} className="rounded border border-line bg-surface-solid px-2 py-0.5 text-[12px] text-text-muted hover:border-danger/50 hover:text-danger">
                  Reject
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {assigning && (
        <AssignReview
          projectId={projectId}
          deliverables={confirmed}
          onClose={() => setAssigning(false)}
          onApplied={async (n) => {
            setAssigning(false);
            setNotice(`${n} task${n === 1 ? "" : "s"} assigned.`);
            await reload();
          }}
        />
      )}

      {picking && (
        <TaskPicker
          projectId={projectId}
          alreadyPromoted={promoted}
          onClose={() => setPicking(false)}
          onDone={(created) => {
            onItemsChange((prev) => sortDeliverables([...(prev ?? []), ...created]));
            setPicking(false);
            setNotice(null);
          }}
        />
      )}

      {adding && (
        <form onSubmit={handleAdd} className="glass-panel flex flex-wrap items-end gap-2 rounded-xl p-3">
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Number
            <input value={draft.number} onChange={(e) => setDraft({ ...draft, number: e.target.value })} placeholder="e.g. 4.6" className={`${inputClass} w-20`} />
          </label>
          <label className="flex min-w-[240px] flex-1 flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Title
            <input autoFocus value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="What is delivered?" className={inputClass} />
          </label>
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Owner
            <input value={draft.owner} onChange={(e) => setDraft({ ...draft, owner: e.target.value })} placeholder="Who delivers it" className={`${inputClass} w-44`} />
          </label>
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Deadline
            <input value={draft.deadline} onChange={(e) => setDraft({ ...draft, deadline: e.target.value })} placeholder="e.g. end of Week 8" className={`${inputClass} w-44`} />
          </label>
          <div className="flex gap-2">
            <button type="button" onClick={() => setAdding(false)} className="hover-tint rounded-lg px-3 py-1.5 text-[12.5px] text-text-muted">
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !draft.title.trim()}
              className="rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-3 py-1.5 text-[12.5px] font-medium text-white transition disabled:opacity-50"
            >
              {saving ? "Adding…" : "Add"}
            </button>
          </div>
        </form>
      )}

      {error && <p className="text-[12.5px] text-danger">{error}</p>}

      {confirmed.length === 0 ? (
        <div className="glass-panel rounded-xl p-5 text-[13px] text-text-muted">
          <p className="font-medium text-text-primary">No deliverables yet.</p>
          <p className="mt-1">
            A deliverable is something you hand over and get signed off. Add them by hand, or use <span className="font-medium">Add from tasks</span> to pick
            them out of the tasks already extracted from your documents. If a SOW has a numbered Deliverables section, they appear here automatically.
          </p>
        </div>
      ) : (
        <div className="glass-panel overflow-x-auto rounded-xl">
          <table className="w-full min-w-[900px] text-left text-[13px]">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
                <th className="px-3.5 py-2.5 font-medium">#</th>
                <th className="px-3.5 py-2.5 font-medium">Deliverable</th>
                <th className="px-3.5 py-2.5 font-medium">Owner</th>
                <th className="px-3.5 py-2.5 font-medium">Deadline</th>
                <th className="px-3.5 py-2.5 font-medium">Status</th>
                <th className="px-3.5 py-2.5 font-medium">Tasks</th>
                <th className="px-3.5 py-2.5 font-medium">Source</th>
                <th className="px-3.5 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {confirmed.map((d) => (
                <tr key={d.deliverable_id} className="border-b border-line last:border-0 hover-tint">
                  <td className="w-20 px-3.5 py-2 font-mono text-[12px]">
                    <TextCell value={d.number} placeholder="-" onSave={(v) => patch(d, { number: v })} />
                  </td>
                  <td className="max-w-[360px] px-3.5 py-2">
                    <TextCell value={d.title} placeholder="Untitled" onSave={(v) => v && patch(d, { title: v })} />
                  </td>
                  <td className="px-3.5 py-2">
                    <TextCell value={d.owner} placeholder="-" onSave={(v) => patch(d, { owner: v })} />
                  </td>
                  <td className="px-3.5 py-2">
                    <TextCell value={d.deadline} placeholder="-" onSave={(v) => patch(d, { deadline: v })} />
                  </td>
                  <td className="px-3.5 py-2">
                    <select
                      value={d.status}
                      onChange={(e) => patch(d, { status: e.target.value as Status })}
                      className={`rounded-md border px-1.5 py-0.5 text-[12.5px] font-medium outline-none ${STATUSES.find((s) => s.id === d.status)?.style}`}
                    >
                      {STATUSES.map((s) => (
                        <option key={s.id} value={s.id}>{s.label}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3.5 py-2 text-[12.5px] text-text-muted">{d.task_count || "-"}</td>
                  <td className="px-3.5 py-2 text-[12px] text-text-faint">{ORIGIN_LABEL[d.origin]}</td>
                  <td className="whitespace-nowrap px-3.5 py-2 text-right">
                    {confirmingId === d.deliverable_id ? (
                      <span className="inline-flex items-center gap-1.5">
                        <button type="button" onClick={() => handleDelete(d)} className="rounded-lg bg-danger px-2.5 py-1 text-[12px] font-medium text-white">
                          Delete
                        </button>
                        <button type="button" onClick={() => setConfirmingId(null)} className="hover-tint rounded-lg px-2 py-1 text-[12px] text-text-faint">
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setConfirmingId(d.deliverable_id)}
                        title="Delete"
                        className="hover-tint rounded-lg p-1.5 text-text-faint transition hover:text-danger"
                      >
                        <IconTrash className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
