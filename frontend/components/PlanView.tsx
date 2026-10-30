"use client";

import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { fetchPlanSummary, fetchProjectTasks, setPlanBaseline } from "@/lib/api";
import type { Deliverable, PlanSummary, Task } from "@/lib/types";
import ImportButton from "./hours/ImportReview";
import { errorText } from "./hours/shared";
import AddTaskPanel from "./plan/AddTaskPanel";
import ChangeHistoryPanel from "./plan/ChangeHistoryPanel";
import PlanSummaryStrip from "./plan/PlanSummaryStrip";
import TaskTracker from "./TaskTracker";

// Workspace > Tasks > Plan: the project plan the project manager maintains here. It is loaded from a file ONCE (the upload is offered only
// while the project has no plan) and edited in place afterwards; the baseline and the change history say how it has moved.
export default function PlanView({
  projectId,
  tasks,
  deliverables,
  onTasksChange,
}: {
  projectId: string;
  tasks: Task[];
  deliverables: Deliverable[];
  onTasksChange: Dispatch<SetStateAction<Task[] | null>>;
}) {
  const [summary, setSummary] = useState<PlanSummary | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // After any change to the plan: reload the tasks and the summary together so the table, the strip and the history agree.
  const reload = useCallback(async () => {
    try {
      const [t, s] = await Promise.all([fetchProjectTasks(projectId), fetchPlanSummary(projectId)]);
      onTasksChange(t.tasks);
      setSummary(s);
      setHistoryKey((k) => k + 1);
    } catch (e) {
      setError(errorText(e));
    }
  }, [projectId, onTasksChange]);

  useEffect(() => {
    setSummary(null);
    setError(null);
    fetchPlanSummary(projectId)
      .then(setSummary)
      .catch((e) => setError(errorText(e)));
  }, [projectId]);

  async function handleSetBaseline() {
    setBusy(true);
    setError(null);
    try {
      setSummary(await setPlanBaseline(projectId));
      setHistoryKey((k) => k + 1);
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const owners = Array.from(new Set(tasks.map((t) => t.owner).filter((o): o is string => !!o))).sort();
  const phases = Array.from(new Set(tasks.map((t) => t.phase).filter((p): p is string => !!p))).sort();

  return (
    <div className="flex flex-col gap-3">
      {error && <p className="text-[13px] text-danger">{error}</p>}

      {summary && !summary.has_plan && (
        <div className="glass-panel flex flex-col gap-2 rounded-xl p-4">
          <h3 className="text-[13px] font-semibold text-text-primary">Load the plan</h3>
          <p className="text-[12.5px] text-text-muted">
            Upload the project plan once (an Excel or CSV file with tasks, estimates and planned weeks). After that it lives here and you edit it in this table;
            there is no second upload.
          </p>
          <ImportButton projectId={projectId} kind="plan" label="Upload plan" onImported={reload} />
        </div>
      )}

      {summary && (
        <PlanSummaryStrip
          summary={summary}
          historyOpen={historyOpen}
          busy={busy}
          onToggleHistory={() => setHistoryOpen((o) => !o)}
          onAddTask={() => setAdding(true)}
          onSetBaseline={handleSetBaseline}
        />
      )}
      {adding && (
        <AddTaskPanel
          projectId={projectId}
          owners={owners}
          phases={phases}
          onAdded={() => {
            setAdding(false);
            reload();
          }}
          onCancel={() => setAdding(false)}
        />
      )}
      {historyOpen && <ChangeHistoryPanel projectId={projectId} refreshKey={historyKey} />}

      {tasks.length > 0 ? (
        <TaskTracker tasks={tasks} deliverables={deliverables} onTasksChange={onTasksChange} hasBaseline={!!summary?.baseline_set_at} onPlanChange={reload} />
      ) : (
        <p className="text-[13px] text-text-faint">No tasks yet in this project. Add one above or upload the plan.</p>
      )}
    </div>
  );
}
