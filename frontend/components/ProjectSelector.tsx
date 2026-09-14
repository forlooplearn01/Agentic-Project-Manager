import type { ProjectRollup } from "@/lib/types";

export default function ProjectSelector({
  projects,
  selected,
  onSelect,
}: {
  projects: ProjectRollup[];
  selected: string | null;
  onSelect: (projectId: string | null) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <label htmlFor="project-select" className="text-[11px] uppercase tracking-wide text-text-faint">
        Project
      </label>
      <select
        id="project-select"
        value={selected ?? ""}
        onChange={(e) => onSelect(e.target.value || null)}
        className="min-w-[220px] rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[13px] font-medium text-text-primary outline-none focus:border-accent-to"
      >
        <option value="">All projects</option>
        {projects.map((p) => (
          <option key={p.project_id} value={p.project_id}>
            {p.project_id} ({p.total_tasks})
          </option>
        ))}
      </select>
    </div>
  );
}
