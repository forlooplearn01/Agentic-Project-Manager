"use client";

import { useEffect, useState } from "react";
import { createStarterPlan, fetchPlanSummary, fetchProjectFolders } from "@/lib/api";
import type { PlanSummary, ProjectFolders, Task } from "@/lib/types";
import { formatDateTime } from "@/lib/format";
import { BTN_QUIET, errorText } from "./hours/shared";
import ChangeHistoryPanel from "./plan/ChangeHistoryPanel";
import PlanSummaryStrip from "./plan/PlanSummaryStrip";
import TaskTracker from "./TaskTracker";

// Workspace > Tasks > Plan: a VIEW of the project plan. The plan lives in an Excel file in the project's plan folder and is read again whenever
// it changes; the baseline is the first version read and the change history is what changed between versions. Nothing here is edited.
export default function PlanView({ projectId, tasks }: { projectId: string; tasks: Task[] }) {
  const [summary, setSummary] = useState<PlanSummary | null>(null);
  const [folders, setFolders] = useState<ProjectFolders | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSummary(null);
    setFolders(null);
    setError(null);
    fetchPlanSummary(projectId)
      .then(setSummary)
      .catch((e) => setError(errorText(e)));
    fetchProjectFolders(projectId)
      .then(setFolders)
      .catch(() => setFolders(null));
  }, [projectId]);

  async function makeStarter() {
    setError(null);
    try {
      await createStarterPlan(projectId);
      const [s, f] = await Promise.all([fetchPlanSummary(projectId), fetchProjectFolders(projectId)]);
      setSummary(s);
      setFolders(f);
    } catch (e) {
      setError(errorText(e));
    }
  }

  const lastRead = folders?.plan.files.find((f) => f.status === "applied");
  const held = folders?.plan.files[0]?.status === "held" ? folders.plan.files[0] : null;

  return (
    <div className="flex flex-col gap-3">
      {error && <p className="text-[13px] text-danger">{error}</p>}

      {summary && <PlanSummaryStrip summary={summary} historyOpen={historyOpen} onToggleHistory={() => setHistoryOpen((o) => !o)} />}

      {folders && (
        <p className="text-[12.5px] text-text-faint">
          Plan file: <code className="rounded bg-surface px-1 py-0.5 text-[12px]">{folders.plan.current_file ? `${folders.plan.folder}/${folders.plan.current_file}` : folders.plan.folder}</code>
          {lastRead ? `, last read ${formatDateTime(lastRead.applied_at ?? lastRead.created_at)}` : ", not read yet"}. Edit the plan in that file; the app reads it again within seconds.
        </p>
      )}
      {folders && !folders.plan.current_file && tasks.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-text-muted">
          <span>These tasks do not have a plan file yet. Create one from them, then keep editing it in Excel.</span>
          <button type="button" onClick={makeStarter} className={BTN_QUIET}>Create the plan file</button>
        </div>
      )}
      {held && (
        <p className="rounded-lg border border-warning-line bg-warning-bg px-3 py-2 text-[12.5px] text-warning">
          The latest version of the plan was not applied: {held.reason} See Suggestions.
        </p>
      )}
      {historyOpen && <ChangeHistoryPanel projectId={projectId} refreshKey={0} />}

      {tasks.length > 0 ? (
        <TaskTracker tasks={tasks} hasBaseline={!!summary?.baseline_set_at} />
      ) : (
        <div className="glass-panel rounded-xl p-5 text-[13px] text-text-muted">
          <p className="font-medium text-text-primary">No plan yet.</p>
          <p className="mt-1">
            Run the project&apos;s SOW in Extractor (it creates a plan file from the SOW&apos;s tasks), or copy your own plan (an Excel file with a Task, Owner, Start and Finish week, Est. hours and Status column) into{" "}
            <code className="rounded bg-surface px-1 py-0.5 text-[12px]">{folders?.plan.folder ?? `projects/${projectId}/plan`}</code>. It is read within a few seconds and shows up here.
          </p>
        </div>
      )}
    </div>
  );
}
