import type { Priority } from "@/lib/types";

const STYLES: Record<Priority, string> = {
  high: "text-danger bg-danger-bg",
  medium: "text-warning bg-warning-bg",
  low: "text-text-muted bg-surface",
};

export default function PriorityBadge({ priority }: { priority: Priority }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide ${STYLES[priority] ?? STYLES.medium}`}>
      {priority}
    </span>
  );
}
