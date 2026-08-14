import { useEffect, useState } from "react";
import { onInFlightChange } from "../api/client";
import "../styles/RouteProgress.css";

/** Below this, showing anything at all reads as a flicker rather than feedback. */
const SHOW_AFTER_MS = 200;

/** Once shown, stay up at least this long - a 30ms flash is worse than none. */
const MIN_VISIBLE_MS = 320;

/**
 * A single thin bar across the top of the window while the API is busy.
 *
 * The two timings are the entire point. Without SHOW_AFTER_MS every cached
 * 20ms response strobes the bar; without MIN_VISIBLE_MS a request that
 * finishes just after the bar appears blinks it out again. Together they mean
 * the bar only shows for work slow enough to be worth reporting, and when it
 * does show it is legible.
 */
export default function RouteProgress() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let showTimer = null;
    let hideTimer = null;
    let shownAt = 0;

    const unsubscribe = onInFlightChange((count) => {
      if (count > 0) {
        if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
        if (showTimer || shownAt) return;
        showTimer = setTimeout(() => {
          showTimer = null;
          shownAt = Date.now();
          setVisible(true);
        }, SHOW_AFTER_MS);
        return;
      }

      // Everything finished.
      if (showTimer) { clearTimeout(showTimer); showTimer = null; }
      if (!shownAt) return;
      const remaining = Math.max(0, MIN_VISIBLE_MS - (Date.now() - shownAt));
      hideTimer = setTimeout(() => {
        hideTimer = null;
        shownAt = 0;
        setVisible(false);
      }, remaining);
    });

    return () => {
      unsubscribe();
      if (showTimer) clearTimeout(showTimer);
      if (hideTimer) clearTimeout(hideTimer);
    };
  }, []);

  if (!visible) return null;
  return <div className="route-progress" role="presentation"><span /></div>;
}
