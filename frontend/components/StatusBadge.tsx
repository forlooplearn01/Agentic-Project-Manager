import type { Status } from "@/lib/types";

const STYLES: Record<Status, string> = {
  not_started: "text-text-muted bg-surface",
  in_progress: "text-info bg-info-bg",
  completed: "text-success bg-success-bg",
  blocked: "text-danger bg-danger-bg",
  on_hold: "text-warning bg-warning-bg",
};

const LABELS: Record<Status, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  completed: "Completed",
  blocked: "Blocked",
  on_hold: "On hold",
};

export default function StatusBadge({ status }: { status: Status | string | null }) {
  const s = (status ?? "not_started") as Status;
  return (
    <span className={`inline-block rounded-md px-2 py-0.5 text-[11px] font-medium ${STYLES[s] ?? STYLES.not_started}`}>
      {LABELS[s] ?? s}
    </span>
  );
}
