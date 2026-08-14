/**
 * session/idle.js
 * ===============
 *
 * Signs the operator out after a period with no activity on the page.
 *
 * Deliberately framework-free: the rules here are the security-relevant part
 * and are easier to reason about (and to test) as plain functions than as
 * something tangled up in a component's render cycle.
 *
 * Three decisions worth stating, because each fixes a way naive idle timers
 * get this wrong:
 *
 * 1. Time is measured against `Date.now()`, not by trusting a timer to fire on
 *    schedule. A laptop lid closed for an hour does not run timers - a plain
 *    `setTimeout(logout, TWO_MINUTES)` fires two minutes after the machine
 *    wakes, leaving the session live across the whole sleep. Comparing wall
 *    clock stamps means waking up already expired, which is the point.
 *
 * 2. The last-activity stamp lives in localStorage, so every open tab shares
 *    one clock. Per-tab timers log you out of the dashboard you left open
 *    while you were working in the customer tab next to it.
 *
 * 3. Only genuinely human events count. Two of these bit in testing:
 *    a pointermove is ignored unless the coordinates actually changed, because
 *    browsers emit one when content shifts under a stationary cursor; and
 *    `focus` is bound WITHOUT capture, so it means "the window came back to
 *    the foreground" and not "something called .focus()". With capture on, the
 *    warning dialog's own autofocused button fired a focus event the instant
 *    it appeared, reset the clock, and dismissed itself - the countdown could
 *    never reach zero.
 */

/** How long a session survives with no activity. */
export const IDLE_MS = 2 * 60 * 1000;

/** How long the "you are about to be signed out" warning is shown for. */
export const WARN_MS = 20 * 1000;

/** Shared across tabs, so activity anywhere keeps every tab alive. */
export const ACTIVITY_KEY = "unicrm.lastActivity";

/** Read by the login screen to explain why the user landed back there. */
export const REASON_KEY = "unicrm.signout_reason";

// Writing to localStorage on every mousemove would be hundreds of writes a
// second. Once a second is precise enough for a two-minute budget.
const WRITE_EVERY_MS = 1000;

/** Bound with capture, so they are seen wherever in the page they happen. */
const ACTIVITY_EVENTS = [
  "pointerdown",
  "pointermove",
  "keydown",
  "wheel",
  "scroll",
  "touchstart",
];

/**
 * Bound WITHOUT capture. See note 3: on window, only the non-capturing phase
 * is reached by the window's own focus event, so this means "the user switched
 * back to this tab" rather than "some element was focused programmatically".
 */
const WINDOW_EVENTS = ["focus"];

export function readLastActivity() {
  const raw = Number(localStorage.getItem(ACTIVITY_KEY));
  // A missing or corrupt stamp means "we do not know when they were last
  // here". Treating that as `0` would sign a freshly-loaded page out
  // instantly, so treat it as now and let the next tick do the real work.
  return Number.isFinite(raw) && raw > 0 ? raw : Date.now();
}

export function markActive(now = Date.now()) {
  localStorage.setItem(ACTIVITY_KEY, String(now));
}

export function clearActivity() {
  localStorage.removeItem(ACTIVITY_KEY);
}

export function signOutReason() {
  try {
    const reason = sessionStorage.getItem(REASON_KEY);
    if (reason) sessionStorage.removeItem(REASON_KEY);
    return reason;
  } catch {
    return null;
  }
}

function setSignOutReason(reason) {
  try {
    sessionStorage.setItem(REASON_KEY, reason);
  } catch {
    // Private-mode Safari throws on sessionStorage writes. The sign-out still
    // has to happen; only the explanation is lost.
  }
}

/**
 * Start watching. Returns a function that stops watching and unbinds
 * everything - call it when the user signs out or the provider unmounts.
 *
 * @param {object}   options
 * @param {Function} options.onWarn    called with milliseconds remaining, or
 *                                     null once the warning is cancelled
 * @param {Function} options.onExpire  called once, when the budget runs out
 * @param {number}  [options.idleMs]
 * @param {number}  [options.warnMs]
 */
export function watchIdle({ onWarn, onExpire, idleMs = IDLE_MS, warnMs = WARN_MS }) {
  let lastWrite = 0;
  let lastX = null;
  let lastY = null;
  let warning = false;
  let stopped = false;

  markActive();

  const record = (event) => {
    if (stopped) return;

    // See note 3 at the top of the file: a pointermove with identical
    // coordinates is the page moving, not the person.
    if (event?.type === "pointermove") {
      if (event.clientX === lastX && event.clientY === lastY) return;
      lastX = event.clientX;
      lastY = event.clientY;
    }

    const now = Date.now();

    // Any real activity cancels a live warning immediately, without waiting
    // for the next tick - the dialog vanishing the instant you move the mouse
    // is what makes it feel like a warning rather than a glitch.
    if (warning) {
      warning = false;
      onWarn?.(null);
    }

    if (now - lastWrite < WRITE_EVERY_MS) return;
    lastWrite = now;
    markActive(now);
  };

  const tick = () => {
    if (stopped) return;
    const idleFor = Date.now() - readLastActivity();

    if (idleFor >= idleMs) {
      stopped = true;
      setSignOutReason("idle");
      onExpire?.();
      return;
    }

    const remaining = idleMs - idleFor;
    if (remaining <= warnMs) {
      warning = true;
      onWarn?.(remaining);
    } else if (warning) {
      // Another tab saw activity, so this tab's warning is stale.
      warning = false;
      onWarn?.(null);
    }
  };

  const onStorage = (event) => {
    // Activity in a sibling tab. Re-evaluate now rather than leaving a warning
    // on screen for up to a second after the other tab reset the clock.
    if (event.key === ACTIVITY_KEY) tick();
  };

  ACTIVITY_EVENTS.forEach((name) =>
    window.addEventListener(name, record, { passive: true, capture: true }));
  WINDOW_EVENTS.forEach((name) =>
    window.addEventListener(name, record, { passive: true }));
  window.addEventListener("storage", onStorage);

  const timer = setInterval(tick, 1000);

  return () => {
    stopped = true;
    clearInterval(timer);
    ACTIVITY_EVENTS.forEach((name) =>
      window.removeEventListener(name, record, { capture: true }));
    WINDOW_EVENTS.forEach((name) => window.removeEventListener(name, record));
    window.removeEventListener("storage", onStorage);
  };
}
