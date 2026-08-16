export type ThemePreference = "auto" | "light" | "dark";

const STORAGE_KEY = "kgdistiller-theme-v1";

export function loadTheme(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "auto";
  } catch {
    return "auto";
  }
}

export function applyTheme(preference: ThemePreference): void {
  if (preference === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = preference;
  try {
    if (preference === "auto") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    // A disabled local store changes persistence only, never the visible theme.
  }
}
