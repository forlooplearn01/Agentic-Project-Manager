// Per-browser app preferences - no user accounts exist yet, so these live in
// localStorage rather than the database. Every read/write is wrapped since
// localStorage can throw (private browsing, disabled site data).

const KEYS = {
  defaultProject: "pref-default-project",
  defaultView: "pref-default-view",
  theme: "pref-theme",
  activityFeedCollapsed: "pref-activity-feed-collapsed",
  dateFormat: "pref-date-format",
  density: "pref-density",
  chatSources: "pref-chat-sources",
  displayName: "pref-display-name",
} as const;

export type Theme = "dark" | "light";

// The inline script in layout.tsx sets data-theme before first paint using this
// same fallback order (stored choice, then system preference) - keep them in sync.
export function getTheme(): Theme {
  try {
    const stored = localStorage.getItem(KEYS.theme);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Storage unavailable - fall through to system preference.
  }
  try {
    if (window.matchMedia("(prefers-color-scheme: light)").matches) return "light";
  } catch {
    // matchMedia unavailable - fall through to the dark default.
  }
  return "dark";
}

export function setTheme(theme: Theme) {
  try {
    localStorage.setItem(KEYS.theme, theme);
  } catch {
    // Storage unavailable - the toggle still applies for this page view below.
  }
  try {
    document.documentElement.setAttribute("data-theme", theme);
  } catch {
    // Not in a browser - nothing to apply.
  }
}

export function getDefaultProject(): string | null {
  try {
    return localStorage.getItem(KEYS.defaultProject);
  } catch {
    return null;
  }
}

export function setDefaultProject(projectId: string | null) {
  try {
    if (projectId) localStorage.setItem(KEYS.defaultProject, projectId);
    else localStorage.removeItem(KEYS.defaultProject);
  } catch {
    // Storage unavailable - preference just won't persist this session.
  }
}

// Plan is the default. The old "List" choice became the Plan view, so a stored "list" reads as Plan.
export function getDefaultView(): "plan" | "board" {
  try {
    return localStorage.getItem(KEYS.defaultView) === "board" ? "board" : "plan";
  } catch {
    return "plan";
  }
}

export function setDefaultView(view: "plan" | "board") {
  try {
    localStorage.setItem(KEYS.defaultView, view);
  } catch {
    // Storage unavailable - preference just won't persist this session.
  }
}

export function getActivityFeedCollapsed(): boolean {
  try {
    return localStorage.getItem(KEYS.activityFeedCollapsed) === "1";
  } catch {
    return false;
  }
}

export function setActivityFeedCollapsed(collapsed: boolean) {
  try {
    localStorage.setItem(KEYS.activityFeedCollapsed, collapsed ? "1" : "0");
  } catch {
    // Storage unavailable - preference just won't persist this session.
  }
}

// ---------------------------------------------------------------------------
// Date format, table density, chat sources, display name
// ---------------------------------------------------------------------------

export type DateFormat = "auto" | "dmy" | "mdy" | "ymd" | "text";
const DATE_FORMATS: DateFormat[] = ["auto", "dmy", "mdy", "ymd", "text"];

// "auto" = whatever the browser's language setting says. Dates picked from a calendar (native date fields) always follow the
// computer's own setting; this changes how dates are SHOWN everywhere else.
export function getDateFormat(): DateFormat {
  try {
    const v = localStorage.getItem(KEYS.dateFormat) as DateFormat | null;
    return v && DATE_FORMATS.includes(v) ? v : "auto";
  } catch {
    return "auto";
  }
}

export function setDateFormat(format: DateFormat) {
  try {
    localStorage.setItem(KEYS.dateFormat, format);
  } catch {
    // Storage unavailable - preference just won't persist this session.
  }
}

export type Density = "comfortable" | "compact";

export function getDensity(): Density {
  try {
    return localStorage.getItem(KEYS.density) === "compact" ? "compact" : "comfortable";
  } catch {
    return "comfortable";
  }
}

// layout.tsx's inline script applies the stored choice before first paint; this applies a change made on the Settings page.
export function setDensity(density: Density) {
  try {
    localStorage.setItem(KEYS.density, density);
  } catch {
    // Storage unavailable - the change still applies for this page view below.
  }
  try {
    if (density === "compact") document.documentElement.setAttribute("data-density", "compact");
    else document.documentElement.removeAttribute("data-density");
  } catch {
    // Not in a browser - nothing to apply.
  }
}

export function getShowChatSources(): boolean {
  try {
    return localStorage.getItem(KEYS.chatSources) === "1";
  } catch {
    return false;
  }
}

export function setShowChatSources(show: boolean) {
  try {
    localStorage.setItem(KEYS.chatSources, show ? "1" : "0");
  } catch {
    // Storage unavailable - preference just won't persist this session.
  }
}

export const PREFS_CHANGED_EVENT = "prefs-changed";
export const DISPLAY_NAME_MAX = 40;

export function getDisplayName(): string {
  try {
    return localStorage.getItem(KEYS.displayName) ?? "";
  } catch {
    return "";
  }
}

export function setDisplayName(name: string) {
  const clean = name.trim().slice(0, DISPLAY_NAME_MAX);
  try {
    if (clean) localStorage.setItem(KEYS.displayName, clean);
    else localStorage.removeItem(KEYS.displayName);
  } catch {
    // Storage unavailable - preference just won't persist this session.
  }
  try {
    window.dispatchEvent(new Event(PREFS_CHANGED_EVENT)); // the sidebar listens
  } catch {
    // Not in a browser - nothing to notify.
  }
}
