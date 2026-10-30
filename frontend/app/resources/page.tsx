import Link from "next/link";
import type { ReactNode } from "react";
import { IconChecklist, IconClock, IconFileText, IconMessageCircle, IconPlug, IconTable } from "@/components/icons";

interface Step {
  icon: ReactNode;
  title: string;
  body: string;
  points?: string[];
  href: string;
  link: string;
}

const STEPS: Step[] = [
  {
    icon: <IconFileText className="h-4 w-4" />,
    title: "Get a project in",
    body: "Paste or upload an SOW or meeting minutes in Extractor. The assistant creates the tasks, owners and deadlines, and suggests risks and deliverables for you to review.",
    points: ["Run each new project under its own project id.", "Jira issues are created only for projects that have a Jira key. Meetings are only created after you approve them."],
    href: "/products/extractor",
    link: "Open Extractor",
  },
  {
    icon: <IconChecklist className="h-4 w-4" />,
    title: "Keep the plan here",
    body: "Upload the plan file once. After that it lives in the app: edit estimates, weeks and phases in place, add or remove tasks, and Export to Excel when someone needs a copy.",
    points: [
      "The first upload sets the baseline. The strip above the table shows planned hours against now.",
      "Change history lists what moved and when. It does not record who.",
      "Plan is the working table; Board shows the same tasks by status.",
    ],
    href: "/products/task-manager",
    link: "Open Workspace",
  },
  {
    icon: <IconClock className="h-4 w-4" />,
    title: "Send timesheets by folder",
    body: "There is no upload button. Copy a timesheet (.xlsx or .csv) into the project's folder and it is read within a few seconds.",
    points: [
      "Folder: timesheets/<project-id>/ inside the app's project folder. Hours > Timesheet shows the exact path.",
      "The file name must start with the project id, for example forloop-timesheet-week5.csv.",
      "Imported: hours are counted. Lines where the task was only a guess appear under Check these matches; pick another task to move them.",
      "Needs review: a person is not on the project's people list, or a row could not be read. Nothing is saved until you open Review and confirm.",
      "Already imported: the same file was seen before. Undo reverses any imported file.",
    ],
    href: "/products/hours?tab=timesheet",
    link: "Open Timesheet",
  },
  {
    icon: <IconClock className="h-4 w-4" />,
    title: "Read the hours status",
    body: "Hours > Overview compares the work done with the work due by now and with the budget. The status is On track, Watch or At risk, with a one-line reason.",
    points: [
      "Work done counts partial credit on tasks still open, and is capped at 90% until a task is completed.",
      "Work due by now is spread evenly across the planned weeks from kickoff.",
      "The forecast is hours logged plus hours still needed on open tasks. Tasks with no estimate are listed so they are not forgotten.",
    ],
    href: "/products/hours",
    link: "Open Hours",
  },
  {
    icon: <IconTable className="h-4 w-4" />,
    title: "Registers",
    body: "RAID log, RACI matrix and Deliverables, one tab each. Items the assistant suggests wait for your review before they count.",
    href: "/products/registers",
    link: "Open Registers",
  },
  {
    icon: <IconMessageCircle className="h-4 w-4" />,
    title: "Ask AI Chat",
    body: "Chat reads the same data as the screens. Use the project id or name in the question.",
    points: [
      "How many hours are left on forloop?",
      "Who is available for engineering work next week?",
      "What changed in the plan since the baseline?",
      "Who has the heaviest workload on this project?",
      "What did the SOW say about acceptance testing?",
    ],
    href: "/chat",
    link: "Open AI Chat",
  },
  {
    icon: <IconPlug className="h-4 w-4" />,
    title: "Jira and Google Calendar",
    body: "Jira uses one shared account with a Jira key per project. Google Calendar uses one connected account. Meetings are only created after you approve them. Set both up under Integrations.",
    href: "/account/integrations",
    link: "Open Integrations",
  },
];

export default function GuidePage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <header className="mb-7">
        <h1 className="text-lg font-semibold tracking-tight text-text-primary">Guide</h1>
        <p className="text-[12.5px] text-text-muted">How the app fits together, in the order a project usually goes.</p>
      </header>

      <div className="flex flex-col gap-3">
        {STEPS.map((s) => (
          <section key={s.title} className="glass-panel flex items-start gap-3 rounded-xl p-4">
            <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-solid text-text-muted">{s.icon}</div>
            <div className="min-w-0">
              <h2 className="text-[13.5px] font-semibold text-text-primary">{s.title}</h2>
              <p className="mt-1 text-[12.5px] text-text-muted">{s.body}</p>
              {s.points && (
                <ul className="mt-2 flex list-disc flex-col gap-1 pl-4 text-[12.5px] text-text-muted">
                  {s.points.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
              <Link href={s.href} className="mt-2 inline-block text-[12.5px] text-text-muted underline hover:text-text-primary">
                {s.link}
              </Link>
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
