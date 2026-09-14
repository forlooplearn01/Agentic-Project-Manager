"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { fetchProjects } from "@/lib/api";
import type { ProjectRollup } from "@/lib/types";
import { IconFolder, IconSearch } from "@/components/icons";

export default function SearchPage() {
  return (
    <Suspense fallback={null}>
      <SearchResults />
    </Suspense>
  );
}

function SearchResults() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = (searchParams.get("q") ?? "").trim();

  const [projects, setProjects] = useState<ProjectRollup[] | null>(null);

  useEffect(() => {
    fetchProjects()
      .then((r) => setProjects(r.projects))
      .catch(() => {});
  }, []);

  const matches = (projects ?? []).filter((p) => p.project_id.toLowerCase().includes(query.toLowerCase()));

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <header className="mb-7">
        <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-text-primary">
          <IconSearch className="h-4.5 w-4.5 text-text-faint" />
          {query ? `Results for "${query}"` : "Search"}
        </h1>
        <p className="text-[12.5px] text-text-muted">
          Searches project names for now - task-level search is planned for a later phase.
        </p>
      </header>

      {!query && <p className="text-[13px] text-text-faint">Type a query and press enter to search.</p>}

      {query && !projects && <p className="text-[13px] text-text-faint">Loading…</p>}

      {query && projects && matches.length === 0 && <p className="text-[13px] text-text-faint">No projects match “{query}”.</p>}

      {query && matches.length > 0 && (
        <div className="flex flex-col gap-2">
          {matches.map((p) => (
            <button
              key={p.project_id}
              onClick={() => router.push(`/products/task-manager?project=${encodeURIComponent(p.project_id)}`)}
              className="hover-tint glass-panel flex items-center gap-2.5 rounded-xl p-3.5 text-left transition"
            >
              <IconFolder className="h-4 w-4 shrink-0 text-text-faint" />
              <span className="text-[13.5px] font-medium text-text-primary">{p.project_id}</span>
              <span className="ml-auto text-[12px] text-text-faint">{p.total_tasks} tasks</span>
            </button>
          ))}
        </div>
      )}
    </main>
  );
}
