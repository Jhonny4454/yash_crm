// First, because it is the token sheet: --brand, --muted, --line, --surface,
// --ink and friends are declared here and used by Pages.css, Panels.css,
// Shared.css and Boxes.css. It reached the bundle by accident until now -
// through components/AppShell.jsx - and the customer portal no longer renders
// that component, so an unreferenced module would have taken every one of
// those variables out of the build with it.
import "./styles/AppShell.css";

import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import "./styles/Pages.css";
import "./styles/Panels.css";
import "./styles/Shared.css";
import "./styles/Surfaces.css";
// Last, deliberately: Boxes.css is the single place that decides how a
// panel is bounded and guarantees nothing escapes one, so it has to win
// against the per-screen stylesheets above it.
import "./styles/Boxes.css";
// Two shells, two stylesheets, both loaded after everything else on purpose.
// Sidebar.css owns the collapsed rail and its fly-out menus, and has to beat
// the older copies of those rules still sitting in index.css and Dashboard.css.
// Portal.css owns the customer portal, and is scoped under .portal-shell so it
// cannot leak into the staff screens.
import "./styles/Sidebar.css";
import "./styles/AdminResponsive.css";
import "./styles/Portal.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode><App /></React.StrictMode>
);

/* A focused `<input type="number">` changes its VALUE when the mouse wheel
 * scrolls over it. Somebody reviewing a long payment form, scrolling with the
 * pointer resting on the amount they just typed, silently rebills the
 * customer - and nothing on screen says it happened.
 *
 * Every amount in this application is a MoneyInput (a text field), so this
 * should never fire. It is here for anything added later that forgets, and
 * for browser-injected controls we do not own. Blur rather than
 * preventDefault: the listener has to be passive to avoid holding up scroll,
 * and a passive listener is not allowed to cancel the event.
 */
if (typeof document !== "undefined") {
  document.addEventListener("wheel", (event) => {
    const el = document.activeElement;
    if (el && el.type === "number" && el === event.target) el.blur();
  }, { passive: true });
}

// Font Awesome is a CDN stylesheet, so it may never arrive. document.fonts
// .check() is no use here - it answers "is this font usable", which is true
// even when the browser has silently fallen back - so measure instead: a real
// glyph has width, a missing one collapses to nothing. Until that measurement
// passes, the icon buttons show their short text labels rather than five
// identical blank squares.
function flagIconFont() {
  const probe = document.createElement("i");
  probe.className = "fas fa-file-pdf";
  probe.style.cssText = "position:absolute;visibility:hidden;font-size:24px";
  document.body.appendChild(probe);
  const ready = probe.offsetWidth > 0;
  probe.remove();
  if (ready) document.documentElement.classList.add("fontawesome-ready");
  return ready;
}

if (typeof document !== "undefined") {
  // Try a few times: the stylesheet may still be in flight on first paint.
  let attempts = 0;
  const tick = () => {
    if (flagIconFont() || (attempts += 1) > 10) return;
    setTimeout(tick, 300);
  };
  window.addEventListener("load", tick);
  setTimeout(tick, 100);
}
