"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { fetchProjectMeetings, fetchProjectTasks, fetchProjects, projectExportUrl } from "@/lib/api";
import {
  getActivityFeedCollapsed,
  getDefaultProject,
  getDefaultView,
  setActivityFeedCollapsed,
  setDefaultProject,
  setDefaultView,
} from "@/lib/preferences";
import type { Meeting, ProjectRollup, Task } from "@/lib/types";
import StatCard from "@/components/StatCard";
import ProjectSelector from "@/components/ProjectSelector";
import TaskBoard from "@/components/TaskBoard";
import TaskTracker from "@/components/TaskTracker";
import ActivityFeed from "@/components/ActivityFeed";
import MeetingsPanel from "@/components/MeetingsPanel";
import PeoplePanel from "@/components/PeoplePanel";
import ProjectSettings from "@/components/ProjectSettings";
import ScheduleMeetingModal from "@/components/ScheduleMeetingModal";
import DeleteProjectModal from "@/components/DeleteProjectModal";
import { IconAlertTriangle, IconCalendar, IconChecklist, IconClock, IconDownload, IconFlame, IconFolder, IconTrash } from "@/components/icons";

export default function TaskManagerPage() {
  return (
    <Suspense fallback={null}>
      <TaskManager />
    </Suspense>
  );
}

