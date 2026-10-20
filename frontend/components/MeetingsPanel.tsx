"use client";

import { useEffect, useState } from "react";
import type { Meeting } from "@/lib/types";
import { fetchProjectMeetings } from "@/lib/api";
import { IconAlertTriangle, IconCalendar, IconCheckCircle, IconX } from "./icons";

const STATUS_META: Record<Meeting["status"], { label: string; className: string }> = {
  pending_approval: { label: "Pending approval", className: "border-warning-line bg-warning-bg text-warning" },
  scheduled: { label: "Scheduled", className: "border-success/40 bg-success-bg text-success" },
  rejected: { label: "Rejected", className: "border-line bg-surface-solid text-text-faint" },
};

function formatRange(startIso: string, endIso: string): string {
  const start = new Date(startIso);
  const end = new Date(endIso);
  const sameDay = start.toDateString() === end.toDateString();
  const dateFmt: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" };
  const timeFmt: Intl.DateTimeFormatOptions = { hour: "numeric", minute: "2-digit" };
  return sameDay
    ? `${start.toLocaleDateString(undefined, dateFmt)} · ${start.toLocaleTimeString(undefined, timeFmt)}-${end.toLocaleTimeString(undefined, timeFmt)}`
    : `${start.toLocaleString(undefined, { ...dateFmt, ...timeFmt })} - ${end.toLocaleString(undefined, { ...dateFmt, ...timeFmt })}`;
}

export default function MeetingsPanel({
  projectId,
  refreshKey,
  onReview,
  onOpenPeople,
}: {
  projectId: string;
  // bump this from the parent whenever a meeting decision happens elsewhere, to refetch
  refreshKey?: number;
  onReview: (meeting: Meeting) => void;
  // Jumps to the People tab, where unresolved attendees get their email added.
  onOpenPeople?: () => void;
}) {
  const [meetings, setMeetings] = useState<Meeting[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchProjectMeetings(projectId)
      .then((r) => !cancelled && setMeetings(r.meetings))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [projectId, refreshKey]);

  if (error) return <p className="text-[13px] text-danger">{error}</p>;
  if (!meetings) return <p className="text-[13px] text-text-faint">Loading…</p>;
  if (meetings.length === 0) {
    return <p className="text-[13px] text-text-faint">No meetings yet - the Meeting Coordinator proposes one here when meeting minutes call for a follow-up.</p>;
  }

  const pending = meetings.filter((m) => m.status === "pending_approval");
  const unresolvedNames = Array.from(new Set(pending.flatMap((m) => m.unresolved_attendees)));

  return (
    <div className="flex flex-col gap-2">
      {pending.length > 0 && (
        <div className="flex items-start gap-2.5 rounded-xl border border-warning-line bg-warning-bg px-3.5 py-2.5 text-[12.5px]">
          <IconAlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
          <div className="min-w-0 text-text-primary">
            <p className="font-medium">
              {pending.length === 1 ? "1 meeting needs your approval" : `${pending.length} meetings need your approval`}
            </p>
            <p className="mt-0.5 text-text-muted">
              Nothing is scheduled until you approve it. Use Review on the meeting below whenever you are ready, or leave it pending.
              {unresolvedNames.length > 0 &&
                ` ${unresolvedNames.length === 1 ? "1 attendee has" : `${unresolvedNames.length} attendees have`} no email on file yet.`}
            </p>
          </div>
        </div>
      )}
      {meetings.map((m) => {
        const meta = STATUS_META[m.status];
        return (
          <div key={m.meeting_id} className="glass-panel rounded-xl p-3.5">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-semibold text-text-primary">{m.title}</p>
                <p className="mt-0.5 text-[12px] text-text-faint">{formatRange(m.start_iso, m.end_iso)}</p>
                <p className="mt-0.5 truncate text-[12px] text-text-faint">{m.attendee_emails.join(", ")}</p>
              </div>
              <span className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium ${meta.className}`}>
                {m.status === "scheduled" && <IconCheckCircle className="h-3 w-3" />}
                {m.status === "rejected" && <IconX className="h-3 w-3" />}
                {m.status === "pending_approval" && <IconAlertTriangle className="h-3 w-3" />}
                {meta.label}
              </span>
            </div>

            {m.status === "pending_approval" && m.unresolved_attendees.length > 0 && (
              <p className="mt-2 text-[12px] text-warning">
                Couldn&apos;t match to an email: {m.unresolved_attendees.join(", ")}
                {onOpenPeople && (
                  <>
                    {" "}-{" "}
                    <button type="button" onClick={onOpenPeople} className="font-medium underline">
                      add them in People
                    </button>
                  </>
                )}
              </p>
            )}

            <div className="mt-2 flex items-center justify-between gap-2">
              {m.status === "scheduled" && m.meeting_link ? (
                <a href={m.meeting_link} target="_blank" rel="noreferrer" className="text-[12px] text-info underline">
                  View on calendar
                </a>
              ) : (
                <span />
              )}
              {m.status === "pending_approval" && (
                <button
                  type="button"
                  onClick={() => onReview(m)}
                  className="hover-tint inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-solid px-2.5 py-1 text-[12px] font-medium text-text-primary transition"
                >
                  <IconCalendar className="h-3 w-3" />
                  Review
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
