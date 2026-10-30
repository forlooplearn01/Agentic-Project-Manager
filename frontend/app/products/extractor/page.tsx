"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchProjects } from "@/lib/api";
import { getDefaultProject, setDefaultProject } from "@/lib/preferences";
import type { ProjectRollup } from "@/lib/types";
import ProjectSelector from "@/components/ProjectSelector";
import RunPanel from "@/components/RunPanel";

export default function ExtractorPage() {
  const [projects, setProjects] = useState<ProjectRollup[] | null>(null);
  const [selectedProject, setSelectedProjectState] = useState<string | null>(() => getDefaultProject());
  const [completedProject, setCompletedProject] = useState<string | null>(null);

  function setSelectedProject(projectId: string | null) {
    setSelectedProjectState(projectId);
    setDefaultProject(projectId);
  }

  useEffect(() => {
    fetchProjects()
      .then((r) => setProjects(r.projects))
      .catch(() => {});
  }, []);

  function handleCompleted() {
    setCompletedProject(selectedProject);
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <header className="mb-7 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-text-primary">Extractor</h1>
          <p className="text-[12.5px] text-text-muted">Turn an SOW into a plan, then keep it current with meeting minutes.</p>
        </div>
        {projects && projects.length > 0 && (
          <ProjectSelector projects={projects} selected={selectedProject} onSelect={setSelectedProject} />
        )}
      </header>

      {/* key forces a remount when the project changes, so the project id field and any
          previous run result reset rather than sticking from the last selection. */}
      <RunPanel key={selectedProject ?? "none"} defaultProjectId={selectedProject ?? undefined} onCompleted={handleCompleted} />

      {completedProject && (
        <div className="mt-4 flex justify-end">
          <Link
            href={`/products/task-manager?project=${encodeURIComponent(completedProject)}`}
            className="hover-tint inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-solid px-3.5 py-2 text-[13px] font-medium text-text-muted transition hover:text-text-primary"
          >
            Open in Workspace →
          </Link>
        </div>
      )}
    </main>
  );
}
