import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { get, post, put } from "../api/client";
import { useLookup } from "../api/useLookup";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import {
  Empty, ErrorNote, Loading, Pager, fmtDate, inr, readableError,
} from "../components/ui";
import "../styles/Forms.css";

/**
 * Three screens, not one: Expiring soon, Expired, and Recently renewed.
 *
 * They used to be a single board with a row of filter chips, and all three
 * "View all" buttons on the dashboard led to it. Landing on the same page from
 * three different questions is disorienting - you press "Expired" and arrive
 * somewhere that says "Next 7 days" until you notice which chip is lit - and
 * it forced one set of controls onto three jobs that need different ones.
 *
 * They now have their own routes, their own titles, and their own controls:
 *
 *   /reports/expiring   who runs out soon. Editable dates, so you can renew
 *                       before the connection drops. No messaging: telling
 *                       somebody their plan HAS expired when it has not is
 *                       worse than not writing at all.
 *
 *   /reports/expired    who has already lapsed. The only screen that can
 *                       select rows, because it is the only one where a bulk
 *                       action makes sense: Quick Renew pushes every ticked
 *                       customer to one date, and the WhatsApp button sends
 *                       them the approved "plan expired" template.
 *
 *   /reports/renewed    a record of work already done. Deliberately READ ONLY
 *                       - no date boxes, no Save, nothing to press. A renewal
 *                       log you can accidentally edit is not a log.
 *
 * The table itself is shared, because it is the same rows and the same
 * columns; what differs is which controls each view is allowed.
 *
 * There is deliberately NO "+30d" / "+20d" quick-extend button anywhere. It
 * filled the date boxes immediately but only staged the change in local state,
 * so the screen said the plan had been extended while the database said it had
 * not - and navigating away lost the lot. That is exactly the shape of "I
 * edited it and it reverted". Type the date and press Save, or tick the rows
 * and use Quick Renew.
 *
 * Paged at 100 rows. The endpoint used to return EVERY matching row - 96 KB at
 * 604 customers, roughly 1.5 MB at ten thousand - re-serialised on every filter
 * change, which is most of what made this screen feel slow.
 */

const PER_PAGE = 100;

const today = () => new Date().toLocaleDateString("en-CA");

const VIEWS = {
  expiring: {
    key: "expiring",
    title: "Expiring soon",
    blurb: "Connections about to run out. Edit a date and press Save to renew "
      + "one before it drops, or tick rows to send a reminder on WhatsApp.",
    ranges: [
      { key: "7", days: 7, label: "Next 7 days" },
      { key: "15", days: 15, label: "Next 15 days" },
      { key: "30", days: 30, label: "Next 30 days" },
      { key: "all", days: "all", label: "All upcoming" },
    ],
    editableDates: true,
    selectable: true,
    daysLabel: "Days left",
    emptyTitle: "Nothing expiring in this window",
  },
  expired: {
    key: "expired",
    title: "Expired customers",
    blurb: "Connections that have already lapsed. Tick the ones you want, then "
      + "renew them to a date or send the expiry notice on WhatsApp.",
    // One set, so no chips - the window is "everything already past".
    ranges: [],
    days: -1,
    editableDates: true,
    selectable: true,
    daysLabel: "Overdue",
    emptyTitle: "No expired plans",
    emptyHint: "Every connection on the books is still inside its period.",
  },
  renewed: {
    key: "renewed",
    title: "Recently renewed",
    blurb: "A record of renewals already done. Read only.",
    ranges: [
      { key: "7", days: 7, label: "Last 7 days" },
      { key: "30", days: 30, label: "Last 30 days" },
      { key: "all", days: "all", label: "Everything on record" },
    ],
    mode: "renewed",
    // The point of this view is that it is a LOG. Date boxes and a Save button
    // on a record of what already happened invite somebody to change history
    // by accident, and there is no undo.
    editableDates: false,
    showRenewedOn: true,
    daysLabel: "Days left",
    emptyTitle: "No renewals in this window",
  },
};

