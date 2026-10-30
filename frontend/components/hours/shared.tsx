"use client";

import { useState } from "react";
import type { HoursStatus } from "@/lib/types";
import { formatDate } from "@/lib/format";
import InlineEditShell, { focusWithoutScrolling } from "../InlineEditShell";

export const INPUT = "rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to";
export const BTN_PRIMARY =
  "rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-3 py-1.5 text-[12.5px] font-medium text-white transition disabled:opacity-50";
export const BTN_QUIET =
  "hover-tint rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary disabled:opacity-50";

// One decimal at most, no trailing ".0"; null/undefined shows a dash.
export function h(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  return `${Math.round(n * 10) / 10}`;
}

export function errorText(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e);
  // The API's own explanation sits after "status: " as JSON {"detail": "..."}; show just that.
  const m = raw.match(/"detail":"([^"]+)"/);
  return m ? m[1] : raw;
}

export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// The first day of the week containing d. startDay is the project's week start: 0 = Monday ... 5 = Saturday, 6 = Sunday (the backend's numbering).
export function weekStartOf(d: Date, startDay: number = 0): Date {
  const copy = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const mondayBased = (copy.getDay() + 6) % 7;
  copy.setDate(copy.getDate() - ((mondayBased - startDay + 7) % 7));
  return copy;
}

export const WEEK_START_OPTIONS = [
  { value: 0, label: "Monday" },
  { value: 6, label: "Sunday" },
  { value: 5, label: "Saturday" },
];

export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  date.setDate(date.getDate() + days);
  return isoDate(date);
}

export function formatWeek(iso: string): string {
  return formatDate(iso);
}

const STATUS_STYLE: Record<HoursStatus, string> = {
  on_track: "border-line bg-success-bg text-success",
  watch: "border-warning-line bg-warning-bg text-warning",
  at_risk: "border-line bg-danger-bg text-danger",
  unknown: "border-line bg-surface text-text-muted",
};
const STATUS_LABEL: Record<HoursStatus, string> = {
  on_track: "On track",
  watch: "Watch",
  at_risk: "At risk",
  unknown: "Not enough data",
};

export function StatusPill({ status }: { status: HoursStatus }) {
  return <span className={`inline-block rounded-full border px-2.5 py-0.5 text-[11.5px] font-semibold ${STATUS_STYLE[status]}`}>{STATUS_LABEL[status]}</span>;
}

// A number you click to edit. Empty input clears the value (saves null).
export function NumberCell({
  value,
  onSave,
  placeholder = "set",
  title,
  step = 0.5,
  integer = false,
}: {
  value: number | null | undefined;
  onSave: (v: number | null) => Promise<void>;
  placeholder?: string;
  title?: string;
  step?: number;
  integer?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function commit() {
    const text = draft.trim();
    const next = text === "" ? null : Number(text);
    if (next !== null && (!Number.isFinite(next) || next < 0)) {
      setError("Enter a number, 0 or more");
      return;
    }
    if (next === (value ?? null)) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave(next);
      setEditing(false);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  const editor = (
    <span className="relative block">
      <input
        ref={focusWithoutScrolling}
        type="number"
        min={0}
        step={integer ? 1 : step}
        value={draft}
        disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setEditing(false);
        }}
        className={`${INPUT} w-full shadow-lg`}
      />
      {error && <span className="absolute right-0 top-full z-40 mt-0.5 whitespace-nowrap rounded bg-surface-solid px-1 text-[11px] text-danger shadow-lg">{error}</span>}
    </span>
  );
  const display = (
    <button
      type="button"
      title={title ?? "Click to edit"}
      onClick={() => {
        setDraft(value === null || value === undefined ? "" : String(value));
        setEditing(true);
      }}
      className={`hover-tint rounded-md px-1.5 py-0.5 text-left tabular-nums transition ${
        value === null || value === undefined ? "text-text-faint italic" : "text-text-primary"
      }`}
    >
      {value === null || value === undefined ? placeholder : h(value)}
    </button>
  );
  return <InlineEditShell editing={editing} display={display} editor={editor} minWidth="5.5rem" align="right" />;
}

// Comma-separated skill tags for one person; click to edit.
export function SkillsCell({ name, skills, onSave }: { name: string; skills: string[]; onSave: (skills: string[]) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function commit() {
    try {
      await onSave(draft.split(",").map((t) => t.trim()).filter(Boolean));
      setEditing(false);
    } catch (e) {
      setError(errorText(e));
    }
  }

  const editor = (
    <span className="relative block">
      <input
        ref={focusWithoutScrolling} value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit} placeholder="python, aws, qa"
        onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }}
        className={`${INPUT} w-full shadow-lg`} aria-label={`Skills for ${name}, separated by commas`}
      />
      {error && <span className="absolute left-0 top-full z-40 mt-0.5 whitespace-nowrap rounded bg-surface-solid px-1 text-[11px] text-danger shadow-lg">{error}</span>}
    </span>
  );
  const display = (
    <button type="button" title="Click to edit skills" onClick={() => { setDraft(skills.join(", ")); setEditing(true); }} className="hover-tint rounded-md px-1.5 py-0.5 text-left">
      {skills.length > 0 ? skills.join(", ") : <span className="italic text-text-faint">add skills</span>}
    </button>
  );
  return <InlineEditShell editing={editing} display={display} editor={editor} minWidth="13rem" />;
}
