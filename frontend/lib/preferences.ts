// Per-browser app preferences - no user accounts exist yet, so these live in
// localStorage rather than the database. Every read/write is wrapped since
// localStorage can throw (private browsing, disabled site data).

const KEYS = {
  defaultProject: "pref-default-project",
  defaultView: "pref-default-view",
  theme: "pref-theme",
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

export function getDefaultView(): "board" | "list" {
  try {
    return localStorage.getItem(KEYS.defaultView) === "list" ? "list" : "board";
  } catch {
    return "board";
  }
}

export function setDefaultView(view: "board" | "list") {
  try {
    localStorage.setItem(KEYS.defaultView, view);
  } catch {
    // Storage unavailable - preference just won't persist this session.
  }
}
