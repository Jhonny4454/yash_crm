import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { Empty, ErrorNote, fmtDate, inr, Loading, Pager, readableError, ScrollArrows } from "../components/ui";
import "../styles/RenewalQueue.css";

/**
 * Renewal queue - requests customers raised from the portal.
 *
 * These were being written to the database by the portal and read by nobody:
 * there was no admin screen at all, so a customer could pay for a renewal and
 * wait indefinitely for someone who had no way of knowing.
 *
 * The organising idea is *has the money arrived*. Approving extends service,
 * so an unpaid request is the one thing on this screen that must never be a
 * one-click action - it is possible, but it takes a separate, labelled choice.
 */
const STATUSES = [
  ["pending", "Pending"],
  ["approved", "Approved"],
  ["rejected", "Rejected"],
  ["cancelled", "Cancelled"],
  ["all", "All"],
];

export default function RenewalQueue() {
  const { isAdmin } = useAuth();
  const { toast, confirm } = useToast();

  const [status, setStatus] = useState("pending");
  const [kind, setKind] = useState("");
  const [paidOnly, setPaidOnly] = useState(false);
  const [term, setTerm] = useState("");
  const [applied, setApplied] = useState("");
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [totals, setTotals] = useState(null);
  const [counts, setCounts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    const params = { status, page };
    if (kind) params.kind = kind;
    if (paidOnly) params.paid = 1;
    if (applied) params.q = applied;

    return get("/renewals", params)
      .then((response) => {
        const data = Array.isArray(response?.data) ? response.data : [];
        setRows(data);
        setMeta(response?.meta || null);
        setTotals(response?.totals || null);
        const live = new Set(data.filter((r) => r.status === "pending")
          .map((r) => r.id));
        setSelected((prev) => new Set([...prev].filter((id) => live.has(id))));
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [status, kind, paidOnly, applied, page]);

  useEffect(() => { load(); }, [load]);

  const refreshCounts = useCallback(() => {
    get("/renewals/counts")
      .then((r) => setCounts(r?.data ?? r))
      .catch(() => setCounts(null));
  }, []);
  useEffect(() => { refreshCounts(); }, [refreshCounts]);

  const pending = useMemo(() => rows.filter((r) => r.status === "pending"), [rows]);
  const selectable = useMemo(() => pending.filter((r) => r.invoice_paid), [pending]);
  const allPaidSelected = selectable.length > 0
    && selectable.every((r) => selected.has(r.id));

  const chosen = useMemo(() => rows.filter((r) => selected.has(r.id)), [rows, selected]);
  const chosenTotal = chosen.reduce((sum, r) => sum + Number(r.amount || 0), 0);
  const chosenUnpaid = chosen.filter((r) => !r.invoice_paid).length;

  function toggle(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  /** Header tick selects only the PAID pending rows - the safe ones. */
  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      selectable.forEach((r) => {
        if (allPaidSelected) next.delete(r.id);
        else next.add(r.id);
      });
      return next;
    });
  }

  async function decide(action) {
    const ids = [...selected];
    if (!ids.length) return;

    let note = "";
    if (action === "reject") {
      const reason = window.prompt(
        `Why are these ${ids.length} request(s) being turned down? `
        + "The customer raised them and may ask.",
      );
      if (reason === null) return;
      if (!reason.trim()) return toast.error("A reason is required to reject.");
      note = reason.trim();
    }

    const confirmed = await confirm({
      title: action === "approve"
        ? `Approve ${ids.length} renewal${ids.length > 1 ? "s" : ""}?`
        : `Reject ${ids.length} renewal${ids.length > 1 ? "s" : ""}?`,
      message: action === "approve"
        ? `${inr(chosenTotal)} of renewals. Each plan is extended and any `
          + "suspended connection comes back online."
          + (chosenUnpaid
            ? ` ${chosenUnpaid} of these are UNPAID and will be skipped.`
            : "")
        : "Their invoices stop chasing, and anything already paid is credited "
          + "to the customer's wallet rather than kept.",
      confirmLabel: action === "approve" ? "Approve" : "Reject",
      tone: action === "reject" ? "danger" : undefined,
    });
    if (!confirmed) return;

    setBusy(true);
    setOutcome(null);
    try {
      const response = await post("/renewals/bulk", { ids, action, note });
      const data = response?.data ?? response;
      setOutcome(data);
      toast.success(`${data.done_count} request(s) ${action}d.`);
      setSelected(new Set());
      await load();
      refreshCounts();
    } catch (bulkError) {
      toast.error(bulkError.detail || readableError(bulkError));
    } finally {
      setBusy(false);
    }
  }

  async function approveOne(row) {
    const unpaid = !row.invoice_paid;
    const confirmed = await confirm({
      title: `Approve ${row.customer_name}'s ${row.kind_label.toLowerCase()}?`,
      message: unpaid
        ? `${row.invoice_no} still has ${inr(row.invoice_balance)} outstanding. `
          + "Approving now extends their service for money that has not arrived."
        : `${inr(row.amount)} received. Their plan extends by ${row.days} days`
          + `${row.kind === "change" ? ` and switches to ${row.requested_plan}` : ""}.`,
      confirmLabel: unpaid ? "Approve anyway" : "Approve",
      tone: unpaid ? "danger" : undefined,
    });
    if (!confirmed) return;

    setBusy(true);
    try {
      const response = await post(`/renewals/${row.id}/approve`,
                                  unpaid ? { allow_unpaid: true } : {});
      const data = response?.data ?? response;
      toast.success(`${row.customer_name} renewed to ${fmtDate(data.effective_to)}.`);
      await load();
      refreshCounts();
    } catch (approveError) {
      toast.error(approveError.detail || readableError(approveError));
    } finally {
      setBusy(false);
    }
  }

  async function rejectOne(row) {
    const reason = window.prompt(
      `Why is ${row.customer_name}'s request being turned down?`);
    if (reason === null) return;
    if (!reason.trim()) return toast.error("A reason is required.");

    setBusy(true);
    try {
      const response = await post(`/renewals/${row.id}/reject`,
                                  { note: reason.trim() });
      const data = response?.data ?? response;
      toast.success(data.detail || "Request rejected.",
                    data.credited_to_wallet ? { duration: 9000 } : undefined);
      await load();
      refreshCounts();
    } catch (rejectError) {
      toast.error(rejectError.detail || readableError(rejectError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page renewal-queue">
      <div className="page-heading">
        <div>
          <h1>Renewal requests</h1>
          <p>
            Renewals and plan changes customers started from the portal.
            Approving extends the plan; the money must be in first.
          </p>
        </div>
        {counts && (
          <div className="queue-counts">
            <span><strong>{counts.pending}</strong> pending</span>
            <span className="ok"><strong>{counts.pending_paid}</strong> paid</span>
            <span><strong>{inr(counts.pending_amount)}</strong> in the queue</span>
          </div>
        )}
      </div>

      <ErrorNote error={error} onRetry={load} />

      <div className="panel-card filter-bar">
        <div className="status-tabs" role="tablist">
          {STATUSES.map(([key, label]) => (
            <button key={key} type="button" role="tab"
                    aria-selected={status === key}
                    className={status === key ? "is-active" : undefined}
                    onClick={() => { setStatus(key); setPage(1); }}>
              {label}
              {counts?.[key] !== undefined && key !== "all" && (
                <span className="tab-count">{counts[key]}</span>
              )}
            </button>
          ))}
        </div>

        <form className="filter-right"
              onSubmit={(e) => { e.preventDefault(); setPage(1); setApplied(term); }}>
          <select value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }}>
            <option value="">Renewals and changes</option>
            <option value="renew">Renewals only</option>
            <option value="change">Plan changes only</option>
          </select>
          <label className="paid-toggle">
            <input type="checkbox" checked={paidOnly}
                   onChange={(e) => { setPaidOnly(e.target.checked); setPage(1); }} />
            <span>Paid only</span>
          </label>
          <input type="search" value={term} placeholder="Name, username or mobile"
                 onChange={(e) => setTerm(e.target.value)} />
          <button type="submit" className="btn sm">Search</button>
        </form>
      </div>

      {outcome && <BulkOutcome outcome={outcome} onDismiss={() => setOutcome(null)} />}

      <section className="panel-card">
        {status === "pending" && (
          <div className="queue-bar">
            <div>
              {totals && (
                <>
                  <strong>{totals.count}</strong> waiting
                  {totals.unpaid > 0 && (
                    <> · <span className="warn">{totals.unpaid} unpaid</span></>
                  )}
                  {" · "}<strong>{inr(totals.amount)}</strong>
                </>
              )}
            </div>
            <div className="row-actions">
              <span className="selection-count">
                {selected.size
                  ? `${selected.size} selected · ${inr(chosenTotal)}`
                  : "Nothing selected"}
              </span>
              {isAdmin ? (
                <>
                  <button type="button" className="btn sm danger"
                          disabled={!selected.size || busy}
                          onClick={() => decide("reject")}>Reject</button>
                  <button type="button" className="btn primary"
                          disabled={!selected.size || busy}
                          onClick={() => decide("approve")}>
                    {busy ? "Working…" : "Approve"}
                  </button>
                </>
              ) : <span className="muted">Only an administrator can decide these.</span>}
            </div>
          </div>
        )}

        {loading ? <Loading label="Loading the queue" />
          : !rows.length ? (
            <Empty title={status === "pending"
              ? "Nothing waiting for a decision"
              : `No ${status} requests`}
                   hint={status === "pending"
                     ? "Renewals customers raise from the portal appear here."
                     : "Try another tab."} />
          ) : (
            <ScrollArrows>
              <table className="tbl renewal-table">
                <thead>
                  <tr>
                    {status === "pending" && (
                      <th className="tick">
                        <input type="checkbox" checked={allPaidSelected}
                               disabled={!selectable.length}
                               aria-label="Select every paid request on this page"
                               onChange={toggleAll} />
                      </th>
                    )}
                    <th>Customer</th><th>Type</th><th>Plan</th>
                    <th>Cycles</th><th className="num">Amount</th>
                    <th>Invoice</th><th>Expires</th><th>Raised</th>
                    {status === "pending" ? <th>Actions</th> : <th>Decision</th>}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}
                        className={[selected.has(row.id) && "is-selected",
                                    !row.invoice_paid && row.status === "pending" && "is-unpaid"]
                          .filter(Boolean).join(" ")}>
                      {status === "pending" && (
                        <td className="tick">
                          <input type="checkbox" checked={selected.has(row.id)}
                                 aria-label={`Select ${row.customer_name}`}
                                 onChange={() => toggle(row.id)} />
                        </td>
                      )}
                      <td>
                        <Link to={`/customers/${row.customer_id}`}>{row.customer_name}</Link>
                        <small className="sub">{row.username || row.mobile}</small>
                      </td>
                      <td>
                        <span className={`kind-tag ${row.kind}`}>{row.kind_label}</span>
                        {row.kind === "change" && (
                          <small className="sub">{row.is_upgrade ? "Upgrade" : "Downgrade"}</small>
                        )}
                      </td>
                      <td>
                        {row.kind === "change"
                          ? <>{row.current_plan} <span className="arrow">→</span> <strong>{row.requested_plan}</strong></>
                          : row.requested_plan}
                      </td>
                      <td>{row.months} × {Math.round(row.days / (row.months || 1))}d</td>
                      <td className="num">{inr(row.amount)}</td>
                      <td>
                        {row.invoice_id ? (
                          <Link to={`/invoices/${row.invoice_id}`}>{row.invoice_no}</Link>
                        ) : "—"}
                        {row.invoice_paid
                          ? <small className="sub ok">Paid</small>
                          : <small className="sub due">{inr(row.invoice_balance)} due</small>}
                      </td>
                      <td>{fmtDate(row.current_expiry)}</td>
                      <td>{fmtDate(row.created_at)}</td>
                      <td>
                        {row.status === "pending" ? (
                          isAdmin ? (
                            <span className="row-actions">
                              <button type="button" className="btn sm primary" disabled={busy}
                                      onClick={() => approveOne(row)}>Approve</button>
                              <button type="button" className="btn sm danger" disabled={busy}
                                      onClick={() => rejectOne(row)}>Reject</button>
                            </span>
                          ) : "—"
                        ) : (
                          <div className="decision">
                            <strong className={`st-${row.status}`}>{row.status}</strong>
                            {row.effective_to && (
                              <small className="sub">to {fmtDate(row.effective_to)}</small>
                            )}
                            {row.decision_note && (
                              <small className="sub note">{row.decision_note}</small>
                            )}
                            {row.decided_by && <small className="sub">{row.decided_by}</small>}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollArrows>
          )}

        <Pager meta={meta} onPage={setPage} />
      </section>

      <ExpiryReminders isAdmin={isAdmin} />
    </section>
  );
}

/* ------------------------------------------------------------------ */

function BulkOutcome({ outcome, onDismiss }) {
  return (
    <section className="panel-card bulk-outcome">
      <div className="panel-head">
        {outcome.done_count} request(s) {outcome.action}d
        <button type="button" className="btn sm" onClick={onDismiss}>Dismiss</button>
      </div>
      <div className="bulk-body">
        {outcome.done?.length > 0 && (
          <ul className="outcome-list">
            {outcome.done.map((e) => (
              <li key={e.id}>
                {e.name}
                {e.effective_to && <> — extended to {fmtDate(e.effective_to)}</>}
                {e.credited_to_wallet > 0 && (
                  <> — {inr(e.credited_to_wallet)} credited to their wallet</>
                )}
              </li>
            ))}
          </ul>
        )}
        {/* Skipped rows stay open. A bulk action that quietly left three
            customers behind is the thing the operator most needs to see. */}
        {outcome.skipped?.length > 0 && (
          <div className="outcome-skipped">
            <strong>{outcome.skipped.length} were not {outcome.action}d:</strong>
            <ul className="outcome-list">
              {outcome.skipped.map((e, i) => (
                <li key={`${e.id}-${i}`}>{e.name} — {e.reason}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function ExpiryReminders({ isAdmin }) {
  const { toast, confirm } = useToast();
  const [days, setDays] = useState(7);
  const [due, setDue] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  useEffect(() => {
    get("/renewals/due", { days })
      .then((r) => setDue({ rows: r?.data || [], totals: r?.totals || {} }))
      .catch(() => setDue(null));
  }, [days]);

  async function send() {
    const count = due?.totals?.count || 0;
    const confirmed = await confirm({
      title: `Message ${count} customer${count === 1 ? "" : "s"}?`,
      message: `Everyone whose plan expires within ${days} days gets an expiry `
        + "reminder. This sends real messages.",
      confirmLabel: "Send reminders",
    });
    if (!confirmed) return;

    setBusy(true);
    setResult(null);
    try {
      const response = await post("/renewals/send-reminders", { days });
      const data = response?.data ?? response;
      setResult(data);
      // Never report a dry run as a send: with the gateway off, "214 sent"
      // would be a lie that stops anyone chasing the real problem.
      if (data.sent_count) toast.success(`${data.sent_count} reminder(s) sent.`);
      else if (data.dry_run_count) {
        toast.warning(`${data.dry_run_count} reminder(s) were logged but NOT sent `
          + "- the messaging gateway is not configured.", { duration: 11000 });
      } else toast.error("No reminders went out.");
    } catch (sendError) {
      toast.error(sendError.detail || readableError(sendError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel-card reminder-panel">
      <div className="panel-head">Expiry reminders</div>
      <div className="reminder-body">
        <div className="reminder-controls">
          <label>
            <span>Expiring within</span>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[3, 7, 15, 30].map((d) => (
                <option key={d} value={d}>{d} days</option>
              ))}
            </select>
          </label>
          <p className="reminder-count">
            {due
              ? <>
                  <strong>{due.totals.count || 0}</strong> customer(s) due
                  {due.totals.without_mobile > 0 && (
                    <> · <span className="warn">{due.totals.without_mobile} have no mobile number</span></>
                  )}
                </>
              : "Loading…"}
          </p>
          {isAdmin && (
            <button type="button" className="btn primary" disabled={busy || !due?.totals?.count}
                    onClick={send}>
              {busy ? "Sending…" : "Send reminders"}
            </button>
          )}
        </div>

        {due?.rows?.length > 0 && (
          <ScrollArrows>
            <table className="tbl">
              <thead>
                <tr><th>Customer</th><th>Plan</th><th>Zone</th><th>Expires</th><th>Days left</th></tr>
              </thead>
              <tbody>
                {due.rows.slice(0, 10).map((row) => (
                  <tr key={row.customer_plan_id}>
                    <td>
                      <Link to={`/customers/${row.customer_id}`}>{row.name}</Link>
                      {!row.has_mobile && <small className="sub warn">No mobile</small>}
                    </td>
                    <td>{row.plan_name}</td>
                    <td>{row.zone || "—"}</td>
                    <td>{fmtDate(row.end_date)}</td>
                    <td className={row.days_left <= 2 ? "due" : undefined}>{row.days_left}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {due.rows.length > 10 && (
              <p className="muted more-note">
                Showing 10 of {due.totals.count}. The send covers all of them.
              </p>
            )}
          </ScrollArrows>
        )}

        {result && (
          <div className={`reminder-result ${result.sent_count ? "ok" : "warn"}`}>
            <strong>
              {result.sent_count} sent · {result.dry_run_count} logged only ·{" "}
              {result.failed_count} failed · {result.skipped_count} skipped
            </strong>
            {!result.gateway_configured && (
              <p>
                The messaging gateway is not configured, so nothing actually
                reached a customer. Set it up under Settings → WhatsApp.
              </p>
            )}
            {result.failed?.length > 0 && (
              <ul className="outcome-list">
                {result.failed.slice(0, 5).map((f, i) => (
                  <li key={i}>{f.name} — {f.reason}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
