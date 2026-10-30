"use client";

import { useState } from "react";
import { removePlanTask } from "@/lib/api";
import type { Task } from "@/lib/types";
import InlineEditShell from "@/components/InlineEditShell";
import { IconX } from "@/components/icons";
import { errorText, h } from "@/components/hours/shared";

// Remove a task from the plan, with a one-line confirmation that floats over the button (so the row never changes size).
export default function RemoveTaskButton({ task, onRemoved }: { task: Task; onRemoved: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logged = task.logged_hours ?? 0;

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await removePlanTask(task.task_id);
      onRemoved();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <InlineEditShell
      editing={asking}
      align="right"
      minWidth="15rem"
      display={
        <button type="button" aria-label={`Remove ${task.task_text} from the plan`} title="Remove from the plan" onClick={() => setAsking(true)} className="hover-tint rounded-md p-1.5 text-text-faint transition hover:text-danger">
          <IconX className="h-3.5 w-3.5" />
        </button>
      }
      editor={
        <span className="flex items-center gap-2 whitespace-nowrap rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 text-[12px] text-text-primary shadow-lg">
          {error ? (
            <span className="text-danger">{error}</span>
          ) : (
            <span>Remove{logged > 0 ? ` (${h(logged)} h logged stay in the total)` : ""}?</span>
          )}
          <button type="button" onClick={confirm} disabled={busy} className="rounded-md bg-danger px-2 py-0.5 font-medium text-white disabled:opacity-50">
            {busy ? "…" : "Yes"}
          </button>
          <button type="button" onClick={() => { setAsking(false); setError(null); }} className="hover-tint rounded-md px-2 py-0.5 text-text-muted">
            No
          </button>
        </span>
      }
    />
  );
}
