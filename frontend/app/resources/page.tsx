import { IconBookOpen, IconFileText, IconPlug } from "@/components/icons";

const RESOURCES = [
  {
    icon: <IconFileText className="h-4 w-4" />,
    title: "Excel export",
    body: "Works out of the box - no setup. Export any project's tasks to .xlsx from that project's page in Workspace.",
  },
  {
    icon: <IconPlug className="h-4 w-4" />,
    title: "Jira integration",
    body: "One shared Jira account, per-project Jira key mapping. See the Integrations page under Account.",
  },
  {
    icon: <IconPlug className="h-4 w-4" />,
    title: "Google Calendar integration",
    body: "OAuth2 today (one connected account), can switch to a Workspace-wide connection later via one config change, no code changes.",
  },
];

export default function ResourcesPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <header className="mb-7">
        <h1 className="text-lg font-semibold tracking-tight text-text-primary">Resources</h1>
        <p className="text-[12.5px] text-text-muted">Reference material for how this app's integrations work.</p>
      </header>

      <div className="flex flex-col gap-3">
        {RESOURCES.map((r) => (
          <div key={r.title} className="glass-panel flex items-start gap-3 rounded-xl p-4">
            <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-solid text-text-muted">
              {r.icon}
            </div>
            <div>
              <h3 className="text-[13.5px] font-semibold text-text-primary">{r.title}</h3>
              <p className="mt-1 text-[12.5px] text-text-muted">{r.body}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="glass-panel mt-4 flex items-center gap-3 rounded-xl p-4 opacity-70">
        <IconBookOpen className="h-4 w-4 shrink-0 text-text-faint" />
        <p className="text-[12.5px] text-text-muted">Sample SOW/MoM templates and a changelog are planned for a later phase.</p>
      </div>
    </main>
  );
}
