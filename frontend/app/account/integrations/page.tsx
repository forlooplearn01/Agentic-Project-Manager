import { IconChecklist, IconClock, IconPlug } from "@/components/icons";

const CONNECTORS = [
  {
    name: "Jira",
    description: "Sync tasks to a Jira project. Each project maps to its own Jira project key.",
    status: "Connected (shared credentials)",
    note: "Manage a project's Jira key from that project's page in Task Manager.",
  },
  {
    name: "Google Calendar",
    description: "Check owner availability and schedule follow-up meetings from meeting minutes.",
    status: "OAuth2 (single account)",
    note: "Configured via .env - see INTEGRATIONS.md for setup and how to switch to a Workspace-wide connection later.",
  },
];

export default function AccountIntegrationsPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <header className="mb-7">
        <h1 className="text-lg font-semibold tracking-tight text-text-primary">Integrations</h1>
        <p className="text-[12.5px] text-text-muted">Third-party connectors available to your projects.</p>
      </header>

      <div className="flex flex-col gap-3">
        {CONNECTORS.map((c) => (
          <div key={c.name} className="glass-panel flex items-start gap-3 rounded-xl p-4">
            <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-solid text-text-muted">
              <IconPlug className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-[13.5px] font-semibold text-text-primary">{c.name}</h3>
                <span className="inline-flex items-center gap-1 rounded-full bg-success-bg px-2 py-0.5 text-[11px] font-medium text-success">
                  <IconChecklist className="h-3 w-3" />
                  {c.status}
                </span>
              </div>
              <p className="mt-1 text-[12.5px] text-text-muted">{c.description}</p>
              <p className="mt-1.5 flex items-center gap-1.5 text-[11.5px] text-text-faint">
                <IconClock className="h-3 w-3 shrink-0" />
                {c.note}
              </p>
            </div>
          </div>
        ))}
      </div>

      <p className="mt-6 text-[12px] text-text-faint">
        A dedicated connect/disconnect flow per connector, instead of .env-based setup, is planned for a later phase.
      </p>
    </main>
  );
}
