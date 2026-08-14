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

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode><App /></React.StrictMode>
);

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
