"use client";

import { useEffect, useState } from "react";
import { fetchHoursSettings, fetchProjects, saveHoursSettings } from "@/lib/api";
import { formatDateWith } from "@/lib/format";
import {
  DISPLAY_NAME_MAX,
  getActivityFeedCollapsed,
  getDateFormat,
  getDefaultProject,
  getDefaultView,
  getDensity,
  getDisplayName,
  getShowChatSources,
  getTheme,
  setActivityFeedCollapsed,
  setDateFormat,
  setDefaultProject,
  setDefaultView,
  setDensity,
  setDisplayName,
  setShowChatSources,
  setTheme,
  type DateFormat,
  type Density,
  type Theme,
} from "@/lib/preferences";
import type { ProjectRollup } from "@/lib/types";
import { IconSettings } from "@/components/icons";
import { WEEK_START_OPTIONS, errorText } from "@/components/hours/shared";

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

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="mt-2 text-[11px] font-semibold uppercase tracking-wide text-text-faint">{title}</h2>
      {children}
    </section>
  );
}

function PillToggle<T extends string | number>({ value, options, onChange, disabled }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; disabled?: boolean }) {
  return (
    <div className={`flex gap-1 rounded-lg border border-line bg-surface-solid p-1 ${disabled ? "pointer-events-none opacity-50" : ""}`}>
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          aria-pressed={value === opt.value}
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

const DATE_FORMAT_OPTIONS: { value: DateFormat; label: string }[] = [
  { value: "auto", label: "Browser default" },
  { value: "dmy", label: "DD/MM/YYYY" },
  { value: "mdy", label: "MM/DD/YYYY" },
  { value: "ymd", label: "YYYY-MM-DD" },
  { value: "text", label: "29 Oct 2026" },
];

export default function AccountSettingsPage() {
  const [projects, setProjects] = useState<ProjectRollup[] | null>(null);
  const [theme, setThemeState] = useState<Theme>("dark");
  const [defaultProject, setDefaultProjectState] = useState<string | null>(null);
  const [defaultView, setDefaultViewState] = useState<"plan" | "board">("plan");
  const [activityCollapsed, setActivityCollapsedState] = useState(false);
  const [density, setDensityState] = useState<Density>("comfortable");
  const [dateFormat, setDateFormatState] = useState<DateFormat>("auto");
  const [displayName, setDisplayNameState] = useState("");
  const [showSources, setShowSourcesState] = useState(false);
  // Week start belongs to a project (hours are filed by week), so this row edits the chosen project's own setting; until one is
  // chosen it follows the default project.
  const [weekProjectChoice, setWeekProjectChoice] = useState<string | null>(null);
  const weekProject = weekProjectChoice ?? defaultProject;
  const [weekStart, setWeekStart] = useState<number | null>(null);
  const [weekStartMessage, setWeekStartMessage] = useState<string | null>(null);

  useEffect(() => {
    fetchProjects()
      .then((r) => setProjects(r.projects))
      .catch(() => {});
    setThemeState(getTheme());
    setDefaultProjectState(getDefaultProject());
    setDefaultViewState(getDefaultView());
    setActivityCollapsedState(getActivityFeedCollapsed());
    setDensityState(getDensity());
    setDateFormatState(getDateFormat());
    setDisplayNameState(getDisplayName());
    setShowSourcesState(getShowChatSources());
  }, []);

  useEffect(() => {
    setWeekStart(null);
    setWeekStartMessage(null);
    if (!weekProject) return;
    let cancelled = false;
    fetchHoursSettings(weekProject)
      .then((s) => !cancelled && setWeekStart(s.week_start_day))
      .catch(() => !cancelled && setWeekStart(null));
    return () => {
      cancelled = true;
    };
  }, [weekProject]);

  function handleDefaultProjectChange(value: string) {
    const next = value || null;
    setDefaultProjectState(next);
    setDefaultProject(next);
  }

  async function handleWeekStartChange(value: number) {
    if (!weekProject) return;
    setWeekStartMessage(null);
    try {
      const s = await saveHoursSettings(weekProject, { week_start_day: value });
      setWeekStart(s.week_start_day);
      setWeekStartMessage("Saved. Hours already logged moved into the new weeks.");
    } catch (e) {
      setWeekStartMessage(errorText(e));
    }
  }

  const today = new Date();

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <header className="mb-7">
        <h1 className="text-lg font-semibold tracking-tight text-text-primary">Settings</h1>
        <p className="text-[12.5px] text-text-muted">App preferences, remembered in this browser.</p>
      </header>

      <div className="flex flex-col gap-3">
        <Section title="Appearance">
          <SettingRow label="Theme" description="Light or dark, applied across the whole app.">
            <PillToggle
              value={theme}
              options={[
                { value: "dark", label: "Dark" },
                { value: "light", label: "Light" },
              ]}
              onChange={(v: Theme) => {
                setThemeState(v);
                setTheme(v);
              }}
            />
          </SettingRow>

          <SettingRow label="Display name" description="Shown at the top of the sidebar instead of the app name, for example your company or a client's name.">
            <input
              value={displayName}
              maxLength={DISPLAY_NAME_MAX}
              onChange={(e) => {
                setDisplayNameState(e.target.value);
                setDisplayName(e.target.value);
              }}
              placeholder="Agentic Project Manager"
              aria-label="Display name"
              className="w-56 rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[13px] text-text-primary outline-none placeholder:text-text-faint focus:border-accent-to"
            />
          </SettingRow>

          <SettingRow label="Table rows" description="Comfortable or Compact: compact tightens the rows in every table so more fit on screen.">
            <PillToggle
              value={density}
              options={[
                { value: "comfortable", label: "Comfortable" },
                { value: "compact", label: "Compact" },
              ]}
              onChange={(v: Density) => {
                setDensityState(v);
                setDensity(v);
              }}
            />
          </SettingRow>

          <SettingRow label="Date format" description={`How dates are shown, for example ${formatDateWith(dateFormat, today)}. Calendar pickers always follow your computer's language setting.`}>
            <select
              value={dateFormat}
              onChange={(e) => {
                const v = e.target.value as DateFormat;
                setDateFormatState(v);
                setDateFormat(v);
              }}
              aria-label="Date format"
              className="min-w-[200px] rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[13px] font-medium text-text-primary outline-none focus:border-accent-to"
            >
              {DATE_FORMAT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.value === "auto" || o.value === "text" ? (o.value === "text" ? `Day Month Year (${formatDateWith("text", today)})` : o.label) : `${o.label} (${formatDateWith(o.value, today)})`}
                </option>
              ))}
            </select>
          </SettingRow>
        </Section>

        <Section title="Workspace">
          <SettingRow label="Default project" description="Which project Workspace, Registers, Hours and Extractor open to.">
            <select
              value={defaultProject ?? ""}
              onChange={(e) => handleDefaultProjectChange(e.target.value)}
              aria-label="Default project"
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

          <SettingRow label="Default task view" description="Plan or Board, when Workspace first loads a project.">
            <PillToggle
              value={defaultView}
              options={[
                { value: "plan", label: "Plan" },
                { value: "board", label: "Board" },
              ]}
              onChange={(v: "plan" | "board") => {
                setDefaultViewState(v);
                setDefaultView(v);
              }}
            />
          </SettingRow>

          <SettingRow label="Agent activity panel" description="Show the activity panel on the Dashboard and Workspace, or keep it collapsed.">
            <PillToggle
              value={activityCollapsed ? "collapsed" : "shown"}
              options={[
                { value: "shown", label: "Shown" },
                { value: "collapsed", label: "Collapsed" },
              ]}
              onChange={(v: string) => {
                setActivityCollapsedState(v === "collapsed");
                setActivityFeedCollapsed(v === "collapsed");
              }}
            />
          </SettingRow>
        </Section>

        <Section title="Chat">
          <SettingRow label="Show how the assistant got each answer" description="Under each answer, a line naming the data it looked at, such as Hours or the SOW. Shown for answers received in this session.">
            <PillToggle
              value={showSources ? "on" : "off"}
              options={[
                { value: "off", label: "Off" },
                { value: "on", label: "On" },
              ]}
              onChange={(v: string) => {
                setShowSourcesState(v === "on");
                setShowChatSources(v === "on");
              }}
            />
          </SettingRow>
        </Section>

        <Section title="Hours">
          <SettingRow
            label="Week starts on"
            description="Each project keeps its own week start, because hours are filed by week. Change it and the hours already logged move into the new weeks; none are lost."
          >
            <div className="flex flex-col items-end gap-2">
              <select
                value={weekProject ?? ""}
                onChange={(e) => setWeekProjectChoice(e.target.value || null)}
                aria-label="Project for the week start"
                className="min-w-[200px] rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[13px] font-medium text-text-primary outline-none focus:border-accent-to"
              >
                <option value="">Choose a project</option>
                {(projects ?? []).map((p) => (
                  <option key={p.project_id} value={p.project_id}>
                    {p.project_id}
                  </option>
                ))}
              </select>
              <PillToggle value={weekStart ?? -1} disabled={weekStart === null} options={WEEK_START_OPTIONS} onChange={handleWeekStartChange} />
              {weekStartMessage && <span className="max-w-[260px] text-right text-[11.5px] text-text-muted">{weekStartMessage}</span>}
            </div>
          </SettingRow>
        </Section>
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
