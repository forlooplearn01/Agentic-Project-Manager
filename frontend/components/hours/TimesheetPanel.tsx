"use client";

import type { TimesheetInbox } from "@/lib/types";
import IncomingTimesheets from "./IncomingTimesheets";

// Hours > Timesheet: timesheets arrive from the project's timesheet folder; nothing is typed in here. Imported files can be undone and held
// ones reviewed from the card; the hours themselves show on the Overview and Capacity tabs.
export default function TimesheetPanel({
  projectId,
  inbox,
  reloadInbox,
  onChanged,
}: {
  projectId: string;
  inbox: TimesheetInbox | null;
  reloadInbox: () => void;
  onChanged: () => void;
}) {
  return (
    <div className="flex flex-col gap-5">
      <IncomingTimesheets projectId={projectId} inbox={inbox} reload={reloadInbox} onChanged={onChanged} />
    </div>
  );
}
