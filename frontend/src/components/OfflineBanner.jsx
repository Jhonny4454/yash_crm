import { useEffect, useState } from "react";
import { get } from "../api/client";
import { useAuth } from "../context/AuthContext";
import "../styles/OfflineBanner.css";

/**
 * A standing bar for "the API is not answering".
 *
 * Before this, an unreachable backend was indistinguishable from a broken
 * app: panels showed generic errors, and the session was thrown away so the
 * operator landed on a login screen that could not log them in either. Saying
 * it once, at the top, is both more honest and less alarming.
 *
 * It polls a cheap endpoint and clears itself the moment the server answers,
 * so nobody has to guess when to reload.
 */
export default function OfflineBanner() {
  const { offline, refreshProfile } = useAuth();
  const [checking, setChecking] = useState(false);
  const [back, setBack] = useState(false);

  useEffect(() => {
    if (!offline) return undefined;

    let cancelled = false;
    const timer = setInterval(() => {
      get("/branding", undefined, { retry: false })
        .then(() => {
          if (cancelled) return;
          setBack(true);
          clearInterval(timer);
        })
        .catch(() => {});
    }, 5000);

    return () => { cancelled = true; clearInterval(timer); };
  }, [offline]);

  // The bar is fixed to the top of the viewport so the sidebar cannot cover
  // it. That takes it out of the flow, so the page needs matching headroom -
  // added and removed here rather than left permanently in the layout CSS.
  useEffect(() => {
    document.body.classList.toggle("has-offline-banner", offline);
    return () => document.body.classList.remove("has-offline-banner");
  }, [offline]);

  if (!offline) return null;

  async function retry() {
    setChecking(true);
    try {
      await refreshProfile();
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className={`offline-banner${back ? " is-back" : ""}`} role="status">
      {back ? (
        <>
          <strong>The server is back.</strong>
          <span>Reload to pick up where you left off.</span>
          <button type="button" onClick={() => window.location.reload()}>
            Reload
          </button>
        </>
      ) : (
        <>
          <strong>Cannot reach the server.</strong>
          <span>
            You are still signed in. If you are running this locally, check
            that Flask is started and listening on port 5000.
          </span>
          <button type="button" onClick={retry} disabled={checking}>
            {checking ? "Checking…" : "Try again"}
          </button>
        </>
      )}
    </div>
  );
}
