"use client";

import { useState, type Dispatch, type SetStateAction } from "react";
import { createRaidItem, deleteRaidItem, promoteRiskToIssue, suggestRaidItems, updateRaidItem } from "@/lib/api";
import type { RaidItem, RaidLevel, RaidStatus, RaidType } from "@/lib/types";
import TextCell from "./TextCell";
import { IconAlertTriangle, IconPlus, IconTrash } from "./icons";

const TYPES: { id: RaidType; label: string; plural: string; chip: string }[] = [
  { id: "risk", label: "Risk", plural: "Risks", chip: "border-warning-line bg-warning-bg text-warning" },
  { id: "assumption", label: "Assumption", plural: "Assumptions", chip: "border-line bg-surface text-info" },
  { id: "issue", label: "Issue", plural: "Issues", chip: "border-danger/40 bg-danger-bg text-danger" },
  { id: "dependency", label: "Dependency", plural: "Dependencies", chip: "border-line bg-surface text-text-muted" },
];
const STATUSES: RaidStatus[] = ["open", "mitigating", "closed"];
// open = orange, mitigating = yellow, closed = green (theme tokens, see globals.css).
const STATUS_STYLE: Record<RaidStatus, string> = {
  open: "border-status-open-line bg-status-open-bg text-status-open",
  mitigating: "border-status-mitigating-line bg-status-mitigating-bg text-status-mitigating",
  closed: "border-success/40 bg-success-bg text-success",
};
const LEVELS: RaidLevel[] = ["low", "medium", "high"];
const TYPE_ORDER: Record<RaidType, number> = { risk: 0, issue: 1, dependency: 2, assumption: 3 };

function detailOf(e: unknown, fallback: string): string {
  const m = e instanceof Error ? e.message.match(/"detail":"([^"]+)"/) : null;
  return m ? m[1] : fallback;
}

const selectClass =
  "rounded-md border border-line bg-surface-solid px-1.5 py-0.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to";
const inputClass =
  "rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to";

function sortItems(items: RaidItem[]): RaidItem[] {
  return [...items].sort(
    (a, b) =>
      Number(b.review_state === "suggested") - Number(a.review_state === "suggested") ||
      TYPE_ORDER[a.item_type] - TYPE_ORDER[b.item_type] ||
      a.created_at.localeCompare(b.created_at)
  );
}

