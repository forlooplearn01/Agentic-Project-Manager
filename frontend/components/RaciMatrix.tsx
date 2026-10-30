"use client";

import { useState, type Dispatch, type SetStateAction } from "react";
import InlineEditShell from "./InlineEditShell";
import Link from "next/link";
import { fetchRaci, setRaci } from "@/lib/api";
import type { RaciMatrix, RaciRole, RaciRow, RaciSubject, RaciWarning } from "@/lib/types";
import { IconAlertTriangle } from "./icons";

const ROLES: { id: RaciRole; name: string }[] = [
  { id: "R", name: "Responsible" },
  { id: "A", name: "Accountable" },
  { id: "C", name: "Consulted" },
  { id: "I", name: "Informed" },
];
const WARNING_TEXT: Record<RaciWarning, string> = {
  multiple_accountable: "More than one Accountable",
  no_responsible: "Nobody Responsible",
  no_accountable: "No Accountable yet",
};

function assignmentFor(row: RaciRow, person: string) {
  return row.assignments.find((a) => a.person === person) ?? null;
}

function Cell({
  row,
  person,
  editing,
  onEdit,
  onToggle,
}: {
  row: RaciRow;
  person: string;
  editing: boolean;
  onEdit: (open: boolean) => void;
  onToggle: (role: RaciRole) => void;
}) {
  const current = assignmentFor(row, person);
  const roles = current?.roles ?? [];
  const editor = (
    <div className="flex items-center gap-1 rounded-lg border border-line bg-surface-solid p-1 shadow-lg">
      {ROLES.map((r) => {
        const on = roles.includes(r.id) && !current?.implied;
        return (
          <button
            key={r.id}
            type="button"
            title={r.name}
            aria-pressed={on}
            onClick={() => onToggle(r.id)}
            className={`h-6 w-6 rounded border text-[11.5px] font-semibold transition ${
              on ? "border-transparent bg-gradient-to-br from-accent-from to-accent-to text-white" : "border-line bg-surface-solid text-text-muted hover:border-accent-to"
            }`}
          >
            {r.id}
          </button>
        );
      })}
      <button type="button" onClick={() => onEdit(false)} className="ml-0.5 rounded px-1 text-[11px] text-text-faint hover:text-text-primary" title="Done">
        Done
      </button>
    </div>
  );
  const display = (
    <button
      type="button"
      onClick={() => onEdit(true)}
      title={current?.implied ? "Implied from the owner - click to assign explicitly" : "Click to edit"}
      className="hover-tint -mx-1 min-w-[2.5rem] rounded px-1.5 py-0.5 text-center"
    >
      {roles.length === 0 ? (
        <span className="text-text-faint">-</span>
      ) : (
        <span className={`font-semibold ${current?.implied ? "italic text-text-faint" : "text-text-primary"}`}>{roles.join("/")}</span>
      )}
    </button>
  );
  return <InlineEditShell editing={editing} display={display} editor={editor} minWidth="10.5rem" align="center" />;
}

