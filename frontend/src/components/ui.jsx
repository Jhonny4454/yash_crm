import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
// ui.jsx is pulled in by the eager App shell, so this lands in the main CSS
// bundle rather than in a per-route chunk - which is what we want, since the
// skeletons have to be styled BEFORE a route's own chunk arrives.
import "../styles/Skeleton.css";

/* Whole rupees, everywhere.
 *
 * This printed two decimal places, so a plan priced at 3050.855 showed as
 * "₹3,050.86" on the customer's header, "₹3,050.85" on the invoice and
 * "₹3,051" on the dashboard - three different numbers for one amount, and an
 * operator reading a bill to a customer over the phone had no idea which was
 * real. Nothing in this business is billed in paise.
 *
 * Rounded, not truncated: ₹0.60 shows as ₹1, because dropping it would make
 * a column of figures fail to add up to its own total. */
export const inr = (value) => `₹${Math.round(Number(value || 0)).toLocaleString("en-IN", {
  maximumFractionDigits: 0,
})}`;

/** A money value as a whole-number STRING, for a form field.
 *
 * The API rounds these now, but a browser talking to a server that has not
 * been restarted yet would still be handed 3050.855 and put it straight into
 * an editable box. Rounding on the way into the form means the operator never
 * sees a number they did not type, whichever half is updated first. */
export const rupees = (value) => (
  value === null || value === undefined || value === ""
    ? "" : String(Math.round(Number(value) || 0)));

export const inrShort = (value) => `₹${Number(value || 0).toLocaleString("en-IN", {
  maximumFractionDigits: 0,
})}`;

export function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString("en-IN", {
    day: "2-digit", month: "short", year: "numeric",
  });
}

/**
 * The one plan a customer is on, out of every plan row the API returned.
 *
 * A customer is on a single service at a time, but the plan rows are a
 * history: assigning or changing a plan closes the old row and writes a new
 * one. So "the plan" is the open row, and picking it has to be done the same
 * way everywhere - the Plan tab, the header's Bill Upto date and the dialog
 * the Renew button opens must all be talking about the same line, or an
 * operator renews one thing while reading the dates of another.
 *
 * When more than one row is open - which older records can be, from before
 * assign/renew closed every open row - the longest-running one wins, because
 * that is the date the customer's service actually runs to. A customer whose
 * plan has lapsed has no open row at all; the newest row on record is
 * returned so the tab still shows what they were last on.
 */
export function currentPlan(plans) {
  const rows = Array.isArray(plans) ? plans : [];
  const open = rows.filter((plan) => plan?.status === "active");
  if (!open.length) return rows[0] || null;
  return open.reduce((best, plan) => {
    const a = plan.end_date || "";
    const b = best.end_date || "";
    if (a !== b) return a > b ? plan : best;         // ISO dates sort as text
    return Number(plan.id || 0) > Number(best.id || 0) ? plan : best;
  });
}

/** The old centred spinner. Kept for anything genuinely inline. */
export function Spinner({ label = "Loading" }) {
  return <div className="state loading"><span className="spinner" />{label}…</div>;
}

/**
 * Block-level "content is on its way".
 *
 * This renders a skeleton rather than the spinner it used to, and it is
 * defined this way instead of being replaced at ~33 call sites on purpose:
 * every one of those sites is a page or panel load sitting behind a lazily
 * loaded route. With a spinner the sequence was skeleton (code arriving),
 * spinner (data arriving), content - two jarring swaps for one navigation.
 * Now the shape holds still from the first paint to the last.
 */
export function Loading({ label = "Loading", rows = 4, cols = 4 }) {
  return <TableSkeleton rows={rows} cols={cols} label={label} />;
}

/**
 * A grey block standing in for content that has not arrived.
 *
 * Preferred over <Loading> anywhere the shape of the result is predictable.
 * A centred spinner tells you nothing and then dumps a full table into the
 * space it occupied, shifting everything below it; a skeleton of roughly the
 * right size means the page does not move when the data lands.
 */
export function Skeleton({ width = "100%", height = 14, radius = 6, className = "" }) {
  return <span className={`skeleton ${className}`} aria-hidden="true"
    style={{ width, height, borderRadius: radius }} />;
}

/** Placeholder rows sized to the table that is about to replace them. */
export function TableSkeleton({ rows = 6, cols = 5, label = "Loading" }) {
  return <div className="skeleton-table" role="status" aria-label={label}>
    <div className="skeleton-row skeleton-head">
      {Array.from({ length: cols }, (_, c) => <Skeleton key={c} height={10} width="60%" />)}
    </div>
    {Array.from({ length: rows }, (_, r) => (
      <div className="skeleton-row" key={r}>
        {Array.from({ length: cols }, (_, c) => (
          // Varied widths, because a grid of identical bars reads as a broken
          // table rather than as text that has not loaded.
          <Skeleton key={c} height={12} width={`${[85, 55, 70, 45, 65, 50][(r + c) % 6]}%`} />
        ))}
      </div>
    ))}
  </div>;
}

/**
 * Stand-in for a whole page while its code chunk downloads.
 *
 * Every route in this app is lazy-loaded, so navigation used to blank the
 * content area and drop a spinner in the middle of it. This keeps the page
 * the same shape it is about to be.
 */
export function PageSkeleton() {
  return <div className="skeleton-page" role="status" aria-label="Opening page">
    <div className="skeleton-page-head">
      <Skeleton width="220px" height={22} />
      <Skeleton width="120px" height={32} radius={8} />
    </div>
    <div className="skeleton-card"><TableSkeleton rows={7} cols={5} /></div>
  </div>;
}