function TypeChip({ type }: { type: RaidType }) {
  const meta = TYPES.find((t) => t.id === type)!;
  return <span className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium ${meta.chip}`}>{meta.label}</span>;
}

export default function RaidLog({
  projectId,
  items,
  onItemsChange,
}: {
  projectId: string;
  items: RaidItem[];
  // Writes through to the page's list so the tab badge (suggested count) stays current.
  onItemsChange: Dispatch<SetStateAction<RaidItem[] | null>>;
}) {
  const [typeFilter, setTypeFilter] = useState<RaidType | "all">("all");
  const [showClosed, setShowClosed] = useState(false);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<{ item_type: RaidType; title: string; owner: string; probability: RaidLevel | ""; impact: RaidLevel | ""; review_date: string }>({
    item_type: "risk",
    title: "",
    owner: "",
    probability: "",
    impact: "",
    review_date: "",
  });
  const [saving, setSaving] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  function replace(updated: RaidItem) {
    onItemsChange((prev) => sortItems((prev ?? []).map((i) => (i.item_id === updated.item_id ? updated : i))));
  }

  async function patch(item: RaidItem, change: Parameters<typeof updateRaidItem>[1]) {
    setError(null);
    try {
      replace(await updateRaidItem(item.item_id, change));
    } catch {
      setError("Could not save that change - try again.");
    }
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!draft.title.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const created = await createRaidItem(projectId, {
        item_type: draft.item_type,
        title: draft.title.trim(),
        owner: draft.owner.trim() || null,
        probability: draft.probability || null,
        impact: draft.impact || null,
        review_date: draft.review_date || null,
      });
      onItemsChange((prev) => sortItems([...(prev ?? []), created]));
      setDraft({ ...draft, title: "", owner: "", probability: "", impact: "", review_date: "" });
      setAdding(false);
    } catch {
      setError("Could not add the item - try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleSuggest() {
    setSuggesting(true);
    setError(null);
    setNotice(null);
    try {
      const r = await suggestRaidItems(projectId);
      onItemsChange((prev) => sortItems([...(prev ?? []), ...r.created]));
      const read = r.documents_scanned === 0 ? "No new documents to read" : `Read ${r.documents_scanned} document${r.documents_scanned === 1 ? "" : "s"}`;
      const failed = r.documents_failed ? `; ${r.documents_failed} could not be read - click again to retry` : "";
      setNotice(
        r.created.length === 0
          ? `${read}${r.skipped_existing ? ` (${r.skipped_existing} item${r.skipped_existing === 1 ? "" : "s"} already in the log or rejected)` : ""}${failed}.`
          : `${read}: ${r.created.length} suggested${r.skipped_existing ? `, ${r.skipped_existing} already known or rejected` : ""}${failed} - review them above.`
      );
    } catch (e) {
      setError(detailOf(e, "Could not get suggestions - try again."));
    } finally {
      setSuggesting(false);
    }
  }

  async function handlePromote(item: RaidItem) {
    setPromotingId(null);
    setError(null);
    try {
      const { risk, issue } = await promoteRiskToIssue(item.item_id);
      onItemsChange((prev) => sortItems([...(prev ?? []).map((i) => (i.item_id === risk.item_id ? risk : i)), issue]));
    } catch {
      setError("Could not promote that risk - try again.");
    }
  }

  async function handleDelete(item: RaidItem) {
    setConfirmingId(null);
    setError(null);
    try {
      await deleteRaidItem(item.item_id);
      onItemsChange((prev) => (prev ?? []).filter((i) => i.item_id !== item.item_id));
    } catch {
      setError("Could not delete the item - try again.");
    }
  }

  const live = items.filter((i) => i.review_state !== "rejected");
  const suggested = live.filter((i) => i.review_state === "suggested");
  const confirmed = live.filter((i) => i.review_state === "confirmed");
  const visible = confirmed.filter(
    (i) => (typeFilter === "all" || i.item_type === typeFilter) && (showClosed || i.status !== "closed")
  );
  const countOf = (t: RaidType) => confirmed.filter((i) => i.item_type === t && i.status !== "closed").length;

  return (
    <div className="flex flex-col gap-3">
      {suggested.length > 0 && (
        <div className="rounded-xl border border-warning-line bg-warning-bg px-3.5 py-3">
          <p className="flex items-center gap-2 text-[12.5px] font-medium text-text-primary">
            <IconAlertTriangle className="h-3.5 w-3.5 text-warning" />
            {suggested.length === 1 ? "1 item suggested by the assistant" : `${suggested.length} items suggested by the assistant`}
            <span className="font-normal text-text-muted">- accept what is right, reject the rest. Nothing joins the log until you accept it.</span>
          </p>
          <div className="mt-2 flex flex-col gap-1.5">
            {suggested.map((i) => (
              <div key={i.item_id} className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-solid px-2.5 py-1.5">
                <TypeChip type={i.item_type} />
                <span className="min-w-0 flex-1 text-[13px] text-text-primary">
                  {i.title}
                  {i.owner && <span className="text-text-faint"> · {i.owner}</span>}
                  {i.evidence && <span className="mt-0.5 block text-[11.5px] italic text-text-faint">“{i.evidence}”</span>}
                </span>
                <button
                  type="button"
                  onClick={() => patch(i, { review_state: "confirmed" })}
                  className="rounded border border-line bg-surface-solid px-2 py-0.5 text-[12px] text-text-primary hover:border-accent-to"
                >
                  Accept
                </button>
                <button
                  type="button"
                  onClick={() => patch(i, { review_state: "rejected" })}
                  className="rounded border border-line bg-surface-solid px-2 py-0.5 text-[12px] text-text-muted hover:border-danger/50 hover:text-danger"
                >
                  Reject
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => setTypeFilter("all")}
            className={`rounded-full border px-3 py-1 text-[12px] font-medium transition ${
              typeFilter === "all" ? "border-accent-to text-text-primary" : "border-line text-text-muted hover:text-text-primary"
            }`}
          >
            All {confirmed.filter((i) => i.status !== "closed").length}
          </button>
          {TYPES.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTypeFilter(t.id)}
              className={`rounded-full border px-3 py-1 text-[12px] font-medium transition ${
                typeFilter === t.id ? "border-accent-to text-text-primary" : "border-line text-text-muted hover:text-text-primary"
              }`}
            >
              {t.plural} {countOf(t.id)}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <label className="inline-flex items-center gap-1.5 text-[12.5px] text-text-muted">
            <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />
            Show closed
          </label>
          <button
            type="button"
            onClick={handleSuggest}
            disabled={suggesting}
            title="Experimental: the assistant reads the project's SOW and meeting minutes it has not read yet and proposes risks, assumptions, issues and dependencies for you to accept or reject"
            className="hover-tint rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary disabled:opacity-50"
          >
            {suggesting ? "Reading documents…" : "Suggest from documents (experimental)"}
          </button>
          <button
            type="button"
            onClick={() => setAdding((v) => !v)}
            className="hover-tint inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary"
          >
            <IconPlus className="h-3.5 w-3.5" />
            Add item
          </button>
        </div>
      </div>

      {adding && (
        <form onSubmit={handleAdd} className="glass-panel flex flex-wrap items-end gap-2 rounded-xl p-3">
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Type
            <select value={draft.item_type} onChange={(e) => setDraft({ ...draft, item_type: e.target.value as RaidType })} className={selectClass}>
              {TYPES.map((t) => (
                <option key={t.id} value={t.id}>{t.label}</option>
              ))}
            </select>
          </label>
          <label className="flex min-w-[220px] flex-1 flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Title
            <input autoFocus value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="What is it?" className={inputClass} />
          </label>
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Owner
            <input value={draft.owner} onChange={(e) => setDraft({ ...draft, owner: e.target.value })} placeholder="Who tracks it" className={`${inputClass} w-36`} />
          </label>
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Probability
            <select value={draft.probability} onChange={(e) => setDraft({ ...draft, probability: e.target.value as RaidLevel | "" })} className={selectClass}>
              <option value="">-</option>
              {LEVELS.map((l) => (<option key={l} value={l}>{l}</option>))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Impact
            <select value={draft.impact} onChange={(e) => setDraft({ ...draft, impact: e.target.value as RaidLevel | "" })} className={selectClass}>
              <option value="">-</option>
              {LEVELS.map((l) => (<option key={l} value={l}>{l}</option>))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
            Review by
            <input type="date" value={draft.review_date} onChange={(e) => setDraft({ ...draft, review_date: e.target.value })} className={inputClass} />
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
      {notice && <p className="text-[12.5px] text-text-muted">{notice}</p>}

      {visible.length === 0 ? (
        <p className="text-[13px] text-text-faint">
          {confirmed.length === 0
            ? "Nothing logged yet. Add the first risk, assumption, issue or dependency - or leave it to the assistant to suggest them from your documents."
            : "No items match this filter."}
        </p>
      ) : (
        <div className="glass-panel overflow-x-auto rounded-xl">
          <table className="w-full min-w-[900px] text-left text-[13px]">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
                <th className="px-3.5 py-2.5 font-medium">Type</th>
                <th className="px-3.5 py-2.5 font-medium">Title</th>
                <th className="px-3.5 py-2.5 font-medium">Owner</th>
                <th className="px-3.5 py-2.5 font-medium">Status</th>
                <th className="px-3.5 py-2.5 font-medium">Probability</th>
                <th className="px-3.5 py-2.5 font-medium">Impact</th>
                <th className="px-3.5 py-2.5 font-medium">Review by</th>
                <th className="px-3.5 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {visible.map((i) => (
                <tr key={i.item_id} className="border-b border-line last:border-0 hover-tint">
                  <td className="px-3.5 py-2"><TypeChip type={i.item_type} /></td>
                  <td className="max-w-[340px] px-3.5 py-2">
                    <TextCell value={i.title} placeholder="Untitled" onSave={(v) => v && patch(i, { title: v })} />
                    {i.origin === "document" && <div className="mt-0.5 text-[11.5px] text-text-faint">From the SOW</div>}
                    {i.promoted_to && (
                      <div className="mt-0.5 text-[11.5px] text-text-faint">Happened - became an issue (see Issues)</div>
                    )}
                    {i.item_type === "issue" && items.some((r) => r.promoted_to === i.item_id) && (
                      <div className="mt-0.5 text-[11.5px] text-text-faint">Promoted from a risk that happened</div>
                    )}
                  </td>
                  <td className="px-3.5 py-2">
                    <TextCell value={i.owner} placeholder="-" onSave={(v) => patch(i, { owner: v })} />
                  </td>
                  <td className="px-3.5 py-2">
                    <select
                      value={i.status}
                      onChange={(e) => patch(i, { status: e.target.value as RaidStatus })}
                      className={`rounded-md border px-1.5 py-0.5 text-[12.5px] font-medium outline-none ${STATUS_STYLE[i.status]}`}
                    >
                      {STATUSES.map((s) => (<option key={s} value={s}>{s}</option>))}
                    </select>
                  </td>
                  <td className="px-3.5 py-2">
                    <select value={i.probability ?? ""} onChange={(e) => patch(i, { probability: (e.target.value || null) as RaidLevel | null })} className={selectClass}>
                      <option value="">-</option>
                      {LEVELS.map((l) => (<option key={l} value={l}>{l}</option>))}
                    </select>
                  </td>
                  <td className="px-3.5 py-2">
                    <select value={i.impact ?? ""} onChange={(e) => patch(i, { impact: (e.target.value || null) as RaidLevel | null })} className={selectClass}>
                      <option value="">-</option>
                      {LEVELS.map((l) => (<option key={l} value={l}>{l}</option>))}
                    </select>
                  </td>
                  <td className="px-3.5 py-2">
                    <input
                      type="date"
                      value={i.review_date ?? ""}
                      onChange={(e) => patch(i, { review_date: e.target.value || null })}
                      className={selectClass}
                    />
                  </td>
                  <td className="whitespace-nowrap px-3.5 py-2 text-right">
                    {i.item_type === "risk" && !i.promoted_to && i.status !== "closed" &&
                      (promotingId === i.item_id ? (
                        <span className="mr-2 inline-flex items-center gap-1.5">
                          <span className="text-[12px] text-text-faint">Create an issue and close this risk?</span>
                          <button type="button" onClick={() => handlePromote(i)} className="rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-2.5 py-1 text-[12px] font-medium text-white">
                            Confirm
                          </button>
                          <button type="button" onClick={() => setPromotingId(null)} className="hover-tint rounded-lg px-2 py-1 text-[12px] text-text-faint">
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setPromotingId(i.item_id)}
                          title="This risk has happened: create an Issue from it and close the risk"
                          className="hover-tint mr-1 rounded-lg border border-line bg-surface-solid px-2 py-1 text-[12px] font-medium text-text-muted transition hover:text-text-primary"
                        >
                          It happened
                        </button>
                      ))}
                    {confirmingId === i.item_id ? (
                      <span className="inline-flex items-center gap-1.5">
                        <button type="button" onClick={() => handleDelete(i)} className="rounded-lg bg-danger px-2.5 py-1 text-[12px] font-medium text-white">
                          Delete
                        </button>
                        <button type="button" onClick={() => setConfirmingId(null)} className="hover-tint rounded-lg px-2 py-1 text-[12px] text-text-faint">
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setConfirmingId(i.item_id)}
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
