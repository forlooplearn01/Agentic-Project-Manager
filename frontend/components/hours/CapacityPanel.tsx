"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import type { AvailabilityReport, AvailabilityWeek } from "@/lib/types";
import { fetchAvailability } from "@/lib/api";
import { BTN_PRIMARY, INPUT, addDays, errorText, formatWeek, h, isoDate } from "./shared";

// Cells at or above this share of the week's capacity read as "full"; above 100% they read as over.
const FULL_SHARE = 0.9;

function cellState(w: AvailabilityWeek): "over" | "full" | "ok" | "unknown" {
  if (w.capacity === null) return "unknown";
  if (w.over) return "over";
  return w.capacity > 0 && w.load / w.capacity >= FULL_SHARE ? "full" : "ok";
}

const CELL_STYLE = {
  over: "bg-danger-bg text-danger font-semibold",
  full: "bg-warning-bg text-warning font-medium",
  ok: "text-text-primary",
  unknown: "text-text-muted",
};

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "danger" | "warning" }) {
  const color = tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : "text-text-primary";
  return (
    <div className="glass-panel flex min-w-[140px] flex-1 flex-col gap-0.5 rounded-xl px-4 py-3">
      <span className="text-[11px] uppercase tracking-wide text-text-faint">{label}</span>
      <span className={`text-[20px] font-semibold tabular-nums ${color}`}>{value}</span>
      {sub && <span className="text-[12px] text-text-muted">{sub}</span>}
    </div>
  );
}

