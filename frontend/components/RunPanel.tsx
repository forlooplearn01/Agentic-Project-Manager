"use client";

import { useRef, useState } from "react";
import { extractFileText, fetchJobTrace, pollJob, setProjectJiraKey, submitRun } from "@/lib/api";
import type { AgentTrace, RunResult } from "@/lib/types";
import PriorityBadge from "./PriorityBadge";
import TracePanel from "./TracePanel";

type RunState = "idle" | "running" | "completed" | "failed";
type InputMode = "paste" | "upload" | null;

export default function RunPanel({ defaultProjectId, onCompleted }: { defaultProjectId?: string; onCompleted?: () => void }) {
  const [projectId, setProjectId] = useState(defaultProjectId ?? "");
  const [jiraKey, setJiraKey] = useState("");
  const [forceRun, setForceRun] = useState(false);
  const [mode, setMode] = useState<InputMode>(null);
  const [text, setText] = useState("");
  const [uploadedFile, setUploadedFile] = useState<{ name: string; charCount: number } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [state, setState] = useState<RunState>("idle");
  const [result, setResult] = useState<RunResult | null>(null);
  const [agents, setAgents] = useState<AgentTrace[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function selectMode(next: InputMode) {
    setMode(next);
    setText("");
    setUploadedFile(null);
    setUploadError(null);
  }

  async function handleFileSelected(file: File) {
    setUploading(true);
    setUploadError(null);
    setUploadedFile(null);
    setText("");
    try {
      const { text: extracted, filename, char_count } = await extractFileText(file);
      setText(extracted);
      setUploadedFile({ name: filename, charCount: char_count });
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || !projectId.trim()) return;

    setState("running");
    setResult(null);
    setAgents(null);
    setError(null);

    try {
      // Set before the run starts, not after - Sync/Reporting can fire during this
      // very run (e.g. on a brand-new project's first SOW), so the key needs to
      // already be in place, not added afterward once it's too late to matter.
      // Blank stays a no-op - it never clears a key a project already has.
      if (jiraKey.trim()) {
        await setProjectJiraKey(projectId, jiraKey.trim().toUpperCase());
      }

      const { job_id, from_cache } = await submitRun(text, projectId, forceRun);

      let poll = await pollJob(job_id);
      while (poll.status === "running") {
        await new Promise((r) => setTimeout(r, 1500));
        poll = await pollJob(job_id);
      }

      if (poll.status === "failed") {
        setError(poll.error ?? "Run failed");
        setState("failed");
        return;
      }

      setResult(poll.result ?? null);
      setState("completed");
      onCompleted?.();

      if (!from_cache) {
        try {
          const traceResp = await fetchJobTrace(job_id);
          setAgents(traceResp.agents);
        } catch {
          // Trace is a nice-to-have - a failure here shouldn't hide the run result.
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setState("failed");
    }
  }

  return (
    <div className="glass-panel w-full rounded-2xl p-5">
      <h2 className="mb-3 text-sm font-semibold tracking-tight text-text-primary">Submit a document</h2>
      <p className="mb-4 text-[13px] text-text-muted">
        The first document for a project is treated as an SOW (creates the plan); later documents are treated as meeting minutes (update it).
      </p>

      <div className="mb-3 flex gap-1 rounded-lg border border-line bg-surface-solid p-1" style={{ width: "fit-content" }}>
        <button
          type="button"
          onClick={() => selectMode("paste")}
          className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition ${
            mode === "paste" ? "bg-gradient-to-br from-accent-from to-accent-to text-white" : "text-text-muted hover-tint"
          }`}
        >
          Paste text
        </button>
        <button
          type="button"
          onClick={() => selectMode("upload")}
          className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition ${
            mode === "upload" ? "bg-gradient-to-br from-accent-from to-accent-to text-white" : "text-text-muted hover-tint"
          }`}
        >
          Upload file
        </button>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="flex gap-3">
          <input
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            placeholder="Project ID (e.g. acme-website-revamp)"
            className="flex-1 rounded-lg border border-line bg-surface-solid px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-to"
          />
          <input
            value={jiraKey}
            onChange={(e) => setJiraKey(e.target.value)}
            placeholder="Jira project key (optional)"
            title="Set this now so Jira sync works from this project's very first run - a new project has no key until one is set."
            className="w-56 rounded-lg border border-line bg-surface-solid px-3 py-2 font-mono text-sm text-text-primary outline-none focus:border-accent-to"
          />
        </div>

        <label className="flex w-fit cursor-pointer items-center gap-2 text-[12.5px] text-text-muted">
          <input
            type="checkbox"
            checked={forceRun}
            onChange={(e) => setForceRun(e.target.checked)}
            className="accent-accent-to"
          />
          Force a fresh run
          <span className="text-text-faint" title="Skips the cache - use this when resubmitting the exact same document text you've run before, otherwise you'll get an instant replay of the old result with no new tasks actually created.">
            (skip cache)
          </span>
        </label>

        {mode === "paste" && (
          <textarea
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste the SOW or meeting minutes text here…"
            rows={8}
            className="resize-y rounded-lg border border-line bg-surface-solid px-3 py-2 font-mono text-[13px] text-text-primary outline-none focus:border-accent-to"
          />
        )}

        {mode === "upload" && (
          <div className="flex flex-col gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.docx,.pdf"
              className="hidden"
              onChange={(e) => e.target.files?.[0] && handleFileSelected(e.target.files[0])}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="hover-tint flex flex-col items-center gap-1 rounded-lg border border-dashed border-line-strong bg-surface-solid px-4 py-6 text-center transition disabled:opacity-60"
            >
              {uploading ? (
                <span className="inline-flex items-center gap-1.5 text-[13px] text-text-muted">
                  <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-accent-to" />
                  Extracting text…
                </span>
              ) : (
                <>
                  <span className="text-[13px] font-medium text-text-primary">Click to choose a file</span>
                  <span className="text-[11.5px] text-text-faint">.txt, .docx, or .pdf</span>
                </>
              )}
            </button>

            {uploadError && (
              <div className="rounded-lg border border-warning-line bg-warning-bg px-3 py-2 text-[13px] text-warning">
                {uploadError}
              </div>
            )}

            {uploadedFile && (
              <div className="rounded-lg border border-line bg-surface-solid p-3">
                <div className="mb-2 flex items-center justify-between text-[12.5px] text-text-muted">
                  <span className="text-text-primary">{uploadedFile.name}</span>
                  <span>{uploadedFile.charCount.toLocaleString()} characters extracted</span>
                </div>
                <div className="max-h-40 overflow-y-auto rounded-md border border-line bg-background px-2.5 py-2 font-mono text-[12px] text-text-muted">
                  {text}
                </div>
              </div>
            )}
          </div>
        )}

        {mode && (
          <button
            type="submit"
            disabled={state === "running" || !text.trim() || !projectId.trim()}
            className="approve-glow self-start rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-4 py-2 text-sm font-medium text-white transition disabled:opacity-50"
          >
            {state === "running" ? (
              <span className="inline-flex items-center gap-1.5">
                <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-white" />
                Processing…
              </span>
            ) : (
              "Run"
            )}
          </button>
        )}
      </form>

      {error && (
        <div className="mt-4 rounded-lg border border-warning-line bg-warning-bg px-3 py-2 text-[13px] text-warning">
          {error}
        </div>
      )}

      {result && (
        <div className="mt-5 flex flex-col gap-3 border-t border-line pt-4">
          <div className="flex items-center gap-2 text-[13px] text-text-muted">
            <span className="rounded-full bg-success-bg px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-success">
              {result.document_type ?? "processed"}
            </span>
            <span>{result.tasks_count} task{result.tasks_count === 1 ? "" : "s"} in this run's plan</span>
          </div>

          <ul className="flex flex-col gap-1.5">
            {result.tasks.map((t, i) => (
              <li key={i} className="flex items-center gap-2 text-[13px] text-text-primary">
                <PriorityBadge priority={t.priority} />
                <span className="min-w-0 flex-1 truncate">{t.task}</span>
                {t.deadline && <span className="shrink-0 text-text-faint">{t.deadline}</span>}
              </li>
            ))}
          </ul>

          {result.event_signals.length > 0 && (
            <div className="text-[13px]">
              <p className="mb-1 text-text-muted">Updates to existing tasks:</p>
              <ul className="flex flex-col gap-1">
                {result.event_signals.map((e, i) => (
                  <li key={i} className="text-text-primary">
                    <span className="text-text-faint">{String(e.event_type)}</span> - {String(e.task_text)}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {agents && (
            <div className="mt-2">
              <p className="mb-2 text-[13px] text-text-muted">Which agents ran:</p>
              <TracePanel agents={agents} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
