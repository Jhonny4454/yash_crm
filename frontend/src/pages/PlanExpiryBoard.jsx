import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { get, post, put } from "../api/client";
import { useLookup } from "../api/useLookup";
import { useToast } from "../context/ToastContext";
import {
  Empty, ErrorNote, Loading, Pager, fmtDate, inr, readableError,
} from "../components/ui";
import "../styles/Forms.css";

/**
 * Plan expiry board - who is expiring, who has expired, and renew them in place.
 *
 * Replaces reports/plan_expiry.html plus the inline-editable expiry table that
 * sat on the Jinja dashboard. That table posted to customer_plan_update_dates;
 * the REST equivalent (PUT /customer-plans/<id>/dates) existed but nothing in
 * the SPA called it, so renewing a plan meant leaving the report.
 *
 * Dates are edited per row and saved individually: the endpoint takes one plan
 * at a time, and a partial failure should not roll back rows that saved fine.
 *
 * There is deliberately NO "+30d" / "+20d" quick-extend button. It filled the
 * two date boxes immediately but only staged the change in local state, so the
 * dates on screen said the plan had been extended while the database still
 * said it had not - and pressing it, seeing the dates move and navigating away
 * lost the lot. That is exactly the shape of "I edited it and it reverted",
 * which is what it kept getting reported as. Type the end date, press Save.
 *
 * Paged at 100 rows. The endpoint used to return EVERY matching row - 96 KB at
 * 604 customers, roughly 1.5 MB at ten thousand - serialised again on every
 * filter change, which is most of what made this screen feel slow.
 *
 * The expired view, and only the expired view, can select rows and send them a
 * WhatsApp expiry notice. Expiring and Renewed are deliberately read-only:
 * telling somebody whose plan runs out next Tuesday that it has already
 * expired is worse than not messaging them, and Renewed is a record of work
 * already done.
 */

const RANGES = [
  { key: "7", days: 7, label: "Next 7 days" },
  { key: "15", days: 15, label: "Next 15 days" },
  { key: "30", days: 30, label: "Next 30 days" },
  // No far edge: every plan still to run out, however far off. The dashboard's
  // "View all" lands here, because the next question after "who expires this
  // week" is always "and after that?" - and a 30-day cap silently answered it
  // wrong for anyone on a quarterly or yearly plan.
  { key: "all", days: "all", label: "All upcoming" },
  { key: "expired", days: -1, label: "Already expired", notify: true },
  // Not an expiry window at all - the API switches which date it filters on.
  // It lives in the same list because it is the same rows and the same
  // columns, and the operator arriving from the dashboard's Renewed chip
  // expects to land on this board, not a separate screen.
  { key: "renewed", days: 7, label: "Renewed (last 7 days)", mode: "renewed" },
  { key: "renewed30", days: 30, label: "Renewed (last 30 days)", mode: "renewed" },
  { key: "renewedall", days: "all", label: "Renewed (all)", mode: "renewed" },
];

const PER_PAGE = 100;

const today = () => new Date().toLocaleDateString("en-CA");

