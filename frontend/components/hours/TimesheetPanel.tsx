"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ProjectPerson, TimesheetInbox, TimesheetWeek } from "@/lib/types";
import { fetchTimesheet, saveTimesheet, setTaskHours } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import IncomingTimesheets from "./IncomingTimesheets";
import { BTN_PRIMARY, BTN_QUIET, INPUT, addDays, errorText, formatWeek, h, isoDate, weekStartOf } from "./shared";

export default function TimesheetPanel({
  projectId,
  people,
  weekStartDay = 0,
  inbox,
  reloadInbox,
  reloadKey,
  onChanged,
}: {
  projectId: string;
  people: ProjectPerson[];
  weekStartDay?: number;
  inbox: TimesheetInbox | null;
  reloadInbox: () => void;
  reloadKey: number;
  onChanged: () => void;
}) {
  const [person, setPerson] = useState<string>("");
  const [week, setWeek] = useState<string>(() => isoDate(weekStartOf(new Date(), weekStartDay)));
  const [sheet, setSheet] = useState<TimesheetWeek | null>(null);
  const [hours, setHours] = useState<Record<string, string>>({});
  const [other, setOther] = useState("");
  const [needed, setNeeded] = useState<Record<string, string>>({});
  const [showAll, setShowAll] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!person && people.length > 0) setPerson(people[0].name);
  }, [people, person]);

  const load = useCallback(() => {
    if (!person) return;
    setSheet(null);
    setError(null);
    fetchTimesheet(projectId, person, week)
      .then((s) => {
        setSheet(s);
        setHours(Object.fromEntries(s.tasks.filter((t) => t.hours_this_week > 0).map((t) => [t.task_id, String(t.hours_this_week)])));
        setOther(s.other_hours > 0 ? String(s.other_hours) : "");
        setNeeded({});
      })
      .catch((e) => setError(errorText(e)));
  }, [projectId, person, week, reloadKey]);
  useEffect(load, [load]);

  const num = (v: string | undefined) => (v && Number(v) > 0 ? Number(v) : 0);

  const visible = useMemo(() => {
    if (!sheet) return [];
    const mine = person.toLowerCase();
    return sheet.tasks
      .filter((t) => showAll || t.status !== "completed" || t.hours_this_week > 0 || num(hours[t.task_id]) > 0)
      .sort((a, b) => Number((b.owner ?? "").toLowerCase() === mine) - Number((a.owner ?? "").toLowerCase() === mine));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sheet, showAll, person, hours]);

  const total = visible.reduce((a, t) => a + num(hours[t.task_id]), 0) + num(other);

  async function save() {
    if (!sheet) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const bad = Object.values(hours).concat(other).find((v) => v !== "" && !(Number(v) >= 0));
      if (bad !== undefined) throw new Error("Hours must be numbers, 0 or more.");
      const entries = sheet.tasks.filter((t) => num(hours[t.task_id]) > 0).map((t) => ({ task_id: t.task_id as string | null, hours: num(hours[t.task_id]) }));
      if (num(other) > 0) entries.push({ task_id: null, hours: num(other) });
      await saveTimesheet(projectId, person, week, entries);
      for (const [taskId, v] of Object.entries(needed)) {
        if (v !== "" && Number(v) >= 0) await setTaskHours(taskId, { hours_still_needed: Number(v) });
      }
      setMessage("Saved.");
      load();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <IncomingTimesheets projectId={projectId} inbox={inbox} reload={reloadInbox} onChanged={() => { load(); onChanged(); }} />

      <section className="glass-panel flex flex-col gap-3 rounded-xl p-4" aria-label="Enter hours by hand">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 className="text-[13px] font-semibold text-text-primary">Enter hours by hand</h3>
            <p className="text-[12px] text-text-faint">Pick a person and a week. Saving replaces everything that person logged for that week.</p>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
              Person
              <select value={person} onChange={(e) => setPerson(e.target.value)} className={`${INPUT} min-w-[160px] normal-case`}>
                {people.length === 0 && <option value="">No people yet</option>}
                {people.map((p) => (
                  <option key={p.name} value={p.name}>{p.name}</option>
                ))}
              </select>
            </label>
            <div className="flex items-center gap-1">
              <button type="button" aria-label="Previous week" onClick={() => setWeek(addDays(week, -7))} className={BTN_QUIET}>‹</button>
              <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-text-faint">
                Week starting
                <input type="date" value={week} onChange={(e) => e.target.value && setWeek(isoDate(weekStartOf(new Date(`${e.target.value}T00:00`), weekStartDay)))} className={`${INPUT} normal-case`} />
              </label>
              <button type="button" aria-label="Next week" onClick={() => setWeek(addDays(week, 7))} className={BTN_QUIET}>›</button>
            </div>
          </div>
        </div>

        {people.length === 0 && <p className="text-[13px] text-text-faint">Add people in Workspace, People first so hours can be logged against them.</p>}
        {error && <p className="text-[13px] text-danger">{error}</p>}
        {!sheet && person && !error && <p className="text-[13px] text-text-faint">Loading…</p>}

        {sheet && (
          <>
            <p className="text-[12.5px] text-text-muted">Week of {formatWeek(sheet.week_start)}</p>
            <div className="overflow-x-auto rounded-lg border border-line">
              <table className="w-full min-w-[720px] text-left text-[13px]">
                <thead>
                  <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-faint">
                    <th className="px-3.5 py-2.5 font-medium">Task</th>
                    <th className="px-3.5 py-2.5 font-medium">Status</th>
                    <th className="px-3.5 py-2.5 text-right font-medium">Estimate</th>
                    <th className="px-3.5 py-2.5 text-right font-medium">Logged so far</th>
                    <th className="px-3.5 py-2.5 text-right font-medium">This week</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((t) => {
                    const loggedBefore = t.logged_hours - t.hours_this_week;
                    const after = loggedBefore + num(hours[t.task_id]);
                    const prompt = t.status !== "completed" && t.estimate_hours !== null && after >= t.estimate_hours && (t.hours_still_needed === null || needed[t.task_id] !== undefined);
                    return (
                      <FragmentRow key={t.task_id}>
                        <tr className="border-b border-line last:border-0">
                          <td className="max-w-[340px] px-3.5 py-2 text-text-primary"><div className="truncate" title={t.task_text}>{t.task_text}</div></td>
                          <td className="px-3.5 py-2"><StatusBadge status={t.status} /></td>
                          <td className="px-3.5 py-2 text-right tabular-nums">{h(t.estimate_hours)}</td>
                          <td className="px-3.5 py-2 text-right tabular-nums">{h(loggedBefore)}</td>
                          <td className="px-3.5 py-2 text-right">
                            <input
                              type="number" min={0} step={0.5} aria-label={`Hours this week on ${t.task_text}`}
                              value={hours[t.task_id] ?? ""} onChange={(e) => setHours({ ...hours, [t.task_id]: e.target.value })}
                              className={`${INPUT} w-20 text-right`}
                            />
                          </td>
                        </tr>
                        {prompt && (
                          <tr className="border-b border-line">
                            <td colSpan={5} className="bg-warning-bg px-3.5 py-2 text-[12.5px] text-warning">
                              <span className="inline-flex flex-wrap items-center gap-2">
                                This task has used its {h(t.estimate_hours)} h estimate ({h(after)} h logged) and is still open. How many more hours does it need?
                                <input
                                  type="number" min={0} step={0.5} aria-label={`Hours still needed on ${t.task_text}`}
                                  value={needed[t.task_id] ?? (t.hours_still_needed ?? "")}
                                  onChange={(e) => setNeeded({ ...needed, [t.task_id]: e.target.value })}
                                  className={`${INPUT} w-20 text-text-primary`}
                                />
                                <span className="text-text-muted">Leave empty if unknown; the forecast will then be low.</span>
                              </span>
                            </td>
                          </tr>
                        )}
                      </FragmentRow>
                    );
                  })}
                  <tr>
                    <td className="px-3.5 py-2 text-text-muted" colSpan={4}>Other: meetings, admin, time on no listed task</td>
                    <td className="px-3.5 py-2 text-right">
                      <input type="number" min={0} step={0.5} aria-label="Hours this week on other work" value={other} onChange={(e) => setOther(e.target.value)} className={`${INPUT} w-20 text-right`} />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <label className="flex items-center gap-2 text-[12.5px] text-text-muted">
              <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
              Also show completed tasks
            </label>
            <div className="flex items-center justify-between gap-3">
              <span className="text-[13px] text-text-muted">Week total: <b className="tabular-nums text-text-primary">{h(total)} h</b></span>
              <div className="flex items-center gap-3">
                {message && <span className="text-[12.5px] text-success">{message}</span>}
                <button type="button" onClick={save} disabled={saving} className={BTN_PRIMARY}>{saving ? "Saving…" : "Save week"}</button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

// tbody children must be rows; a keyed fragment lets a task row and its prompt row travel together.
function FragmentRow({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
