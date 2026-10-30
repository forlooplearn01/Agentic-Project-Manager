"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { fetchHours, fetchProjectPeople, fetchProjects } from "@/lib/api";
import { getDefaultProject, setDefaultProject } from "@/lib/preferences";
import type { HoursReport, ProjectPerson, ProjectRollup } from "@/lib/types";
import ProjectSelector from "@/components/ProjectSelector";
import Tabs, { type TabDef } from "@/components/Tabs";
import HoursOverview from "@/components/hours/HoursOverview";
import TimesheetPanel from "@/components/hours/TimesheetPanel";
import CapacityPanel from "@/components/hours/CapacityPanel";
import { StatusPill } from "@/components/hours/shared";
import { useTimesheetInbox } from "@/components/hours/IncomingTimesheets";

type TabId = "overview" | "timesheet" | "capacity";
const TAB_IDS: TabId[] = ["overview", "timesheet", "capacity"];

export default function HoursPage() {
  return (
    <Suspense fallback={null}>
      <Hours />
    </Suspense>
  );
}

function Hours() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedProject = searchParams.get("project");
  const tabParam = searchParams.get("tab");
  const activeTab: TabId = TAB_IDS.includes(tabParam as TabId) ? (tabParam as TabId) : "overview";

  const [projects, setProjects] = useState<ProjectRollup[] | null>(null);
  const [report, setReport] = useState<HoursReport | null>(null);
  const [people, setPeople] = useState<ProjectPerson[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [checkedDefaultProject, setCheckedDefaultProject] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const fail = useCallback((e: unknown) => setError(e instanceof Error ? e.message : String(e)), []);

  useEffect(() => {
    fetchProjects()
      .then((r) => setProjects(r.projects))
      .catch(fail);
  }, [fail]);

  const reload = useCallback(() => {
    if (!selectedProject) return;
    fetchHours(selectedProject).then(setReport).catch(fail);
  }, [selectedProject, fail]);

  // A file was picked up from the project's timesheet folder (or undone): refresh the report and the grid.
  const { inbox, reload: reloadInbox } = useTimesheetInbox(
    selectedProject ?? "",
    useCallback(() => {
      reload();
      setReloadKey((k) => k + 1);
    }, [reload])
  );

  useEffect(() => {
    setReport(null);
    setPeople([]);
    setError(null);
    if (!selectedProject) return;
    let cancelled = false;
    fetchHours(selectedProject)
      .then((r) => !cancelled && setReport(r))
      .catch((e) => !cancelled && fail(e));
    fetchProjectPeople(selectedProject)
      .then((r) => !cancelled && setPeople(r.people))
      .catch((e) => !cancelled && fail(e));
    return () => {
      cancelled = true;
    };
  }, [selectedProject, fail]);

  useEffect(() => {
    if (checkedDefaultProject) return;
    setCheckedDefaultProject(true);
    if (!selectedProject) {
      const def = getDefaultProject();
      if (def) {
        const params = new URLSearchParams(searchParams.toString());
        params.set("project", def);
        router.replace(`/products/hours?${params.toString()}`);
      }
    }
  }, [checkedDefaultProject, selectedProject, searchParams, router]);

  function handleSelectProject(projectId: string | null) {
    setDefaultProject(projectId);
    const params = new URLSearchParams(searchParams.toString());
    if (projectId) params.set("project", projectId);
    else params.delete("project");
    router.push(`/products/hours?${params.toString()}`);
  }

  function handleTabChange(next: TabId) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "overview") params.delete("tab");
    else params.set("tab", next);
    const query = params.toString();
    router.replace(query ? `/products/hours?${query}` : "/products/hours", { scroll: false });
  }

  const currentProject = projects?.find((p) => p.project_id === selectedProject);
  const tabs: TabDef<TabId>[] = [
    { id: "overview", label: "Overview" },
    { id: "timesheet", label: "Timesheet" },
    { id: "capacity", label: "Capacity" },
  ];

  return (
    <main className="mx-auto max-w-[1800px] px-6 py-8">
      <header className="mb-7 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-3 text-lg font-semibold tracking-tight text-text-primary">
            Hours
            {report && selectedProject && <StatusPill status={report.burn_check.status} />}
          </h1>
          <p className="text-[12.5px] text-text-muted">Budget, hours worked and what is left, one project at a time.</p>
        </div>
        {projects && projects.length > 0 && <ProjectSelector projects={projects} selected={selectedProject} onSelect={handleSelectProject} />}
      </header>

      {error && <p className="mb-4 text-[13px] text-danger">{error}</p>}
      {!projects && !error && <p className="text-[13px] text-text-faint">Loading…</p>}

      {projects && projects.length === 0 && (
        <div className="glass-panel rounded-2xl p-6 text-center">
          <p className="mb-3 text-[13px] text-text-muted">No projects yet.</p>
          <Link href="/products/extractor" className="hover-tint inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-4 py-2 text-[13px] font-medium text-white transition">
            Submit an SOW in Extractor
          </Link>
        </div>
      )}

      {projects && projects.length > 0 && !currentProject && (
        <div className="glass-panel rounded-2xl p-6 text-center">
          <p className="text-[13px] text-text-muted">Select a project above to see its hours.</p>
        </div>
      )}

      {currentProject && (
        <div className="flex min-w-0 flex-col gap-5">
          <Tabs tabs={tabs} active={activeTab} onChange={handleTabChange} ariaLabel="Hours" />
          {!report && !error && <p className="text-[13px] text-text-faint">Loading…</p>}
          {report && activeTab === "overview" && (
            <div role="tabpanel" id="tabpanel-overview" aria-labelledby="tab-overview">
              <HoursOverview key={`${selectedProject}-${report.budget_hours}-${report.kickoff_date}`} projectId={currentProject.project_id} report={report} onChanged={reload} />
            </div>
          )}
          {report && activeTab === "timesheet" && (
            <div role="tabpanel" id="tabpanel-timesheet" aria-labelledby="tab-timesheet">
              <TimesheetPanel projectId={currentProject.project_id} people={people} weekStartDay={report.week_start_day} inbox={inbox} reloadInbox={reloadInbox} reloadKey={reloadKey} onChanged={reload} />
            </div>
          )}
          {report && activeTab === "capacity" && (
            <div role="tabpanel" id="tabpanel-capacity" aria-labelledby="tab-capacity">
              <CapacityPanel projectId={currentProject.project_id} people={people} onPeopleChanged={(p) => { setPeople(p); reload(); }} />
            </div>
          )}
        </div>
      )}
    </main>
  );
}