/** Old links (/reports/plan-expiry?range=…) land on the right new page. */
export function PlanExpiryRedirect() {
  const [params] = useSearchParams();
  const range = params.get("range") || "7";
  const zone = params.get("zone");

  const view = range.startsWith("renewed") ? "renewed"
    : range === "expired" ? "expired" : "expiring";

  // The renewed and expiring windows carry over; "expired" has none.
  const next = new URLSearchParams();
  if (zone) next.set("zone", zone);
  if (view === "renewed") next.set("range", range === "renewed30" ? "30"
    : range === "renewedall" ? "all" : "7");
  if (view === "expiring" && ["7", "15", "30", "all"].includes(range)) {
    next.set("range", range);
  }
  const query = next.toString();
  return <Navigate to={`/reports/${view}${query ? `?${query}` : ""}`} replace />;
}

export default function PlanExpiryBoard({ view = "expiring" }) {
  const config = VIEWS[view] || VIEWS.expiring;
  const { toast, confirm } = useToast();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();

  const canRenew = can("plans.renew");
  const canMessage = can("messages.send");

  const zone = params.get("zone") || "";
  // `on` pins the board to one date, from the dashboard's day chips. The
  // ranges below then stop applying - "12 Aug (10)" must open the ten plans
  // that end on 12 Aug, not whatever the 7-day default window says.
  const on = params.get("on") || "";
  const page = Math.max(1, Number(params.get("page") || 1));

  const ranges = config.ranges;
  const rangeKey = params.get("range") || (ranges.length ? ranges[0].key : "");
  const selectedRange = ranges.find((r) => r.key === rangeKey) || ranges[0];
  const days = config.days ?? selectedRange?.days ?? 7;
  const mode = config.mode;

  // Selection exists only where a bulk action does, and only for somebody who
  // can actually perform one. A column of checkboxes that leads to no button
  // reads as broken rather than as forbidden.
  const canSelect = Boolean(config.selectable) && (canRenew || canMessage);
  const canEdit = Boolean(config.editableDates) && canRenew;

  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  // Per-row pending edits, keyed by customer_plan_id.
  const [edits, setEdits] = useState({});
  const [savingId, setSavingId] = useState(null);

  // Ticked rows, and the separate "everything the filter matches" mode. They
  // are different instructions: 100 ticks on this page is not the same thing
  // as "all 3,412 expired customers", and conflating them is how a bulk action
  // goes to the wrong list.
  const [picked, setPicked] = useState(() => new Set());
  const [allMatching, setAllMatching] = useState(false);
  const [sending, setSending] = useState(false);
  const [renewing, setRenewing] = useState(false);
  const [renewTo, setRenewTo] = useState(today);
  const [job, setJob] = useState(null);
  const jobId = useRef(null);

  const { options: zones } = useLookup("/masters/zones", { valueKey: "name", labelKey: "name" });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEdits({});

    get("/reports/plan-expiry", {
      days, mode, zone: zone || undefined, on: on || undefined, page, per_page: PER_PAGE,
    })
      .then((payload) => {
        if (cancelled) return;
        setRows(payload?.data || []);
        setMeta(payload?.meta || null);
        setSummary(payload?.summary || null);
      })
      .catch((err) => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [days, mode, zone, on, page, reloadKey]);

  // A change of filter is a change of list, so the selection cannot survive it.
  // Paging deliberately does NOT clear it - ticking rows on page 1, going to
  // page 2 and losing them is the classic way a bulk action reaches half the
  // people it should have.
  useEffect(() => {
    setPicked(new Set());
    setAllMatching(false);
  }, [view, rangeKey, zone, on]);

  /* Follow a running send. Polling stops the moment the job reports a finish
   * time, so a completed send does not keep asking about itself forever. */
  useEffect(() => {
    if (!job || job.finished_at || !jobId.current) return undefined;
    const timer = setInterval(async () => {
      try {
        const response = await get(`/messages/jobs/${jobId.current}`);
        setJob(response?.data ?? response);
      } catch {
        // Jobs are pruned half an hour after they finish. Losing the progress
        // is not losing the send - the message log has every attempt.
        setJob((current) => (current ? { ...current, finished_at: Date.now() / 1000 } : null));
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [job]);

  const patch = useCallback((updates) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(updates)) {
      if (v) next.set(k, String(v));
      else next.delete(k);
    }
    return next;
  }, { replace: true }), [setParams]);

  // Server-side totals: they cover every matching row, not the hundred on
  // screen. A footer that only adds up the current page is worse than none.
  const totals = useMemo(() => summary || {
    count: rows.length,
    outstanding: rows.reduce((sum, r) => sum + Number(r.outstanding || 0), 0),
    value: rows.reduce((sum, r) => sum + Number(r.price || 0), 0),
  }, [summary, rows]);

  const matchingCount = meta?.total ?? totals.count ?? rows.length;
  const selectedCount = allMatching ? matchingCount : picked.size;
  const pageIds = useMemo(() => rows.map((r) => r.customer_plan_id), [rows]);
  const allOnPagePicked = pageIds.length > 0
    && pageIds.every((id) => allMatching || picked.has(id));

  function togglePick(id) {
    setAllMatching(false);
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function togglePage() {
    setAllMatching(false);
    setPicked((prev) => {
      const next = new Set(prev);
      if (allOnPagePicked) pageIds.forEach((id) => next.delete(id));
      else pageIds.forEach((id) => next.add(id));
      return next;
    });
  }

  function clearSelection() {
    setPicked(new Set());
    setAllMatching(false);
  }

  function editRow(id, field, value) {
    setEdits((prev) => ({ ...prev, [id]: { ...prev[id], [field]: value } }));
  }

  async function saveRow(row) {
    const id = row.customer_plan_id;
    const pending = edits[id];
    if (!pending) return;

    const start_date = pending.start_date ?? row.start_date;
    const end_date = pending.end_date ?? row.end_date;

    // Same rule the API enforces - catch it before the round trip.
    if (end_date < start_date) {
      toast.error("The end date cannot be before the start date.");
      return;
    }

    setSavingId(id);
    try {
      const response = await put(`/customer-plans/${id}/dates`, { start_date, end_date });
      const saved = response?.data || response;

      // Patch the row in place rather than refetching the whole board, so the
      // user keeps their scroll position and any other pending edits.
      setRows((prev) => prev.map((r) => (
        r.customer_plan_id === id
          ? {
              ...r,
              start_date: saved.start_date,
              end_date: saved.end_date,
              days_left: Math.round((new Date(saved.end_date) - new Date(today())) / 86400000),
            }
          : r
      )));
      setEdits((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      toast.success(`${row.customer_name}'s plan now runs to ${fmtDate(saved.end_date)}.`);
    } catch (err) {
      toast.error(
        err.message === "end_date_before_start_date"
          ? "The end date cannot be before the start date."
          : err.message === "no_valid_dates"
            ? "Enter a valid start or end date."
            : readableError(err),
      );
    } finally {
      setSavingId(null);
    }
  }

  /** The filter, exactly as the GET sends it, so the server re-runs the same
   *  query rather than trusting a list of ids from the browser. */
  function filterQuery() {
    const query = new URLSearchParams({ days: String(days) });
    if (mode) query.set("mode", mode);
    if (zone) query.set("zone", zone);
    if (on) query.set("on", on);
    return query.toString();
  }

  function selection() {
    return allMatching ? { all: true } : { customer_plan_ids: [...picked] };
  }

  async function sendExpiryNotice() {
    if (!canSelect || !canMessage || sending || !selectedCount) return;

    const isExpired = view === "expired";
    const confirmed = await confirm({
      title: isExpired
        ? `Send the expiry notice to ${selectedCount} customer${selectedCount === 1 ? "" : "s"}?`
        : `Send a reminder to ${selectedCount} customer${selectedCount === 1 ? "" : "s"}?`,
      message: (allMatching
        ? `Every customer the current filter matches${zone ? ` in ${zone}` : ""} `
        : "They ")
        + (isExpired
          ? "will get the approved plan expired WhatsApp template. "
          : "will get the appropriate expiry reminder WhatsApp template. ")
        + "WhatsApp messages cannot be recalled once sent.",
      confirmLabel: "Send now",
      tone: "danger",
    });
    if (!confirmed) return;

    setSending(true);
    try {
      const response = await post(
        `/reports/plan-expiry/notify?${filterQuery()}`, selection());
      const data = response?.data || response;

      if (!data?.recipients) {
        toast.warning(data?.detail || "Nobody in that selection has a mobile number on file.");
      } else {
        jobId.current = data.job?.id || null;
        setJob(data.job || null);
        toast.success(`Sending to ${data.recipients} customer`
          + `${data.recipients === 1 ? "" : "s"}. You can leave this screen.`);
        clearSelection();
      }
    } catch (err) {
      toast.error(
        err.message === "messaging_unavailable"
          ? "WhatsApp messaging is not configured. Check Settings."
          : readableError(err),
      );
    } finally {
      setSending(false);
    }
  }

  /* Push every selected plan out to one date, in one call.
   *
   * Deliberately raises NO invoices - extending service and billing for it are
   * separate decisions, and a button that quietly issued two hundred bills
   * would be an expensive surprise. The confirmation says so. */
  async function quickRenew() {
    if (!canSelect || !canRenew || renewing || !selectedCount) return;

    if (!renewTo) {
      toast.error("Pick the date these plans should run to.");
      return;
    }

    const confirmed = await confirm({
      title: `Renew ${selectedCount} plan${selectedCount === 1 ? "" : "s"} to ${fmtDate(renewTo)}?`,
      message: (allMatching
        ? `Every expired customer the current filter matches${zone ? ` in ${zone}` : ""} `
        : "The selected plans ")
        + `will run to ${fmtDate(renewTo)} and go back to active, each on the `
        + "plan they are already on — nobody is moved to a different package. "
        + "No invoice is raised — bill them from Generate Invoice.",
      confirmLabel: "Renew now",
    });
    if (!confirmed) return;

    setRenewing(true);
    try {
      const response = await post(`/reports/plan-expiry/renew?${filterQuery()}`,
        { end_date: renewTo, ...selection() });
      const data = response?.data || response;
      toast.success(data?.detail || `${data?.renewed || 0} plan(s) renewed.`);
      clearSelection();
      // Those rows have left this list - they are not expired any more.
      setReloadKey((k) => k + 1);
    } catch (err) {
      toast.error(readableError(err));
    } finally {
      setRenewing(false);
    }
  }

  const dirtyCount = Object.keys(edits).length;
  const showSave = canEdit;
  const showRenewedOn = Boolean(config.showRenewedOn);

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>{config.title}</h1>
          <p>{config.blurb}</p>
        </div>
        {/* The other two views are one click away, so arriving on the wrong
            one costs nothing. They are separate pages, not chips, because they
            answer separate questions. */}
        <nav className="view-switch" aria-label="Other expiry views">
          {Object.values(VIEWS).map((v) => (
            <Link key={v.key} to={`/reports/${v.key}`}
                  className={v.key === config.key ? "chip is-active" : "chip"}>
              {v.title}
            </Link>
          ))}
        </nav>
      </div>

      <div className="toolbar">
        {/* The exact-day view replaces the window chips: a day is already a
            window of one. The chip says which day and clears it in one tap. */}
        {on ? (
          <div className="filter-chips" role="group" aria-label="Exact day">
            <button type="button" className="chip is-active"
                    title="Show this whole list again"
                    onClick={() => patch({ on: "", range: "", page: "" })}>
              On {fmtDate(on)}
            </button>
          </div>
        ) : ranges.length > 0 && (
          <div className="filter-chips" role="group" aria-label="Window">
            {ranges.map((r) => (
              <button key={r.key} type="button"
                      className={rangeKey === r.key ? "chip is-active" : "chip"}
                      onClick={() => patch({ range: r.key, page: "" })}>
                {r.label}
              </button>
            ))}
          </div>
        )}
        <select className="input" style={{ maxWidth: 200 }} value={zone}
                onChange={(e) => patch({ zone: e.target.value, page: "", on: "" })}
                aria-label="Filter by zone">
          <option value="">All zones</option>
          {zones.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>

      {job && <JobProgress job={job} />}

      {canSelect && selectedCount > 0 && (
        <div className="bulk-bar" role="status">
          <span>
            <strong>{selectedCount}</strong> customer{selectedCount === 1 ? "" : "s"} selected
            {!allMatching && matchingCount > picked.size && (
              <>
                {" · "}
                <button type="button" className="linkish" onClick={() => setAllMatching(true)}>
                  Select all {matchingCount} matching
                </button>
              </>
            )}
          </span>
          <div className="bulk-actions">
            <button type="button" className="btn sm" onClick={clearSelection}>Clear</button>

            {/* Hidden, not disabled, for a user whose permissions do not cover
                them. A greyed-out button that never becomes usable is an
                invitation to ask why, every day, forever. */}
            {canRenew && (
              <div className="quick-renew">
                <label htmlFor="renew-to">Renew to</label>
                <input id="renew-to" type="date" className="input" value={renewTo}
                       min={today()} onChange={(e) => setRenewTo(e.target.value)} />
                <button type="button" className="btn sm primary"
                        disabled={renewing || !renewTo} onClick={quickRenew}>
                  {renewing ? "Renewing…" : "Quick Renew"}
                </button>
              </div>
            )}

            {canMessage && (
              <button type="button" className="btn sm"
                      disabled={sending} onClick={sendExpiryNotice}>
                {sending ? "Sending…" : view === "expired"
                  ? "Send expiry notice on WhatsApp"
                  : "Send reminder on WhatsApp"}
              </button>
            )}
          </div>
        </div>
      )}

      {dirtyCount > 0 && (
        <div className="bulk-bar" role="status">
          <span><strong>{dirtyCount}</strong> row{dirtyCount === 1 ? "" : "s"} with unsaved changes</span>
          <div className="bulk-actions">
            <button className="btn sm" onClick={() => setEdits({})}>Discard changes</button>
          </div>
        </div>
      )}

      <ErrorNote error={error} onRetry={() => setReloadKey((k) => k + 1)} />

      <div className="card">
        <div className="table-wrap">
          {loading ? (
            <Loading label={`Loading ${config.title.toLowerCase()}`} />
          ) : !rows.length ? (
            <Empty
              title={config.emptyTitle}
              hint={zone ? `Try clearing the ${zone} zone filter.`
                : config.emptyHint || "Try a wider date range."}
            />
          ) : (
            <table className="data">
              <thead>
                <tr>
                  {canSelect && (
                    <th className="select-col">
                      <input type="checkbox" checked={allOnPagePicked}
                             onChange={togglePage}
                             aria-label="Select every customer on this page" />
                    </th>
                  )}
                  <th>Customer</th><th>Mobile</th><th>Zone</th><th>Plan</th>
                  <th className="right">Price</th>
                  <th>Start date</th><th>End date</th>
                  {/* On the Renewed view, "days left" is not what the operator
                      came to see - they want to know WHEN it was renewed. */}
                  {showRenewedOn && <th>Renewed on</th>}
                  <th className="right">{config.daysLabel}</th>
                  <th className="right">Outstanding</th>
                  {showSave && <th className="right">Save</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const id = row.customer_plan_id;
                  const pending = edits[id];
                  const startValue = pending?.start_date ?? row.start_date ?? "";
                  const endValue = pending?.end_date ?? row.end_date ?? "";
                  const invalid = endValue && startValue && endValue < startValue;
                  const overdue = row.days_left < 0;
                  const ticked = allMatching || picked.has(id);
                  const rail = overdue ? "rail rail-danger"
                    : row.days_left <= 3 ? "rail rail-warn" : "rail rail-idle";

                  return (
                    <tr key={id} className={ticked ? `${rail} is-selected` : rail}>
                      {canSelect && (
                        <td className="select-col">
                          <input type="checkbox" checked={ticked}
                                 onChange={() => togglePick(id)}
                                 aria-label={`Select ${row.customer_name}`} />
                        </td>
                      )}
                      <td>
                        <Link to={`/customers/${row.customer_id}`}>{row.customer_name}</Link>
                      </td>
                      <td>{row.mobile || "—"}</td>
                      <td>{row.zone || "—"}</td>
                      <td>{row.plan_name || "—"}</td>
                      <td className="right num">{inr(row.price)}</td>

                      {canEdit ? (
                        <>
                          <td>
                            <input type="date" className="input mini-date" value={startValue}
                                   onChange={(e) => editRow(id, "start_date", e.target.value)}
                                   aria-label={`Start date for ${row.customer_name}`} />
                          </td>
                          <td>
                            <input type="date" className={`input mini-date${invalid ? " is-invalid" : ""}`}
                                   value={endValue} min={startValue || undefined}
                                   onChange={(e) => editRow(id, "end_date", e.target.value)}
                                   aria-invalid={Boolean(invalid)}
                                   aria-label={`End date for ${row.customer_name}`} />
                          </td>
                        </>
                      ) : (
                        <>
                          <td>{fmtDate(row.start_date)}</td>
                          <td>{fmtDate(row.end_date)}</td>
                        </>
                      )}

                      {showRenewedOn && (
                        <td>{row.renewed_on ? fmtDate(row.renewed_on) : "—"}</td>
                      )}
                      <td className="right num">
                        <span className={overdue ? "pill danger" : row.days_left <= 3 ? "pill warn" : "pill idle"}>
                          {overdue ? `${Math.abs(row.days_left)} overdue` : row.days_left}
                        </span>
                      </td>
                      <td className="right num">
                        {row.outstanding > 0
                          ? <strong style={{ color: "#b91c1c" }}>{inr(row.outstanding)}</strong>
                          : "—"}
                      </td>
                      {showSave && (
                        <td className="right">
                          <div className="row-actions">
                            <button type="button" className="btn sm primary"
                                    disabled={!pending || savingId === id || invalid}
                                    onClick={() => saveRow(row)}>
                              {savingId === id ? "…" : "Save"}
                            </button>
                          </div>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  {/* Checkbox + Customer/Mobile/Zone/Plan */}
                  <td colSpan={(canSelect ? 1 : 0) + 4}>
                    <strong>{totals.count} plan{totals.count === 1 ? "" : "s"}</strong>
                    {meta?.pages > 1 && (
                      <span className="muted"> · showing {rows.length} on page {meta.page} of {meta.pages}</span>
                    )}
                  </td>
                  <td className="right num"><strong>{inr(totals.value)}</strong></td>
                  {/* Start + End + optional Renewed on + the days column */}
                  <td colSpan={2 + (showRenewedOn ? 1 : 0) + 1} />
                  <td className="right num"><strong>{inr(totals.outstanding)}</strong></td>
                  {showSave && <td />}
                </tr>
              </tfoot>
            </table>
          )}
        </div>
        <Pager meta={meta} onPage={(next) => patch({ page: next > 1 ? next : "" })} />
      </div>
    </section>
  );
}

/**
 * How far a background send has got.
 *
 * Shows the count rather than a bare spinner: an operator who has just
 * messaged four hundred customers wants to know how many have gone, and
 * whether any are failing, while it is still happening - not afterwards.
 */
function JobProgress({ job }) {
  const done = Number(job.done || 0);
  const total = Number(job.total || 0) || 1;
  const percent = Math.min(100, Math.round((done / total) * 100));
  const finished = Boolean(job.finished_at);
  const tone = job.failed ? "warning" : finished ? "success" : "info";

  return (
    <div className={`alert ${tone}`} role="status">
      <div>
        <strong>{finished ? "Finished" : "Sending"} — {done} of {job.total}</strong>
        {job.failed > 0 && <> · {job.failed} failed</>}
        <div className="bulk-progress" aria-hidden="true">
          <span style={{ width: `${percent}%` }} />
        </div>
        <div className="hint">
          {finished
            ? "Every attempt is in the message log."
            : "This continues on the server — you can leave this screen."}
        </div>
      </div>
    </div>
  );
}
