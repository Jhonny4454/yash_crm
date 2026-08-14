import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { get, put } from "../api/client";
import { useLookup } from "../api/useLookup";
import { useToast } from "../context/ToastContext";
import {
  Empty, ErrorNote, Loading, fmtDate, inr, readableError,
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
  { key: "expired", days: -1, label: "Already expired" },
  // Not an expiry window at all - the API switches which date it filters on.
  // It lives in the same list because it is the same rows and the same
  // columns, and the operator arriving from the dashboard's Renewed chip
  // expects to land on this board, not a separate screen.
  { key: "renewed", days: 7, label: "Renewed (last 7 days)", mode: "renewed" },
  { key: "renewed30", days: 30, label: "Renewed (last 30 days)", mode: "renewed" },
  { key: "renewedall", days: "all", label: "Renewed (all)", mode: "renewed" },
];

const today = () => new Date().toISOString().slice(0, 10);

function addDays(iso, days) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export default function PlanExpiryBoard() {
  const { toast } = useToast();
  const [params, setParams] = useSearchParams();

  const range = params.get("range") || "7";
  const zone = params.get("zone") || "";
  const selectedRange = RANGES.find((r) => r.key === range);
  const days = selectedRange?.days ?? 7;
  const mode = selectedRange?.mode;

  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  // Per-row pending edits, keyed by customer_plan_id.
  const [edits, setEdits] = useState({});
  const [savingId, setSavingId] = useState(null);

  const { options: zones } = useLookup("/masters/zones", { valueKey: "name", labelKey: "name" });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEdits({});

    get("/reports/plan-expiry", { days, mode, zone: zone || undefined })
      .then((payload) => { if (!cancelled) setRows(payload?.data || []); })
      .catch((err) => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [days, mode, zone, reloadKey]);

  const patch = (updates) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(updates)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    return next;
  }, { replace: true });

  const totals = useMemo(() => ({
    count: rows.length,
    outstanding: rows.reduce((sum, r) => sum + Number(r.outstanding || 0), 0),
    value: rows.reduce((sum, r) => sum + Number(r.price || 0), 0),
  }), [rows]);

  function editRow(id, field, value) {
    setEdits((prev) => ({ ...prev, [id]: { ...prev[id], [field]: value } }));
  }

  /** Extend by this plan's own validity period from the current end date. */
  function quickRenew(row) {
    const current = edits[row.customer_plan_id]?.end_date || row.end_date;
    const start = addDays(current, 1);
    const validityDays = Math.max(1, Number(row.validity_days || 30));
    editRow(row.customer_plan_id, "start_date", start);
    editRow(row.customer_plan_id, "end_date", addDays(start, validityDays - 1));
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

  const dirtyCount = Object.keys(edits).length;

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>Plan expiry</h1>
          <p>Renew a plan by editing its dates here — no need to open each customer.</p>
        </div>
      </div>

      <div className="toolbar">
        <div className="filter-chips" role="group" aria-label="Expiry window">
          {RANGES.map((r) => (
            <button key={r.key} type="button"
                    className={range === r.key ? "chip is-active" : "chip"}
                    onClick={() => patch({ range: r.key })}>
              {r.label}
            </button>
          ))}
        </div>
        <select className="input" style={{ maxWidth: 200 }} value={zone}
                onChange={(e) => patch({ zone: e.target.value })} aria-label="Filter by zone">
          <option value="">All zones</option>
          {zones.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>

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

                  return (
                    <tr key={id} className={
                      overdue ? "rail rail-danger" : row.days_left <= 3 ? "rail rail-warn" : "rail rail-idle"
                    }>
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
                          <button type="button" className="btn sm"
                                  title={`Extend by ${row.validity_days || 30} days`}
                                  onClick={() => quickRenew(row)}>
                            +{row.validity_days || 30}d
                          </button>
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
                  <td colSpan={4}><strong>{totals.count} plan{totals.count === 1 ? "" : "s"}</strong></td>
                  <td className="right num"><strong>{inr(totals.value)}</strong></td>
                  <td colSpan={3} />
                  <td className="right num"><strong>{inr(totals.outstanding)}</strong></td>
                  <td />
                </tr>
              </tfoot>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}
