"use client";

import { useState } from "react";
import { deleteProject } from "@/lib/api";
import { IconTrash, IconX } from "./icons";

export default function DeleteProjectModal({
  projectId,
  taskCount,
  onClose,
  onDeleted,
}: {
  projectId: string;
  taskCount: number;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const matches = confirmText.trim() === projectId;

  async function handleDelete() {
    if (!matches) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteProject(projectId);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setDeleting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="glass-panel w-full max-w-md rounded-2xl p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-[15px] font-semibold text-danger">
            <IconTrash className="h-4.5 w-4.5" />
            Delete Project
          </h2>
          <button type="button" onClick={onClose} className="hover-tint rounded-lg p-1.5 text-text-faint transition">
            <IconX className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-4 rounded-lg border border-danger/40 bg-danger-bg px-3.5 py-3 text-[13px] text-text-primary">
          This permanently deletes <strong>{projectId}</strong> - all {taskCount} task{taskCount === 1 ? "" : "s"},
          dependencies, and its Jira mapping. This cannot be undone.
        </div>

        <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-text-faint">
          Type <span className="font-mono text-text-primary">{projectId}</span> to confirm
        </label>
        <input
          autoFocus
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && matches && handleDelete()}
          placeholder={projectId}
          className="w-full rounded-lg border border-line bg-surface-solid px-3 py-2 font-mono text-[13px] text-text-primary outline-none focus:border-danger"
        />

        {error && <p className="mt-3 text-[12.5px] text-danger">{error}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="hover-tint rounded-lg px-3.5 py-2 text-[13px] text-text-muted transition">
            Cancel
          </button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={!matches || deleting}
            className="rounded-lg bg-danger px-4 py-2 text-[13px] font-medium text-white transition disabled:opacity-40"
          >
            {deleting ? "Deleting…" : "Delete Project"}
          </button>
        </div>
      </div>
    </div>
  );
}