export function Empty({ title = "Nothing here yet", hint, action }) {
  return <div className="state empty"><h3>{title}</h3>{hint && <p>{hint}</p>}{action}</div>;
}

export function ErrorNote({ error, onRetry }) {
  if (!error) return null;
  return <div className="alert error" role="alert">
    <span>
      {readableError(error)}
      {error?.detail && <small className="error-detail">{error.detail}</small>}
    </span>
    {onRetry && <button className="btn sm" onClick={onRetry}>Try again</button>}
  </div>;
}

const ERROR_MESSAGES = {
  invalid_credentials: "That username or password is not correct.",
  account_disabled: "This account is disabled. Please contact the administrator.",
  account_inactive: "Your account is no longer active. Please sign in again.",
  token_expired: "Your session expired. Please sign in again.",
  missing_token: "Please sign in to continue.",
  forbidden: "You do not have permission to do that.",
  network_error: "The server could not be reached. Check your internet connection and retry.",
  "Cannot reach the server.": "Cannot reach the server.",
  payment_gateway_not_configured: "Online payment is not configured yet. Please contact the office.",
  username_taken: "That username is already in use.",
};

export function readableError(error) {
  const key = typeof error === "string" ? error : error?.message;
  if (ERROR_MESSAGES[key]) return ERROR_MESSAGES[key];

  /* Prefer the server's sentence over its error code.
   *
   * The API answers with a machine key (`invalid_values`) plus a `detail` that
   * says what is actually wrong ("Status must be one of: draft, pending,
   * approved, rejected"). Only the key was ever shown, so a validation failure
   * put the literal word "invalid_values" on the screen - which tells the
   * operator nothing about which field or what to type instead. */
  const detail = typeof error === "object" ? error?.detail : null;
  if (detail) return detail;

  if (!key) return "Something went wrong. Please try again.";
  // A bare snake_case code is still better read as words than as an identifier.
  return /^[a-z0-9]+(_[a-z0-9]+)+$/.test(key)
    ? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) + "."
    : key;
}

/**
 * Row class for the coloured status rail on invoice and payment tables.
 *
 * Returns a `rail rail-<tone>` class so a table row can carry a 3px left
 * border in the same colour language as <StatusPill>: red for money that is
 * late or refused, amber for anything awaiting a human, green when settled.
 *
 * @param {"invoice"|"payment"} kind
 * @param {string} status                 status straight off the API row
 * @param {boolean} [needsAuthorization]  payments only - awaiting review
 * @returns {string}
 */
export function railFor(kind, status, needsAuthorization = false) {
  const value = String(status || "").toLowerCase();

  // A payment can be "approved" and still be sitting in the review queue.
  if (kind === "payment" && needsAuthorization) return "rail rail-warn";

  if (kind === "invoice") {
    if (value === "overdue") return "rail rail-danger";
    if (value === "paid") return "rail rail-ok";
    if (value === "partial") return "rail rail-warn";
    if (value === "cancelled") return "rail rail-muted";
    return "rail rail-idle"; // draft / sent
  }

  if (value === "rejected" || value === "failed") return "rail rail-danger";
  if (value === "approved") return "rail rail-ok";
  if (value === "pending") return "rail rail-warn";
  return "rail rail-idle";
}

export function StatusPill({ value, kind = "default" }) {
  const text = value || "unknown";
  const normalized = String(text).toLowerCase();
  const tone = ["paid", "approved", "active", "present", "success", "open"].includes(normalized)
    ? "ok"
    : ["pending", "sent", "partial", "draft"].includes(normalized)
      ? "warn"
      : ["overdue", "rejected", "expired", "cancelled", "inactive", "failed"].includes(normalized)
        ? "danger" : "idle";
  return <span className={`pill ${tone} ${kind}`}>{text}</span>;
}

export function Pager({ meta, onPage }) {
  if (!meta) return null;
  return <div className="pager">
    <span>{meta.total || 0} records</span>
    {meta.pages > 1 && <div>
      <button className="btn sm" disabled={!meta.has_prev} onClick={() => onPage(meta.page - 1)}>Previous</button>
      <span className="pager-page">Page {meta.page} of {meta.pages}</span>
      <button className="btn sm" disabled={!meta.has_next} onClick={() => onPage(meta.page + 1)}>Next</button>
    </div>}
  </div>;
}

/**
 * Route guard.
 *
 * There is no "Restoring your session" state here any more. The saved session
 * in localStorage answers "who is signed in?" synchronously, so waiting on a
 * network round-trip before rendering only bought a full-screen flash on every
 * page load. AuthContext re-validates in the background and signs the user out
 * if the server disagrees.
 *
 * The cached role decides `adminOnly` for one render before the server has
 * confirmed it. That is not a way in: every endpoint behind these screens
 * re-checks the role on the token, so a hand-edited localStorage buys an empty
 * page full of 403s and no data.
 */
export function ProtectedRoute({ children, audience, adminOnly = false }) {
  const auth = useAuth();
  if (!auth.isAuthenticated) return <Navigate to={audience === "customer" ? "/customer/login" : "/login"} replace />;
  if (audience && auth.audience !== audience) return <Navigate to={auth.isCustomer ? "/customer" : "/"} replace />;
  if (adminOnly && !auth.isAdmin) return <Navigate to="/forbidden" replace />;
  return children;
}
