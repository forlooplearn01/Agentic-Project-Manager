"use client";

import { useEffect, useState } from "react";
import { fetchProjects } from "@/lib/api";
import {
  getDefaultProject,
  getDefaultView,
  getTheme,
  setDefaultProject,
  setDefaultView,
  setTheme,
  type Theme,
} from "@/lib/preferences";
import type { ProjectRollup } from "@/lib/types";
import { IconSettings } from "@/components/icons";

function SettingRow({ label, description, children }: { label: string; description: string; children: React.ReactNode }) {
  return (
    <div className="glass-panel flex items-center justify-between gap-4 rounded-xl p-4">
      <div>
        <h3 className="text-[13.5px] font-semibold text-text-primary">{label}</h3>
        <p className="mt-0.5 text-[12.5px] text-text-muted">{description}</p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function PillToggle<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="flex gap-1 rounded-lg border border-line bg-surface-solid p-1">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`rounded-md px-3 py-1 text-[12.5px] font-medium transition ${
            value === opt.value ? "bg-gradient-to-br from-accent-from to-accent-to text-white" : "text-text-muted hover-tint"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export default function AccountSettingsPage() {
  const [projects, setProjects] = useState<ProjectRollup[] | null>(null);
  const [theme, setThemeState] = useState<Theme>("dark");
  const [defaultProject, setDefaultProjectState] = useState<string | null>(null);
  const [defaultView, setDefaultViewState] = useState<"board" | "list">("board");

  useEffect(() => {
    fetchProjects()
      .then((r) => setProjects(r.projects))
      .catch(() => {});
    setThemeState(getTheme());
    setDefaultProjectState(getDefaultProject());
    setDefaultViewState(getDefaultView());
  }, []);

  function handleThemeChange(value: Theme) {
    setThemeState(value);
    setTheme(value);
  }

  function handleDefaultProjectChange(value: string) {
    const next = value || null;
    setDefaultProjectState(next);
    setDefaultProject(next);
  }

  function handleDefaultViewChange(value: "board" | "list") {
    setDefaultViewState(value);
    setDefaultView(value);
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <header className="mb-7">
        <h1 className="text-lg font-semibold tracking-tight text-text-primary">Settings</h1>
        <p className="text-[12.5px] text-text-muted">App preferences, remembered in this browser.</p>
      </header>

      <div className="flex flex-col gap-3">
        <SettingRow label="Theme" description="Light or dark, applied across the whole app.">
          <PillToggle
            value={theme}
            options={[
              { value: "dark", label: "Dark" },
              { value: "light", label: "Light" },
            ]}
            onChange={handleThemeChange}
          />
        </SettingRow>

        <SettingRow label="Default project" description="Which project Task Manager and Task Extractor open to.">
          <select
            value={defaultProject ?? ""}
            onChange={(e) => handleDefaultProjectChange(e.target.value)}
            className="min-w-[200px] rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[13px] font-medium text-text-primary outline-none focus:border-accent-to"
          >
            <option value="">None</option>
            {(projects ?? []).map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.project_id}
              </option>
            ))}
          </select>
        </SettingRow>

        <SettingRow label="Default task view" description="Board or List, when Task Manager first loads a project.">
          <PillToggle
            value={defaultView}
            options={[
              { value: "board", label: "Board" },
              { value: "list", label: "List" },
            ]}
            onChange={handleDefaultViewChange}
          />
        </SettingRow>
      </div>

      <div className="glass-panel mt-4 flex items-center gap-3 rounded-xl p-4 opacity-70">
        <IconSettings className="h-4 w-4 shrink-0 text-text-faint" />
        <p className="text-[12.5px] text-text-muted">
          More preferences get added here as they come up - tell us what you want configurable.
        </p>
      </div>
    </main>
  );
}