export default function CapacityPanel({ projectId }: { projectId: string }) {
  const today = isoDate(new Date());
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(addDays(today, 27));
  const [skill, setSkill] = useState("");
  const [report, setReport] = useState<AvailabilityReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchAvailability(projectId, from, to, skill)
      .then(setReport)
      .catch((e) => setError(errorText(e)));
  }, [projectId, from, to, skill]);
  useEffect(load, [load]);

  const peopleLink = `/products/task-manager?project=${encodeURIComponent(projectId)}&tab=people`;
  const planLink = `/products/task-manager?project=${encodeURIComponent(projectId)}`;
  const s = report?.summary;
  const needsSetup = report ? report.people.filter((p) => p.weekly_capacity_hours === null) : [];
  const unknown = report ? report.people.flatMap((p) => p.unknown_items.map((u) => ({ ...u, owner: p.name }))) : [];

  return (
    <div className="flex flex-col gap-4">
      <div className="glass-panel flex flex-wrap items-end gap-3 rounded-xl p-4">
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
          From
          <input type="date" value={from} onChange={(e) => e.target.value && setFrom(e.target.value)} className={`${INPUT} normal-case`} />
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
          To
          <input type="date" value={to} onChange={(e) => e.target.value && setTo(e.target.value)} className={`${INPUT} normal-case`} />
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
          Skill or role
          <input value={skill} onChange={(e) => setSkill(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} placeholder="e.g. backend, QA" className={`${INPUT} w-48 normal-case`} />
        </label>
        <button type="button" onClick={load} className={BTN_PRIMARY}>Show who is free</button>
        <p className="ml-auto max-w-[320px] text-[12px] text-text-faint">
          Capacity, skills and who is on the project are edited under <Link href={peopleLink} className="underline hover:text-text-primary">Workspace &gt; People</Link>.
        </p>
      </div>

      {error && <p className="text-[13px] text-danger">{error}</p>}

      {report && s && (
        <>
          <div className="flex flex-wrap gap-3">
            <Stat label="Team capacity" value={`${h(s.capacity_in_window)} h`} sub={`${report.window.working_days} working days, ${s.people - s.no_capacity} of ${s.people} people with capacity set`} />
            <Stat label="Planned work" value={`${h(s.planned_load_in_window)} h`} sub="open tasks, spread to their due dates" />
            <Stat label="Free" value={`${h(s.available_hours)} h`} sub="capacity minus planned work" tone={s.available_hours < 0 ? "danger" : undefined} />
            <Stat label="Over-allocated" value={String(s.over_allocated)} sub={s.over_allocated === 1 ? "person has a week over capacity" : "people have a week over capacity"} tone={s.over_allocated > 0 ? "danger" : undefined} />
            <Stat
              label="Needs setup"
              value={String(s.no_capacity + s.unknown_tasks)}
              sub={`${s.no_capacity} without capacity, ${s.unknown_tasks} tasks not counted`}
              tone={s.no_capacity + s.unknown_tasks > 0 ? "warning" : undefined}
            />
          </div>

          <div className="glass-panel overflow-x-auto rounded-xl">
            <table className="w-full text-left text-[13px]" style={{ minWidth: 420 + report.weeks.length * 96 }}>
              <thead>
                <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
                  <th className="px-3.5 py-2.5 font-medium">Person</th>
                  <th className="px-3.5 py-2.5 text-right font-medium">Per week</th>
                  {report.weeks.map((w) => {
                    const past = report.people[0]?.weeks.find((x) => x.week === w)?.kind === "actual";
                    return (
                      <th key={w} className="px-2 py-2.5 text-right font-medium" title={past ? "Week already over: hours logged" : "Planned hours"}>
                        <span className="block">{formatWeek(w)}</span>
                        <span className="block text-[10px] normal-case tracking-normal text-text-faint">{past ? "logged" : "planned"}</span>
                      </th>
                    );
                  })}
                  <th className="px-3.5 py-2.5 text-right font-medium">Free (h)</th>
                </tr>
              </thead>
              <tbody>
                {report.people.map((p) => (
                  <tr key={p.name} className="border-b border-line last:border-0 align-top hover-tint">
                    <td className="px-3.5 py-2">
                      <span className="text-text-primary">{p.name}</span>
                      {p.role && <span className="ml-1.5 text-text-faint">· {p.role}</span>}
                      {p.skills.length > 0 && <p className="text-[12px] text-text-faint">{p.skills.join(", ")}</p>}
                    </td>
                    <td className="px-3.5 py-2 text-right tabular-nums">
                      {p.weekly_capacity_hours === null ? <Link href={peopleLink} className="text-[12px] italic text-text-faint underline">not set</Link> : h(p.weekly_capacity_hours)}
                    </td>
                    {p.weeks.map((w) => {
                      const state = cellState(w);
                      const tip =
                        w.kind === "actual"
                          ? `${h(w.logged)} h logged${w.capacity !== null ? ` of ${h(w.capacity)} h capacity` : ""}`
                          : `${h(w.load)} h planned${w.capacity !== null ? ` of ${h(w.capacity)} h capacity` : ""}${w.logged > 0 ? `; ${h(w.logged)} h already logged this week (not included)` : ""}`;
                      return (
                        <td key={w.week} title={tip} className={`px-2 py-2 text-right tabular-nums ${CELL_STYLE[state]}`}>
                          {h(w.load)}
                          {w.capacity !== null && <span className="font-normal text-text-faint"> / {h(w.capacity)}</span>}
                          {state === "over" && <span className="sr-only"> (over capacity)</span>}
                        </td>
                      );
                    })}
                    <td className={`px-3.5 py-2 text-right font-medium tabular-nums ${p.available_hours !== null && p.available_hours < 0 ? "text-danger" : "text-text-primary"}`}>
                      {p.available_hours === null ? <span className="font-normal italic text-text-faint">unknown</span> : h(p.available_hours)}
                    </td>
                  </tr>
                ))}
                {report.people.length === 0 && (
                  <tr><td colSpan={report.weeks.length + 3} className="px-3.5 py-6 text-center text-text-faint">{skill ? `Nobody's role or skills match "${skill}".` : "No people yet."}</td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-text-faint">
            <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-danger-bg ring-1 ring-line" />over capacity</span>
            <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-warning-bg ring-1 ring-line" />90% or more of capacity</span>
            <span>Each cell is hours / the week&apos;s capacity. Weeks already over show hours logged; the rest show planned hours.</span>
          </div>

          {(needsSetup.length > 0 || unknown.length > 0) && (
            <div className="glass-panel flex flex-col gap-3 rounded-xl p-4">
              <h3 className="text-[13px] font-semibold text-text-primary">Needs setup before these numbers are complete</h3>
              {needsSetup.length > 0 && (
                <p className="text-[12.5px] text-text-muted">
                  <b>No weekly capacity:</b> {needsSetup.map((p) => p.name).join(", ")}. Their free hours are unknown.{" "}
                  <Link href={peopleLink} className="underline hover:text-text-primary">Set it in People</Link>
                </p>
              )}
              {unknown.length > 0 && (
                <div className="text-[12.5px] text-text-muted">
                  <p>
                    <b>Open tasks that are not counted</b>, because they have no estimate or no due date.{" "}
                    <Link href={planLink} className="underline hover:text-text-primary">Fix them in the Plan</Link>
                  </p>
                  <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-4">
                    {unknown.map((u) => (
                      <li key={u.task_id}>{u.task} <span className="text-text-faint">· {u.owner} · {u.reason}</span></li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          <p className="text-[12px] text-text-faint">
            Free hours = weekly capacity for the working days in the window, minus the remaining hours of each person&apos;s open tasks spread evenly from today to the task&apos;s due date (the end
            of its planned finish week, or an ISO date in its deadline).
          </p>
          {report.notes.map((n) => (
            <p key={n} className="text-[12.5px] text-text-muted">• {n}</p>
          ))}
        </>
      )}
    </div>
  );
}
