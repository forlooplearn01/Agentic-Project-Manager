"use client";

import { useState } from "react";
import { createPlanTask } from "@/lib/api";
import { BTN_PRIMARY, BTN_QUIET, INPUT, errorText } from "@/components/hours/shared";

export default function AddTaskPanel({
  projectId,
  owners,
  phases,
  onAdded,
  onCancel,
}: {
  projectId: string;
  owners: string[];
  phases: string[];
  onAdded: () => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  const [owner, setOwner] = useState("");
  const [phase, setPhase] = useState("");
  const [estimate, setEstimate] = useState("");
  const [start, setStart] = useState("");
  const [finish, setFinish] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const num = (v: string) => (v.trim() === "" ? null : Number(v));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await createPlanTask(projectId, {
        task_text: text,
        owner: owner || undefined,
        phase: phase || undefined,
        estimate_hours: num(estimate),
        plan_start_week: num(start),
        plan_finish_week: num(finish),
      });
      onAdded();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="glass-panel flex flex-col gap-3 rounded-xl p-4" aria-label="Add a task to the plan">
      <h3 className="text-[13px] font-semibold text-text-primary">Add a task to the plan</h3>
      <div className="grid gap-3 sm:grid-cols-[2fr_1fr_1fr]">
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
          Task
          <input autoFocus required value={text} onChange={(e) => setText(e.target.value)} placeholder="What needs doing" className={`${INPUT} normal-case`} />
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
          Owner
          <input list="plan-owners" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="Optional" className={`${INPUT} normal-case`} />
          <datalist id="plan-owners">{owners.map((o) => <option key={o} value={o} />)}</datalist>
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
          Phase
          <input list="plan-phases" value={phase} onChange={(e) => setPhase(e.target.value)} placeholder="Optional" className={`${INPUT} normal-case`} />
          <datalist id="plan-phases">{phases.map((p) => <option key={p} value={p} />)}</datalist>
        </label>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
          Estimate (h)
          <input type="number" min={0} step={0.5} value={estimate} onChange={(e) => setEstimate(e.target.value)} className={`${INPUT} w-28 normal-case`} />
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
          Start week
          <input type="number" min={1} step={1} value={start} onChange={(e) => setStart(e.target.value)} className={`${INPUT} w-24 normal-case`} />
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
          Finish week
          <input type="number" min={1} step={1} value={finish} onChange={(e) => setFinish(e.target.value)} className={`${INPUT} w-24 normal-case`} />
        </label>
        <button type="submit" disabled={saving || !text.trim()} className={BTN_PRIMARY}>
          {saving ? "Adding…" : "Add task"}
        </button>
        <button type="button" onClick={onCancel} className={BTN_QUIET}>
          Cancel
        </button>
      </div>
      {error && <p className="text-[13px] text-danger">{error}</p>}
    </form>
  );
}
