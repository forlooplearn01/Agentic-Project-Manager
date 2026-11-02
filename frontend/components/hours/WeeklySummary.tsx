"use client";

import { useState } from "react";
import { fetchWeeklySummary } from "@/lib/api";
import { BTN_QUIET, errorText } from "./shared";

// A plain-text status the PMO can paste into an email or a chat. Built from the numbers on this page; nothing is sent from here.
export default function WeeklySummary({ projectId }: { projectId: string }) {
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function show() {
    setBusy(true);
    setError(null);
    try {
      setText((await fetchWeeklySummary(projectId)).text);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Could not copy. Select the text and copy it.");
    }
  }

  return (
    <section className="glass-panel flex flex-col gap-3 rounded-xl p-4" aria-label="Weekly summary">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-[13px] font-semibold text-text-primary">Weekly summary</h3>
          <p className="text-[12.5px] text-text-muted">A short status written from these numbers, ready to paste into an email or a chat. Nothing is sent from here.</p>
        </div>
        <div className="flex items-center gap-2">
          {text && <button type="button" onClick={copy} className={BTN_QUIET}>{copied ? "Copied" : "Copy"}</button>}
          <button type="button" onClick={show} disabled={busy} className={BTN_QUIET}>{busy ? "Writing…" : text ? "Refresh" : "Write summary"}</button>
        </div>
      </div>
      {error && <p className="text-[13px] text-danger">{error}</p>}
      {text && <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-surface p-3 text-[12.5px] leading-relaxed text-text-primary">{text}</pre>}
    </section>
  );
}