export default function PlanExpiryBoard() {
  const { toast, confirm } = useToast();
  const [params, setParams] = useSearchParams();

  const range = params.get("range") || "7";
  const zone = params.get("zone") || "";
  const page = Math.max(1, Number(params.get("page") || 1));
  const selectedRange = RANGES.find((r) => r.key === range) || RANGES[0];
  const days = selectedRange.days;
  const mode = selectedRange.mode;
  const canNotify = Boolean(selectedRange.notify);

  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  // Per-row pending edits, keyed by customer_plan_id.
  const [edits, setEdits] = useState({});
  const [savingId, setSavingId] = useState(null);

  // Ticked rows, and the separate "everything the filter matches" mode.
  // They are different things: 100 ticks on this page is not the same
  // instruction as "all 3,412 expired customers", and conflating them is how
  // a bulk send goes to the wrong list.
  const [picked, setPicked] = useState(() => new Set());
  const [allMatching, setAllMatching] = useState(false);
  const [sending, setSending] = useState(false);
  const [job, setJob] = useState(null);
  const jobId = useRef(null);

  const { options: zones } = useLookup("/masters/zones", { valueKey: "name", labelKey: "name" });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEdits({});

    get("/reports/plan-expiry", {
      days, mode, zone: zone || undefined, page, per_page: PER_PAGE,
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
  }, [days, mode, zone, page, reloadKey]);

  // A change of filter is a change of list, so the selection cannot survive
  // it. Paging deliberately does NOT clear it - ticking rows on page 1, going
  // to page 2 and losing them is the classic way a bulk action gets sent to
  // half the people it should have.
  useEffect(() => {
    setPicked(new Set());
    setAllMatching(false);
  }, [range, zone]);

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

  async function sendExpiryNotice() {
    if (!canNotify || sending || !selectedCount) return;

    const confirmed = await confirm({
      title: `Send the expiry notice to ${selectedCount} customer${selectedCount === 1 ? "" : "s"}?`,
      message: allMatching
        ? `Every expired customer the current filter matches${zone ? ` in ${zone}` : ""} `
          + "will get a WhatsApp message saying their plan has expired. "
          + "WhatsApp messages cannot be recalled once sent."
        : "They will each get a WhatsApp message saying their plan has expired. "
          + "WhatsApp messages cannot be recalled once sent.",
      confirmLabel: "Send now",
      tone: "danger",
    });
    if (!confirmed) return;

    setSending(true);
    try {
      // The filter travels in the query string, exactly as it does on the GET.
      // The server re-runs the same query rather than trusting a list of ids
      // from the browser - so "all matching" means all matching on the server,
      // and a tampered id cannot pull in somebody outside the current view.
      const query = new URLSearchParams({ days: String(days) });
      if (zone) query.set("zone", zone);

      const response = await post(
        `/reports/plan-expiry/notify?${query.toString()}`,
        allMatching ? { all: true } : { customer_plan_ids: [...picked] },
      );
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

  const dirtyCount = Object.keys(edits).length;

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>Plan expiry</h1>
          <p>
            Renew a plan by editing its dates here — no need to open each customer.
            {canNotify && " Tick the customers you want to remind, then send the expiry notice."}
          </p>
        </div>
      </div>

      <div className="toolbar">
        <div className="filter-chips" role="group" aria-label="Expiry window">
          {RANGES.map((r) => (
            <button key={r.key} type="button"
                    className={range === r.key ? "chip is-active" : "chip"}
                    onClick={() => patch({ range: r.key, page: "" })}>
              {r.label}
            </button>
          ))}
        </div>
        <select className="input" style={{ maxWidth: 200 }} value={zone}
                onChange={(e) => patch({ zone: e.target.value, page: "" })}
                aria-label="Filter by zone">
          <option value="">All zones</option>
          {zones.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>

      {job && <JobProgress job={job} />}

      {canNotify && selectedCount > 0 && (
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
            <button type="button" className="btn sm primary"
                    disabled={sending} onClick={sendExpiryNotice}>
              {sending ? "Sending…" : "Send expiry notice on WhatsApp"}
            </button>
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
            <Loading label="Loading plans" />
          ) : !rows.length ? (
            <Empty
              title={mode === "renewed" ? "No renewals in this window"
                : days === -1 ? "No expired plans"
                  : days === "all" ? "Nothing is due to expire"
                    : "Nothing expiring in this window"}
              hint={zone ? `Try clearing the ${zone} zone filter.` : "Try a wider date range."}
            />
          ) : (
            <table className="data">
              <thead>
                <tr>
                  {canNotify && (
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
                  {mode === "renewed" && <th>Renewed on</th>}
                  <th className="right">Days left</th>
                  <th className="right">Outstanding</th>
                  <th className="right">Save</th>
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
                      {canNotify && (
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
                      {mode === "renewed" && (
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
                      <td className="right">
                        <div className="row-actions">
                          <button type="button" className="btn sm primary"
                                  disabled={!pending || savingId === id || invalid}
                                  onClick={() => saveRow(row)}>
                            {savingId === id ? "…" : "Save"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={canNotify ? 5 : 4}>
                    <strong>{totals.count} plan{totals.count === 1 ? "" : "s"}</strong>
                    {meta?.pages > 1 && (
                      <span className="muted"> · showing {rows.length} on page {meta.page} of {meta.pages}</span>
                    )}
                  </td>
                  <td className="right num"><strong>{inr(totals.value)}</strong></td>
                  <td colSpan={mode === "renewed" ? 4 : 3} />
                  <td className="right num"><strong>{inr(totals.outstanding)}</strong></td>
                  <td />
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