export default function RaciMatrixView({
  projectId,
  subject,
  matrix,
  onMatrixChange,
  onOpenDeliverables,
}: {
  projectId: string;
  subject: RaciSubject;
  matrix: RaciMatrix;
  onMatrixChange: Dispatch<SetStateAction<RaciMatrix | null>>;
  onOpenDeliverables: () => void;
}) {
  const [query, setQuery] = useState("");
  const [onlyIncomplete, setOnlyIncomplete] = useState(false);
  const [editing, setEditing] = useState<{ id: string; person: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const noun = subject === "deliverable" ? "deliverable" : "task";

  async function toggle(row: RaciRow, person: string, role: RaciRole) {
    setError(null);
    const existing = assignmentFor(row, person);
    // An implied R is not a saved assignment: toggling starts from nothing, so the first click saves that role.
    const base = existing && !existing.implied ? existing.roles : [];
    const next = base.includes(role) ? base.filter((r) => r !== role) : [...base, role];
    try {
      await setRaci(subject, row.subject_id, person, next);
      // Warnings and implied roles depend on the whole row, so take the server's view of the matrix.
      onMatrixChange(await fetchRaci(projectId, subject));
    } catch {
      setError("Could not save that change - try again.");
    }
  }

  const assignedRows = matrix.rows.filter((r) => r.assignments.some((a) => !a.implied)).length;
  const incompleteRows = matrix.rows.filter((r) => r.warnings.length > 0).length;
  const rows = matrix.rows.filter(
    (r) => (!query || r.title.toLowerCase().includes(query.toLowerCase())) && (!onlyIncomplete || r.warnings.length > 0)
  );

  if (matrix.rows.length === 0) {
    return subject === "deliverable" ? (
      <p className="text-[13px] text-text-faint">
        No deliverables yet - RACI roles are assigned per deliverable.{" "}
        <button type="button" onClick={onOpenDeliverables} className="underline">
          Add them in the Deliverables tab
        </button>
        , or switch to Tasks.
      </p>
    ) : (
      <p className="text-[13px] text-text-faint">No tasks in this project yet - RACI roles are assigned per task.</p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${noun}s...`}
            className="min-w-[180px] rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[13px] text-text-primary outline-none focus:border-accent-to"
          />
          <label className="inline-flex items-center gap-1.5 text-[12.5px] text-text-muted">
            <input type="checkbox" checked={onlyIncomplete} onChange={(e) => setOnlyIncomplete(e.target.checked)} />
            Only rows needing attention ({incompleteRows})
          </label>
        </div>
        <p className="text-[12px] text-text-faint">
          {assignedRows} of {matrix.rows.length} {noun}s have roles assigned
        </p>
      </div>

      <p className="text-[12px] text-text-faint">
        <span className="font-semibold text-text-muted">R</span> Responsible - does the work &nbsp;·&nbsp;
        <span className="font-semibold text-text-muted">A</span> Accountable - signs off &nbsp;·&nbsp;
        <span className="font-semibold text-text-muted">C</span> Consulted &nbsp;·&nbsp;
        <span className="font-semibold text-text-muted">I</span> Informed. <span className="italic">Lighter italic R</span> = inferred from the owner until you assign it.
        Someone missing?{" "}
        <Link href="/products/task-manager?tab=people" className="underline">
          Add them in Workspace → People
        </Link>
        .
      </p>

      {error && <p className="text-[12.5px] text-danger">{error}</p>}

      <div className="glass-panel max-h-[70vh] overflow-auto rounded-xl">
        <table className="w-full text-left text-[13px]">
          <thead>
            <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
              <th className="sticky left-0 top-0 z-20 min-w-[260px] bg-surface-solid px-3.5 py-2.5 font-medium">
                {subject === "deliverable" ? "Deliverable" : "Task"}
              </th>
              {matrix.people.map((p) => (
                <th key={p} className="sticky top-0 z-10 whitespace-nowrap bg-surface-solid px-3 py-2.5 text-center font-medium">
                  {p}
                </th>
              ))}
              <th className="sticky top-0 z-10 bg-surface-solid px-3 py-2.5 font-medium">Check</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.subject_id} className="border-b border-line last:border-0">
                <td className="sticky left-0 z-10 max-w-[320px] bg-surface-solid px-3.5 py-2">
                  <div className="truncate text-text-primary" title={r.title}>
                    {r.number && <span className="mr-1.5 font-mono text-[11.5px] text-text-faint">{r.number}</span>}
                    {r.title}
                  </div>
                  {r.owner && <div className="truncate text-[11.5px] text-text-faint">Owner: {r.owner}</div>}
                </td>
                {matrix.people.map((p) => (
                  <td key={p} className="px-3 py-2 text-center">
                    <Cell
                      row={r}
                      person={p}
                      editing={editing?.id === r.subject_id && editing.person === p}
                      onEdit={(open) => setEditing(open ? { id: r.subject_id, person: p } : null)}
                      onToggle={(role) => toggle(r, p, role)}
                    />
                  </td>
                ))}
                <td className="px-3 py-2">
                  {r.warnings.length === 0 ? (
                    <span className="text-text-faint">-</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-[12px] text-warning" title={r.warnings.map((w) => WARNING_TEXT[w]).join("; ")}>
                      <IconAlertTriangle className="h-3.5 w-3.5 shrink-0" />
                      {r.warnings.map((w) => WARNING_TEXT[w]).join(", ")}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
