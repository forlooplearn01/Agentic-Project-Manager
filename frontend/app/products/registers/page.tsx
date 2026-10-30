"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { fetchDeliverables, fetchProjects, fetchRaci, fetchRaidItems, registersExportUrl } from "@/lib/api";
import { getDefaultProject, setDefaultProject } from "@/lib/preferences";
import type { Deliverable, ProjectRollup, RaciMatrix, RaciSubject, RaidItem } from "@/lib/types";
import ProjectSelector from "@/components/ProjectSelector";
import Tabs, { type TabDef } from "@/components/Tabs";
import RaidLog from "@/components/RaidLog";
import RaciMatrixView from "@/components/RaciMatrix";
import { IconDownload } from "@/components/icons";
import DeliverablesRegister from "@/components/DeliverablesRegister";

type TabId = "raid" | "raci" | "deliverables";
const TAB_IDS: TabId[] = ["raid", "raci", "deliverables"];

export default function RegistersPage() {
  return (
    <Suspense fallback={null}>
      <Registers />
    </Suspense>
  );
}

function Registers() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedProject = searchParams.get("project");
  const tabParam = searchParams.get("tab");
  const activeTab: TabId = TAB_IDS.includes(tabParam as TabId) ? (tabParam as TabId) : "raid";

  const [projects, setProjects] = useState<ProjectRollup[] | null>(null);
  const [raidItems, setRaidItems] = useState<RaidItem[] | null>(null);
  const [matrix, setMatrix] = useState<RaciMatrix | null>(null);
  const [deliverables, setDeliverables] = useState<Deliverable[] | null>(null);
  // null = not chosen yet: show deliverables when the project has any, otherwise tasks.
  const [raciSubjectChoice, setRaciSubjectChoice] = useState<RaciSubject | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checkedDefaultProject, setCheckedDefaultProject] = useState(false);

  const fail = useCallback((e: unknown) => setError(e instanceof Error ? e.message : String(e)), []);

  useEffect(() => {
    fetchProjects()
      .then((r) => setProjects(r.projects))
      .catch(fail);
  }, [fail]);

  const confirmedDeliverables = deliverables?.filter((d) => d.review_state === "confirmed").length ?? 0;
  const raciSubject: RaciSubject | null = raciSubjectChoice ?? (deliverables === null ? null : confirmedDeliverables > 0 ? "deliverable" : "task");

  useEffect(() => {
    setRaidItems(null);
    setDeliverables(null);
    setMatrix(null);
    setRaciSubjectChoice(null);
    setError(null);
    if (!selectedProject) return;
    let cancelled = false;
    fetchRaidItems(selectedProject)
      .then((r) => !cancelled && setRaidItems(r.items))
      .catch((e) => !cancelled && fail(e));
    fetchDeliverables(selectedProject)
      .then((r) => !cancelled && setDeliverables(r.deliverables))
      .catch((e) => !cancelled && fail(e));
    return () => {
      cancelled = true;
    };
  }, [selectedProject, fail]);

  useEffect(() => {
    if (!selectedProject || !raciSubject) return;
    let cancelled = false;
    setMatrix(null);
    fetchRaci(selectedProject, raciSubject)
      .then((m) => !cancelled && setMatrix(m))
      .catch((e) => !cancelled && fail(e));
    return () => {
      cancelled = true;
    };
  }, [selectedProject, raciSubject, fail]);

  // On first load with no ?project= in the URL, fall back to the saved default (same behaviour as Workspace).
  useEffect(() => {
    if (checkedDefaultProject) return;
    setCheckedDefaultProject(true);
    if (!selectedProject) {
      const def = getDefaultProject();
      if (def) {
        const params = new URLSearchParams(searchParams.toString());
        params.set("project", def);
        router.replace(`/products/registers?${params.toString()}`);
      }
    }
  }, [checkedDefaultProject, selectedProject, searchParams, router]);

  function handleSelectProject(projectId: string | null) {
    setDefaultProject(projectId);
    const params = new URLSearchParams(searchParams.toString());
    if (projectId) params.set("project", projectId);
    else params.delete("project");
    router.push(`/products/registers?${params.toString()}`);
  }

  function handleTabChange(next: TabId) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "raid") params.delete("tab");
    else params.set("tab", next);
    const query = params.toString();
    router.replace(query ? `/products/registers?${query}` : "/products/registers", { scroll: false });
  }

  const currentProject = projects?.find((p) => p.project_id === selectedProject);
  const suggestedCount = raidItems?.filter((i) => i.review_state === "suggested").length ?? 0;
  const attentionRows = matrix?.rows.filter((r) => r.warnings.length > 0).length ?? 0;
  const tabs: TabDef<TabId>[] = [
    { id: "raid", label: "RAID log", badge: suggestedCount, badgeTitle: `${suggestedCount} item(s) suggested by the assistant, waiting for your review` },
    { id: "raci", label: "RACI matrix", badge: attentionRows, badgeTitle: `${attentionRows} row(s) with incomplete RACI roles` },
    { id: "deliverables", label: "Deliverables" },
  ];

  return (
    <main className="mx-auto max-w-[1800px] px-6 py-8">
      <header className="mb-7 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-text-primary">Registers</h1>
          <p className="text-[12.5px] text-text-muted">RAID log, RACI matrix and deliverables for one project at a time.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {projects && projects.length > 0 && (
            <ProjectSelector projects={projects} selected={selectedProject} onSelect={handleSelectProject} />
          )}
          {currentProject && (
            <a
              href={registersExportUrl(currentProject.project_id)}
              download
              title="A read-only Excel snapshot: RAID log, RACI (by deliverable and by task) and deliverables. Items still waiting for review are left out."
              className="hover-tint inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary"
            >
              <IconDownload className="h-3.5 w-3.5" />
              Export to Excel
            </a>
          )}
        </div>
      </header>

      {error && <p className="mb-4 text-[13px] text-danger">{error}</p>}
      {!projects && !error && <p className="text-[13px] text-text-faint">Loading…</p>}

      {projects && projects.length === 0 && (
        <div className="glass-panel rounded-2xl p-6 text-center">
          <p className="mb-3 text-[13px] text-text-muted">No projects yet.</p>
          <Link
            href="/products/extractor"
            className="hover-tint inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-4 py-2 text-[13px] font-medium text-white transition"
          >
            Submit an SOW in Extractor
          </Link>
        </div>
      )}

      {projects && projects.length > 0 && !currentProject && (
        <div className="glass-panel rounded-2xl p-6 text-center">
          <p className="text-[13px] text-text-muted">Select a project above to open its registers.</p>
        </div>
      )}

      {currentProject && (
        <div className="flex min-w-0 flex-col gap-5">
          <Tabs tabs={tabs} active={activeTab} onChange={handleTabChange} ariaLabel="Registers" />

          {activeTab === "raid" && (
            <div role="tabpanel" id="tabpanel-raid" aria-labelledby="tab-raid">
              <p className="mb-3 max-w-3xl text-[12.5px] text-text-muted">
                Risks, Assumptions, Issues and Dependencies. A living log - update status as things change, and review anything the assistant suggests.
              </p>
              {!raidItems && <p className="text-[13px] text-text-faint">Loading…</p>}
              {raidItems && <RaidLog projectId={currentProject.project_id} items={raidItems} onItemsChange={setRaidItems} />}
            </div>
          )}

          {activeTab === "raci" && (
            <div role="tabpanel" id="tabpanel-raci" aria-labelledby="tab-raci">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <p className="max-w-3xl text-[12.5px] text-text-muted">
                  Who is Responsible, Accountable, Consulted and Informed. Click a cell to assign roles.
                  {raciSubject === "task" && confirmedDeliverables === 0 && (
                    <>
                      {" "}
                      This project has no deliverables yet, so the rows are your tasks.{" "}
                      <button type="button" onClick={() => handleTabChange("deliverables")} className="underline">
                        Add deliverables
                      </button>{" "}
                      to assign roles per deliverable instead.
                    </>
                  )}
                </p>
                <div className="flex gap-1 rounded-lg border border-line bg-surface-solid p-1">
                  {(["deliverable", "task"] as RaciSubject[]).map((sub) => (
                    <button
                      key={sub}
                      type="button"
                      onClick={() => setRaciSubjectChoice(sub)}
                      className={`rounded-md px-3 py-1 text-[12.5px] font-medium transition ${
                        raciSubject === sub ? "bg-gradient-to-br from-accent-from to-accent-to text-white" : "text-text-muted hover-tint"
                      }`}
                    >
                      {sub === "deliverable" ? "Deliverables" : "Tasks"}
                    </button>
                  ))}
                </div>
              </div>
              {(!matrix || !raciSubject) && <p className="text-[13px] text-text-faint">Loading…</p>}
              {matrix && raciSubject && (
                <RaciMatrixView
                  projectId={currentProject.project_id}
                  subject={raciSubject}
                  matrix={matrix}
                  onMatrixChange={setMatrix}
                  onOpenDeliverables={() => handleTabChange("deliverables")}
                />
              )}
            </div>
          )}

          {activeTab === "deliverables" && (
            <div role="tabpanel" id="tabpanel-deliverables" aria-labelledby="tab-deliverables">
              <p className="mb-3 max-w-3xl text-[12.5px] text-text-muted">
                What you hand over and get signed off.{" "}
                {confirmedDeliverables > 0
                  ? "Each one is a row in the RACI matrix (Deliverables view)."
                  : "Until you add some, the RACI matrix shows your tasks instead."}
              </p>
              {!deliverables && <p className="text-[13px] text-text-faint">Loading…</p>}
              {deliverables && (
                <DeliverablesRegister projectId={currentProject.project_id} items={deliverables} onItemsChange={setDeliverables} />
              )}
            </div>
          )}
        </div>
      )}
    </main>
  );
}
