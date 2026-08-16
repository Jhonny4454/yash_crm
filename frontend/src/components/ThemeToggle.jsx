import { useCallback, useEffect, useState } from "react";
import "../styles/Theme.css";

/**
 * Light / dark, for the staff panel.
 *
 * The choice lives on <html data-theme>, not in React state alone: the CSS
 * has to see it before the first paint, and a small script in index.html
 * applies the stored value while the bundle is still downloading. Without
 * that, an operator who chose dark gets a full-white flash on every page
 * load, which is worse than not having the feature.
 *
 * Stored per browser rather than per account, deliberately - it is a property
 * of the screen somebody is sitting at (a bright counter, a dim back office),
 * not of who is signed in, and it must work before anybody signs in.
 */
export const THEME_KEY = "unicrm.theme";

/** The theme to start in: what was chosen, else what the OS asks for. */
export function preferredTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    // Private mode, or storage disabled. Fall through to the OS preference.
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark" : "light";
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Not being able to remember the choice is not a reason to refuse it.
  }
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState(preferredTheme);

  useEffect(() => { applyTheme(theme); }, [theme]);

  /* Follow the operating system until somebody makes a choice of their own -
     after that, theirs wins. `localStorage` is the record of having chosen. */
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) return undefined;
    const onChange = (event) => {
      let chosen = null;
      try { chosen = localStorage.getItem(THEME_KEY); } catch { chosen = null; }
      if (!chosen) setTheme(event.matches ? "dark" : "light");
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const toggle = useCallback(
    () => setTheme((current) => (current === "dark" ? "light" : "dark")), []);

  const dark = theme === "dark";
  return (
    <button type="button" className="theme-toggle" onClick={toggle}
            aria-pressed={dark}
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            title={dark ? "Switch to light mode" : "Switch to dark mode"}>
      <i className={`fas fa-${dark ? "sun" : "moon"}`} aria-hidden="true" />
    </button>
  );
}
