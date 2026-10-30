"use client";

import { useCallback, useEffect, useState } from "react";
import type { AvailabilityReport, ProjectPerson } from "@/lib/types";
import { fetchAvailability, setPersonCapacity } from "@/lib/api";
import { BTN_PRIMARY, INPUT, NumberCell, SkillsCell, addDays, errorText, h, isoDate } from "./shared";

export default function CapacityPanel({ projectId, people, onPeopleChanged }: { projectId: string; people: ProjectPerson[]; onPeopleChanged: (p: ProjectPerson[]) => void }) {
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
  useEffect(load, [load, people]);

  const byName = new Map(people.map((p) => [p.name, p]));

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
      </div>

      {error && <p className="text-[13px] text-danger">{error}</p>}

      {report && (
        <>
          <div className="glass-panel overflow-x-auto rounded-xl">
            <table className="w-full min-w-[900px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
                  <th className="px-3.5 py-2.5 font-medium">Person</th>
                  <th className="px-3.5 py-2.5 font-medium">Skills</th>
                  <th className="px-3.5 py-2.5 text-right font-medium">Capacity per week (h)</th>
                  <th className="px-3.5 py-2.5 text-right font-medium">In this window (h)</th>
                  <th className="px-3.5 py-2.5 text-right font-medium">Planned work (h)</th>
                  <th className="px-3.5 py-2.5 text-right font-medium">Free (h)</th>
                  <th className="px-3.5 py-2.5 font-medium">Unknown</th>
                </tr>
              </thead>
              <tbody>
                {report.people.map((p) => {
                  const person = byName.get(p.name);
                  return (
                    <tr key={p.name} className="border-b border-line last:border-0 align-top hover-tint">
                      <td className="px-3.5 py-2 text-text-primary">{p.name}{p.role && <span className="ml-1.5 text-text-faint">· {p.role}</span>}</td>
                      <td className="px-3.5 py-2 text-text-muted">
                        {person ? (
                          <SkillsCell name={person.name} skills={person.skills ?? []} onSave={async (skills) => { const r = await setPersonCapacity(projectId, p.name, { skills }); onPeopleChanged(r.people); }} />
                        ) : (p.skills.join(", ") || "–")}
                      </td>
                      <td className="px-3.5 py-2 text-right">
                        <NumberCell value={p.weekly_capacity_hours} placeholder="set" title="Hours per week this person can give the project" onSave={async (v) => { const r = await setPersonCapacity(projectId, p.name, { weekly_capacity_hours: v }); onPeopleChanged(r.people); }} />
                      </td>
                      <td className="px-3.5 py-2 text-right tabular-nums">{h(p.capacity_in_window)}</td>
                      <td className="px-3.5 py-2 text-right tabular-nums">{h(p.planned_load_in_window)}</td>
                      <td className={`px-3.5 py-2 text-right font-medium tabular-nums ${p.available_hours !== null && p.available_hours < 0 ? "text-danger" : "text-text-primary"}`}>
                        {p.available_hours === null ? <span className="font-normal italic text-text-faint">unknown</span> : h(p.available_hours)}
                      </td>
                      <td className="px-3.5 py-2 text-[12px] text-text-muted">
                        {p.unknown_items.length === 0 ? "–" : p.unknown_items.map((u) => `${u.task} (${u.reason})`).join("; ")}
                      </td>
                    </tr>
                  );
                })}
                {report.people.length === 0 && (
                  <tr><td colSpan={7} className="px-3.5 py-6 text-center text-text-faint">{skill ? `Nobody's role or skills match "${skill}".` : "No people yet."}</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="text-[12px] text-text-faint">
            {report.window.working_days} working days. Free hours = weekly capacity for those days, minus the remaining hours of each person&apos;s open tasks spread evenly from today to the
            task&apos;s due date (the end of its planned finish week, or an ISO date in its deadline).
          </p>
          {report.notes.map((n) => (
            <p key={n} className="text-[12.5px] text-text-muted">• {n}</p>
          ))}
        </>
      )}
    </div>
  );
}
