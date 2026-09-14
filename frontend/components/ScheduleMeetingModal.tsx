"use client";

import { useEffect, useMemo, useState } from "react";
import { approveMeeting, fetchProjectPeople, rejectMeeting, scheduleMeeting } from "@/lib/api";
import type { Meeting, ProjectPerson, Task } from "@/lib/types";
import { IconAlertTriangle, IconCalendar, IconX } from "./icons";

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function toLocalInputValue(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Sensible default slot - tomorrow at 10:00 for 30 minutes - rather than leaving
// the fields blank the way Google Calendar itself doesn't either.
function defaultStart() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(10, 0, 0, 0);
  return toLocalInputValue(d);
}
function defaultEnd() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(10, 30, 0, 0);
  return toLocalInputValue(d);
}

export default function ScheduleMeetingModal({
  projectId,
  tasks,
  pendingMeeting,
  onClose,
  onDecided,
}: {
  projectId: string;
  tasks: Task[];
  // When set, the modal reviews an already-proposed meeting (from the Meeting
  // Coordinator agent) instead of building a new one from scratch - same form,
  // pre-filled, with Approve/Reject in place of a plain Schedule action.
  pendingMeeting?: Meeting;
  onClose: () => void;
  onDecided?: () => void;
}) {
  const isReview = Boolean(pendingMeeting);
  const [people, setPeople] = useState<ProjectPerson[] | null>(null);
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<string>>(new Set());
  const [taskFilter, setTaskFilter] = useState("");
  const [selectedEmails, setSelectedEmails] = useState<Set<string>>(
    () => new Set(pendingMeeting?.attendee_emails ?? [])
  );
  const [extraEmail, setExtraEmail] = useState("");
  const [extraEmails, setExtraEmails] = useState<string[]>([]);
  const [title, setTitle] = useState(pendingMeeting?.title ?? "");
  const [titleTouched, setTitleTouched] = useState(isReview);
  const [description, setDescription] = useState(pendingMeeting?.description ?? "");
  const [descriptionTouched, setDescriptionTouched] = useState(isReview);
  const [start, setStart] = useState(() => (pendingMeeting ? toLocalInputValue(new Date(pendingMeeting.start_iso)) : defaultStart()));
  const [end, setEnd] = useState(() => (pendingMeeting ? toLocalInputValue(new Date(pendingMeeting.end_iso)) : defaultEnd()));
  const [submitting, setSubmitting] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ meeting_link: string | null } | null>(null);
  const [rejected, setRejected] = useState(false);

  useEffect(() => {
    fetchProjectPeople(projectId)
      .then((r) => setPeople(r.people))
      .catch(() => setPeople([]));
  }, [projectId]);

  // A proposed meeting's attendee emails render as highlighted chips only for people
  // this project already knows about - anyone else (an owner not in project_people,
  // or a client contact) would otherwise be silently invisible: still submitted, but
  // with no chip and no extra-email pill to show a reviewer they're included at all.
  // Move those into the free-typed extra-email list once people has loaded.
  useEffect(() => {
    if (!isReview || !people || !pendingMeeting) return;
    const knownEmails = new Set(people.map((p) => p.email));
    const unmatched = pendingMeeting.attendee_emails.filter((email) => !knownEmails.has(email));
    if (unmatched.length === 0) return;
    setExtraEmails((prev) => [...new Set([...prev, ...unmatched])]);
    setSelectedEmails((prev) => {
      const next = new Set(prev);
      for (const email of unmatched) next.delete(email);
      return next;
    });
  }, [isReview, people, pendingMeeting]);

  const selectedTasks = useMemo(() => tasks.filter((t) => selectedTaskIds.has(t.task_id)), [tasks, selectedTaskIds]);

  // Auto-fill title/description from selected tasks, but stop once the user has
  // edited either by hand - never clobber a deliberate edit with a re-derivation.
  useEffect(() => {
    if (titleTouched) return;
    if (selectedTasks.length === 0) {
      setTitle("");
    } else if (selectedTasks.length === 1) {
      setTitle(`Discuss: ${selectedTasks[0].task_text}`);
    } else {
      setTitle(`Review ${selectedTasks.length} tasks: ${selectedTasks[0].task_text}, +${selectedTasks.length - 1} more`);
    }
  }, [selectedTasks, titleTouched]);

  useEffect(() => {
    if (descriptionTouched) return;
    if (selectedTasks.length === 0) {
      setDescription("");
      return;
    }
    const lines = selectedTasks.map(
      (t) => `- ${t.task_text}${t.owner ? ` (${t.owner})` : ""}${t.blocker ? ` - blocked: ${t.blocker}` : ""}`
    );
    setDescription(`Tasks for this meeting:\n${lines.join("\n")}`);
  }, [selectedTasks, descriptionTouched]);

  // Convenience: pre-select attendees who own a selected task, once people are loaded.
  useEffect(() => {
    if (!people || people.length === 0) return;
    const owners = new Set(selectedTasks.flatMap((t) => (t.owner ? t.owner.split(",").map((o) => o.trim()) : [])));
    if (owners.size === 0) return;
    setSelectedEmails((prev) => {
      const next = new Set(prev);
      for (const person of people) {
        if (owners.has(person.name)) next.add(person.email);
      }
      return next;
    });
  }, [selectedTasks, people]);

  function toggleTask(taskId: string) {
    setSelectedTaskIds((prev) => {
      const next = new Set(prev);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  }

  function toggleAttendee(email: string) {
    setSelectedEmails((prev) => {
      const next = new Set(prev);
      if (next.has(email)) next.delete(email);
      else next.add(email);
      return next;
    });
  }

  function addExtraEmail() {
    const email = extraEmail.trim().replace(/,$/, "");
    if (!email || !email.includes("@")) return;
    if (!extraEmails.includes(email)) setExtraEmails((prev) => [...prev, email]);
    setExtraEmail("");
  }

  function removeExtraEmail(email: string) {
    setExtraEmails((prev) => prev.filter((e) => e !== email));
  }

  const allAttendeeEmails = useMemo(() => [...selectedEmails, ...extraEmails], [selectedEmails, extraEmails]);

  const filteredTasks = useMemo(() => {
    if (!taskFilter.trim()) return tasks;
    const q = taskFilter.toLowerCase();
    return tasks.filter((t) => t.task_text.toLowerCase().includes(q));
  }, [tasks, taskFilter]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || allAttendeeEmails.length === 0 || !start || !end) return;

    const startIso = new Date(start).toISOString();
    const endIso = new Date(end).toISOString();
    if (new Date(endIso) <= new Date(startIso)) {
      setError("End time must be after start time.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const fields = {
        title: title.trim(),
        attendee_emails: allAttendeeEmails,
        start_iso: startIso,
        end_iso: endIso,
        description: description.trim() || undefined,
      };
      const res = isReview && pendingMeeting
        ? await approveMeeting(pendingMeeting.meeting_id, fields)
        : await scheduleMeeting(projectId, fields);
      setResult({ meeting_link: (res as { meeting_link: string | null }).meeting_link });
      onDecided?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleReject() {
    if (!pendingMeeting) return;
    setRejecting(true);
    setError(null);
    try {
      await rejectMeeting(pendingMeeting.meeting_id);
      setRejected(true);
      onDecided?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRejecting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="glass-panel max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl p-5" onClick={(e) => e.stopPropagation()}>
        {rejected ? (
          <div className="flex flex-col items-center gap-3 py-6 text-center">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-surface-solid text-text-faint">
              <IconX className="h-5 w-5" />
            </div>
            <h2 className="text-[15px] font-semibold text-text-primary">Proposal rejected</h2>
            <p className="text-[13px] text-text-muted">No calendar event was created - nobody was notified.</p>
            <button
              type="button"
              onClick={onClose}
              className="hover-tint mt-2 rounded-lg border border-line bg-surface-solid px-4 py-2 text-[13px] font-medium text-text-primary transition"
            >
              Done
            </button>
          </div>
        ) : result ? (
          <div className="flex flex-col items-center gap-3 py-6 text-center">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-success-bg text-success">
              <IconCalendar className="h-5 w-5" />
            </div>
            <h2 className="text-[15px] font-semibold text-text-primary">Meeting scheduled</h2>
            <p className="text-[13px] text-text-muted">Invites were sent to every attendee automatically.</p>
            {result.meeting_link && (
              <a href={result.meeting_link} target="_blank" rel="noreferrer" className="text-[13px] text-info underline">
                {result.meeting_link}
              </a>
            )}
            <button
              type="button"
              onClick={onClose}
              className="approve-glow mt-2 rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-4 py-2 text-[13px] font-medium text-white transition"
            >
              Done
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-[15px] font-semibold text-text-primary">
                  <IconCalendar className="h-4.5 w-4.5" />
                  {isReview ? "Review Proposed Meeting" : "Schedule Meeting"}
                </h2>
                {isReview && (
                  <p className="mt-0.5 pl-6 text-[12px] text-text-faint">Detected automatically - review before it's created.</p>
                )}
              </div>
              <button type="button" onClick={onClose} className="hover-tint rounded-lg p-1.5 text-text-faint transition">
                <IconX className="h-4 w-4" />
              </button>
            </div>

            {isReview && pendingMeeting?.reason && (
              <p className="rounded-lg border border-line bg-surface-solid px-3 py-2 text-[12.5px] text-text-muted">
                <span className="font-semibold text-text-primary">Why: </span>
                {pendingMeeting.reason}
              </p>
            )}

            {isReview && pendingMeeting && pendingMeeting.unresolved_attendees.length > 0 && (
              <div className="flex items-start gap-2 rounded-lg border border-warning-line bg-warning-bg px-3 py-2 text-[12.5px] text-warning">
                <IconAlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  Couldn't resolve: {pendingMeeting.unresolved_attendees.join(", ")} - add them to this project's people, or add their
                  email below.
                </span>
              </div>
            )}

            <div>
              <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-text-faint">Title</label>
              <input
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value);
                  setTitleTouched(true);
                }}
                placeholder="Meeting title"
                required
                className="w-full rounded-lg border border-line bg-surface-solid px-3 py-2 text-[13.5px] text-text-primary outline-none focus:border-accent-to"
              />
            </div>

            {!isReview && (
            <div>
              <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                Related tasks{selectedTaskIds.size > 0 ? ` (${selectedTaskIds.size} selected)` : ""}
              </label>
              <input
                value={taskFilter}
                onChange={(e) => setTaskFilter(e.target.value)}
                placeholder="Search tasks…"
                className="mb-1.5 w-full rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to"
              />
              <div className="max-h-36 overflow-y-auto rounded-lg border border-line">
                {filteredTasks.length === 0 && <p className="px-3 py-2 text-[12.5px] text-text-faint">No tasks match.</p>}
                {filteredTasks.map((t) => (
                  <label
                    key={t.task_id}
                    className="hover-tint flex cursor-pointer items-start gap-2 border-b border-line px-3 py-2 text-[12.5px] transition last:border-b-0"
                  >
                    <input type="checkbox" checked={selectedTaskIds.has(t.task_id)} onChange={() => toggleTask(t.task_id)} className="mt-0.5" />
                    <span className="min-w-0 flex-1 text-text-primary">
                      {t.task_text}
                      {t.owner && <span className="text-text-faint"> · {t.owner}</span>}
                    </span>
                  </label>
                ))}
              </div>
            </div>
            )}

            <div>
              <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-text-faint">Attendees</label>
              {people === null && <p className="text-[12.5px] text-text-faint">Loading…</p>}
              {people && people.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {people.map((p) => (
                    <button
                      key={p.email}
                      type="button"
                      onClick={() => toggleAttendee(p.email)}
                      title={p.email}
                      className={`rounded-full border px-2.5 py-1 text-[12px] font-medium transition ${
                        selectedEmails.has(p.email)
                          ? "border-accent-to bg-gradient-to-br from-accent-from to-accent-to text-white"
                          : "hover-tint border-line bg-surface-solid text-text-muted"
                      }`}
                    >
                      {p.name}
                    </button>
                  ))}
                </div>
              )}
              <input
                value={extraEmail}
                onChange={(e) => setExtraEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    addExtraEmail();
                  }
                }}
                onBlur={addExtraEmail}
                placeholder="Add an email and press Enter"
                className="w-full rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to"
              />
              {extraEmails.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {extraEmails.map((email) => (
                    <span
                      key={email}
                      className="inline-flex items-center gap-1 rounded-full border border-line bg-surface-solid px-2.5 py-1 text-[12px] text-text-primary"
                    >
                      {email}
                      <button type="button" onClick={() => removeExtraEmail(email)} className="text-text-faint hover:text-danger">
                        <IconX className="h-3 w-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-text-faint">Start</label>
                <input
                  type="datetime-local"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                  required
                  className="w-full rounded-lg border border-line bg-surface-solid px-3 py-2 text-[12.5px] text-text-primary outline-none focus:border-accent-to"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-text-faint">End</label>
                <input
                  type="datetime-local"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                  required
                  className="w-full rounded-lg border border-line bg-surface-solid px-3 py-2 text-[12.5px] text-text-primary outline-none focus:border-accent-to"
                />
              </div>
            </div>

            <div>
              <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-text-faint">Description (optional)</label>
              <textarea
                value={description}
                onChange={(e) => {
                  setDescription(e.target.value);
                  setDescriptionTouched(true);
                }}
                rows={3}
                className="w-full resize-y rounded-lg border border-line bg-surface-solid px-3 py-2 text-[12.5px] text-text-primary outline-none focus:border-accent-to"
              />
            </div>

            {error && (
              <div className="rounded-lg border border-warning-line bg-warning-bg px-3 py-2 text-[12.5px] text-warning">{error}</div>
            )}

            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose} className="hover-tint rounded-lg px-3.5 py-2 text-[13px] text-text-muted transition">
                {isReview ? "Decide later" : "Cancel"}
              </button>
              {isReview && (
                <button
                  type="button"
                  onClick={handleReject}
                  disabled={rejecting || submitting}
                  className="hover-tint rounded-lg border border-danger/40 px-3.5 py-2 text-[13px] font-medium text-danger transition disabled:opacity-50"
                >
                  {rejecting ? "Rejecting…" : "Reject"}
                </button>
              )}
              <button
                type="submit"
                disabled={submitting || rejecting || !title.trim() || allAttendeeEmails.length === 0 || !start || !end}
                className="approve-glow rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-4 py-2 text-[13px] font-medium text-white transition disabled:opacity-50"
              >
                {submitting ? (isReview ? "Approving…" : "Scheduling…") : isReview ? "Approve & Schedule" : "Schedule Meeting"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
