"use client";

import { useEffect, useState } from "react";
import { fetchProjectIntegrations, setProjectJiraKey } from "@/lib/api";

export default function ProjectSettings({ projectId }: { projectId: string }) {
  const [jiraKey, setJiraKey] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setJiraKey(null);
    fetchProjectIntegrations(projectId)
      .then((r) => setJiraKey(r.jira_project_key))
      .catch(() => setJiraKey(null));
  }, [projectId]);

  async function save() {
    setSaving(true);
    try {
      const r = await setProjectJiraKey(projectId, draft.trim().toUpperCase());
      setJiraKey(r.jira_project_key);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => {
          setDraft(jiraKey ?? "");
          setEditing(true);
        }}
        className="hover-tint inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] text-text-muted transition hover:text-text-primary"
        title="Which Jira project this project's tasks sync into"
      >
        <span className="text-text-faint">Jira project:</span>
        {jiraKey ? (
          <span className="font-mono text-text-primary">{jiraKey}</span>
        ) : (
          <span className="text-text-faint">not set</span>
        )}
      </button>
    );
  }

  return (
    <div className="inline-flex items-center gap-1.5">
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && save()}
        placeholder="e.g. PROJ"
        className="w-24 rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 font-mono text-[12.5px] text-text-primary outline-none focus:border-accent-to"
      />
      <button
        type="button"
        onClick={save}
        disabled={saving}
        className="rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-2.5 py-1.5 text-[12.5px] font-medium text-white disabled:opacity-50"
      >
        Save
      </button>
      <button
        type="button"
        onClick={() => setEditing(false)}
        className="hover-tint rounded-lg px-2 py-1.5 text-[12.5px] text-text-faint"
      >
        Cancel
      </button>
    </div>
  );
}