function TaskManager() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedProject = searchParams.get("project");

  const [projects, setProjects] = useState<ProjectRollup[] | null>(null);
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setViewState] = useState<"board" | "list">(() => getDefaultView());
  const [checkedDefaultProject, setCheckedDefaultProject] = useState(false);
  const [schedulingMeeting, setSchedulingMeeting] = useState(false);
  const [deletingProject, setDeletingProject] = useState(false);
  const [reviewingMeeting, setReviewingMeeting] = useState<Meeting | null>(null);
  const [meetingsRefreshKey, setMeetingsRefreshKey] = useState(0);
  const [checkedPendingMeeting, setCheckedPendingMeeting] = useState<string | null>(null);
  const [activityCollapsed, setActivityCollapsedState] = useState(() => getActivityFeedCollapsed());

  function toggleActivityCollapsed() {
    setActivityCollapsedState((prev) => {
      const next = !prev;
      setActivityFeedCollapsed(next);
      return next;
    });
  }

  function setView(next: "board" | "list") {
    setViewState(next);
    setDefaultView(next);
  }

  const loadProjects = useCallback(() => {
    fetchProjects()
      .then((r) => setProjects(r.projects))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const loadTasks = useCallback((projectId: string) => {
    fetchProjectTasks(projectId)
      .then((r) => setTasks(r.tasks))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    if (selectedProject) {
      loadTasks(selectedProject);
    } else {
      setTasks(null);
    }
  }, [selectedProject, loadTasks]);

  // Surface a meeting the Meeting Coordinator proposed, unprompted, the first time this
  // project is viewed in this session - this is the "pop up and ask for approval" step;
  // it deliberately checks once per project (not on every meetingsRefreshKey bump) so
  // dismissing it doesn't make it reappear immediately.
  useEffect(() => {
    if (!selectedProject || checkedPendingMeeting === selectedProject) return;
    setCheckedPendingMeeting(selectedProject);
    fetchProjectMeetings(selectedProject)
      .then((r) => {
        const pending = r.meetings.find((m) => m.status === "pending_approval");
        if (pending) setReviewingMeeting(pending);
      })
      .catch(() => {});
  }, [selectedProject, checkedPendingMeeting]);

  // On first load with no ?project= in the URL, fall back to the saved default -
  // only once, so explicitly clearing the selection later doesn't keep snapping back.
  useEffect(() => {
    if (checkedDefaultProject) return;
    setCheckedDefaultProject(true);
    if (!selectedProject) {
      const def = getDefaultProject();
      if (def) {
        const params = new URLSearchParams(searchParams.toString());
        params.set("project", def);
        router.replace(`/products/task-manager?${params.toString()}`);
      }
    }
  }, [checkedDefaultProject, selectedProject, searchParams, router]);

  function handleSelectProject(projectId: string | null) {
    setDefaultProject(projectId);
    const params = new URLSearchParams(searchParams.toString());
    if (projectId) {
      params.set("project", projectId);
    } else {
      params.delete("project");
    }
    router.push(`/products/task-manager?${params.toString()}`);
  }

  const currentProject = projects?.find((p) => p.project_id === selectedProject);

  return (
    <main className="mx-auto max-w-[1800px] px-6 py-8">
      <header className="mb-7 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-text-primary">Task Manager</h1>
          <p className="text-[12.5px] text-text-muted">Track status, owner, deadline, blocker, and dependency for one project at a time.</p>
        </div>
        {projects && projects.length > 0 && (
          <ProjectSelector projects={projects} selected={selectedProject} onSelect={handleSelectProject} />
        )}
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
            Submit an SOW in Task Extractor
          </Link>
        </div>
      )}

      {projects && projects.length > 0 && !currentProject && (
        <div className="glass-panel rounded-2xl p-6 text-center">
          <p className="text-[13px] text-text-muted">Select a project above to see its tasks.</p>
        </div>
      )}

      {currentProject && (
        <div className={`grid grid-cols-1 gap-6 ${activityCollapsed ? "lg:grid-cols-[1fr_auto]" : "lg:grid-cols-[1fr_320px]"}`}>
          <div className="flex min-w-0 flex-col gap-6">
            <section>
              <h2 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                {currentProject.project_id}: overview
              </h2>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 2xl:grid-cols-6">
                <StatCard label="Total Tasks" value={currentProject.total_tasks} color="indigo" icon={<IconChecklist className="h-4.5 w-4.5" />} />
                <StatCard label="Open" value={currentProject.open_tasks} color="blue" icon={<IconClock className="h-4.5 w-4.5" />} />
                <StatCard label="In Progress" value={currentProject.in_progress_tasks} color="purple" icon={<IconFolder className="h-4.5 w-4.5" />} />
                <StatCard label="Completed" value={currentProject.completed_tasks} color="emerald" icon={<IconChecklist className="h-4.5 w-4.5" />} />
                <StatCard label="Blocked" value={currentProject.blocked_tasks} color="red" icon={<IconAlertTriangle className="h-4.5 w-4.5" />} />
                <StatCard label="High Priority" value={currentProject.high_priority_tasks} color="amber" icon={<IconFlame className="h-4.5 w-4.5" />} />
              </div>
            </section>

            <section>
              <div className="mb-2.5 flex items-center justify-between">
                <h2 className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Tasks</h2>
                <div className="flex items-center gap-2">
                  <ProjectSettings projectId={currentProject.project_id} />
                  <button
                    type="button"
                    onClick={() => setSchedulingMeeting(true)}
                    className="hover-tint inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary"
                  >
                    <IconCalendar className="h-3.5 w-3.5" />
                    Schedule Meeting
                  </button>
                  <a
                    href={projectExportUrl(currentProject.project_id)}
                    download
                    className="hover-tint inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary"
                  >
                    <IconDownload className="h-3.5 w-3.5" />
                    Export to Excel
                  </a>
                  <div className="flex gap-1 rounded-lg border border-line bg-surface-solid p-1">
                    <button
                      onClick={() => setView("board")}
                      className={`rounded-md px-3 py-1 text-[12.5px] font-medium transition ${
                        view === "board" ? "bg-gradient-to-br from-accent-from to-accent-to text-white" : "text-text-muted hover-tint"
                      }`}
                    >
                      Board
                    </button>
                    <button
                      onClick={() => setView("list")}
                      className={`rounded-md px-3 py-1 text-[12.5px] font-medium transition ${
                        view === "list" ? "bg-gradient-to-br from-accent-from to-accent-to text-white" : "text-text-muted hover-tint"
                      }`}
                    >
                      List
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDeletingProject(true)}
                    title="Delete this project"
                    className="hover-tint ml-1 inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 text-[12.5px] font-medium text-text-faint transition hover:border-danger/50 hover:text-danger"
                  >
                    <IconTrash className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {!tasks && <p className="text-[13px] text-text-faint">Loading…</p>}
              {tasks && tasks.length === 0 && <p className="text-[13px] text-text-faint">No tasks yet in this project.</p>}
              {tasks && tasks.length > 0 && (
                view === "board" ? <TaskBoard tasks={tasks} /> : <TaskTracker tasks={tasks} onTasksChange={setTasks} />
              )}
            </section>

            <section>
              <h2 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-text-faint">Meetings</h2>
              <MeetingsPanel
                projectId={currentProject.project_id}
                refreshKey={meetingsRefreshKey}
                onReview={(meeting) => setReviewingMeeting(meeting)}
              />
            </section>

            <section>
              <h2 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-text-faint">People</h2>
              <PeoplePanel projectId={currentProject.project_id} />
            </section>
          </div>

          <div className="lg:sticky lg:top-8 lg:self-start">
            <ActivityFeed projectId={selectedProject} collapsed={activityCollapsed} onToggleCollapsed={toggleActivityCollapsed} />
          </div>
        </div>
      )}

      {schedulingMeeting && currentProject && (
        <ScheduleMeetingModal
          projectId={currentProject.project_id}
          tasks={tasks ?? []}
          onClose={() => setSchedulingMeeting(false)}
        />
      )}

      {reviewingMeeting && currentProject && (
        <ScheduleMeetingModal
          projectId={currentProject.project_id}
          tasks={[]}
          pendingMeeting={reviewingMeeting}
          onClose={() => setReviewingMeeting(null)}
          onDecided={() => setMeetingsRefreshKey((k) => k + 1)}
        />
      )}

      {deletingProject && currentProject && (
        <DeleteProjectModal
          projectId={currentProject.project_id}
          taskCount={currentProject.total_tasks}
          onClose={() => setDeletingProject(false)}
          onDeleted={() => {
            if (getDefaultProject() === currentProject.project_id) setDefaultProject(null);
            router.push("/products/dashboard");
          }}
        />
      )}
    </main>
  );
}
