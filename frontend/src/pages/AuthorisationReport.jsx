import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { Empty, ErrorNote, fmtDate, inr, Loading, Pager, readableError } from "../components/ui";
import "../styles/AuthorisationReport.css";

/**
 * Authorising Report - the day's collections waiting for sign-off.
 *
 * Two things make this different from a plain payment list, and both come from
 * how the money is actually reconciled: the operator works a zone or a
 * collector's round at a time, so the filters are the customer's location and
 * the agent who took the cash; and they sign off in batches, so selection is
 * the primary interaction rather than a per-row button.
 *
 * The header checkbox selects the loaded page only. Selecting rows the
 * operator cannot see would be a way to authorise money they never looked at.
 */

const EMPTY_FILTERS = {
  from: "", to: "", locality: "", area: "", building: "",
  zone: "", staff_id: "", mode: "", q: "",
};

export default function AuthorisationReport() {
  const { isAdmin } = useAuth();
  const { toast, confirm } = useToast();

  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [applied, setApplied] = useState(EMPTY_FILTERS);
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [totals, setTotals] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [options, setOptions] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    const params = Object.fromEntries(
      Object.entries(applied).filter(([, value]) => value !== ""),
    );
    return get("/payments/authorisation-queue", { ...params, page })
      .then((response) => {
        setRows(Array.isArray(response?.data) ? response.data : []);
        setMeta(response?.meta || null);
        setTotals(response?.totals || null);
        // Anything that is no longer in the queue must drop out of the
        // selection, or Submit would post ids that have already been decided.
        setSelected((previous) => {
          const visible = new Set((response?.data || []).map((r) => r.id));
          return new Set([...previous].filter((id) => visible.has(id)));
        });
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [applied, page]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    get("/payments/authorisation-filters")
      .then((response) => setOptions(response?.data ?? response))
      .catch(() => setOptions(null));
  }, []);

  const pageIds = useMemo(() => rows.map((row) => row.id), [rows]);
  const allOnPageSelected = pageIds.length > 0
    && pageIds.every((id) => selected.has(id));

  const selectedTotal = useMemo(
    () => rows.filter((row) => selected.has(row.id))
      .reduce((sum, row) => sum + Number(row.amount || 0), 0),
    [rows, selected],
  );

  function toggle(id) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((previous) => {
      const next = new Set(previous);
      if (allOnPageSelected) pageIds.forEach((id) => next.delete(id));
      else pageIds.forEach((id) => next.add(id));
      return next;
    });
  }

  function search(event) {
    event.preventDefault();
    setPage(1);
    setApplied(filters);
  }

  function reset() {
    setFilters(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setPage(1);
  }

  async function submit() {
    const ids = [...selected];
    if (!ids.length) return;

    const confirmed = await confirm({
      title: `Authorise ${ids.length} payment${ids.length > 1 ? "s" : ""}?`,
      message: `${inr(selectedTotal)} will be signed off and any invoice settled `
        + "by these entries will be marked paid. This is the final review step.",
      confirmLabel: "Authorise",
    });
    if (!confirmed) return;

    setBusy(true);
    try {
      const response = await post("/payments/authorize-bulk", { ids });
      const result = response?.data ?? response;
      toast.success(`${result.authorised_count} payment(s) authorised.`);
      // Say plainly what did not move rather than letting the count imply
      // everything did.
      if (result.skipped?.length) {
        toast.warning(
          `${result.skipped.length} were skipped: `
          + result.skipped.map((s) => `#${s.id} ${s.reason}`).join(" "),
          { duration: 10000 },
        );
      }
      setSelected(new Set());
      await load();
    } catch (submitError) {
      toast.error(submitError.detail || readableError(submitError));
    } finally {
      setBusy(false);
    }
  }

  async function rejectSelected() {
    const ids = [...selected];
    if (!ids.length) return;

    const reason = window.prompt(
      `Why are these ${ids.length} entries being rejected? The customer may ask.`,
    );
    if (reason === null) return;
    if (!reason.trim()) return toast.error("A reason is required to reject.");

    setBusy(true);
    try {
      const response = await post("/payments/reject-bulk", { ids, reason: reason.trim() });
      const result = response?.data ?? response;
      toast.success(`${result.rejected_count} payment(s) rejected.`);
      setSelected(new Set());
      await load();
    } catch (rejectError) {
      toast.error(rejectError.detail || readableError(rejectError));
    } finally {
      setBusy(false);
    }
  }

  function exportCsv() {
    const header = ["#", "Name", "Username", "Flat No.", "Building", "Area",
      "Zone", "Mode", "Details", "Receipt No", "Amount", "Discount",
      "Outstanding", "Receipt Date", "Agent"];
    const escape = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
    const lines = [header.join(",")];
    rows.forEach((row, index) => {
      lines.push([index + 1, row.name, row.username, row.flat_no, row.building,
        row.area, row.zone, row.mode, row.details, row.receipt_no, row.amount,
        row.discount, row.outstanding, row.receipt_date, row.agent]
        .map(escape).join(","));
    });

    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = href;
    link.download = `authorising-report-${new Date().toLocaleDateString("en-CA")}.csv`;
    link.click();
    URL.revokeObjectURL(href);
  }

  return (
    <section className="page auth-report">
      <div className="page-heading">
        <div>
          <h1>Authorising report</h1>
          <p>
            Collections waiting for sign-off. Tick the entries you have checked
            and submit them together.
          </p>
        </div>
        <div className="row-actions no-print">
          <button type="button" className="btn sm" onClick={exportCsv}
                  disabled={!rows.length} title="Download this page as CSV">
            <i className="fas fa-file-excel" aria-hidden="true" /> Export
          </button>
          <button type="button" className="btn sm" onClick={() => window.print()}
                  disabled={!rows.length} title="Print this page">
            <i className="fas fa-print" aria-hidden="true" /> Print
          </button>
        </div>
      </div>

      <form className="panel-card search-panel no-print" onSubmit={search}>
        <div className="panel-head">Search customer</div>
        <div className="search-grid">
          <Field label="Date from">
            <input type="date" value={filters.from}
                   onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
          </Field>
          <Field label="Date to">
            <input type="date" value={filters.to}
                   onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
          </Field>
          <Select label="Locality" value={filters.locality} options={options?.localities}
                  onChange={(v) => setFilters({ ...filters, locality: v })} />
          <Select label="Area" value={filters.area} options={options?.areas}
                  onChange={(v) => setFilters({ ...filters, area: v })} />
          <Select label="Building" value={filters.building} options={options?.buildings}
                  onChange={(v) => setFilters({ ...filters, building: v })} />
          <Select label="Zone" value={filters.zone} options={options?.zones}
                  onChange={(v) => setFilters({ ...filters, zone: v })} />
          <Field label="Staff">
            <select value={filters.staff_id}
                    onChange={(e) => setFilters({ ...filters, staff_id: e.target.value })}>
              <option value="">-Select-</option>
              {(options?.staff || []).map((member) => (
                <option key={member.id} value={member.id}>{member.name}</option>
              ))}
            </select>
          </Field>
          <Select label="Mode" value={filters.mode} options={options?.modes}
                  onChange={(v) => setFilters({ ...filters, mode: v })} />
          <Field label="Search">
            <input type="search" value={filters.q} placeholder="Name, username or receipt"
                   onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
          </Field>
          <div className="search-actions">
            <button type="submit" className="btn primary">Search</button>
            <button type="button" className="btn" onClick={reset}>Reset</button>
          </div>
        </div>
      </form>

      <ErrorNote error={error} onRetry={load} />

      <section className="panel-card">
        <div className="queue-bar">
          <div>
            {totals && (
              <>
                <strong>{totals.count}</strong> awaiting authorisation
                {" · "}<strong>{inr(totals.amount)}</strong> on this page
                {totals.discount > 0 && <> · {inr(totals.discount)} discounted</>}
              </>
            )}
          </div>
          <div className="row-actions no-print">
            <span className="selection-count">
              {selected.size
                ? `${selected.size} selected · ${inr(selectedTotal)}`
                : "Nothing selected"}
            </span>
            {isAdmin ? (
              <>
                <button type="button" className="btn sm danger"
                        disabled={!selected.size || busy} onClick={rejectSelected}>
                  Reject
                </button>
                <button type="button" className="btn primary"
                        disabled={!selected.size || busy} onClick={submit}>
                  {busy ? "Working…" : "Submit"}
                </button>
              </>
            ) : (
              <span className="muted">Only an administrator can authorise payments.</span>
            )}
          </div>
        </div>

        {loading ? <Loading label="Loading the queue" />
          : !rows.length ? (
            <Empty title="Nothing waiting for authorisation"
                   hint={applied === EMPTY_FILTERS
                     ? "Every collected payment has been signed off."
                     : "No entries match these filters. Try widening them."} />
          ) : (
            <div className="table-wrap">
              <table className="tbl auth-table">
                <thead>
                  <tr>
                    <th className="tick no-print">
                      <input type="checkbox" checked={allOnPageSelected}
                             aria-label="Select every row on this page"
                             onChange={toggleAll} />
                    </th>
                    <th>#</th><th>Name</th><th>Username</th><th>Flat No.</th>
                    <th>Building</th><th>Area</th><th>Zone</th><th>Mode</th>
                    <th>Details</th><th>Receipt No</th>
                    <th className="num">Amount</th><th className="num">Discount</th>
                    <th className="num">Outstanding</th><th>Receipt Date</th>
                    <th>Agent</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={row.id}
                        className={selected.has(row.id) ? "is-selected" : undefined}>
                      <td className="tick no-print">
                        <input type="checkbox" checked={selected.has(row.id)}
                               aria-label={`Select ${row.name}'s payment of ${row.amount}`}
                               onChange={() => toggle(row.id)} />
                      </td>
                      <td>{((meta?.page || 1) - 1) * (meta?.per_page || 50) + index + 1}</td>
                      <td>
                        <Link to={`/customers/${row.customer_id}`}>{row.name || "—"}</Link>
                      </td>
                      <td className="mono">{row.username || "—"}</td>
                      <td>{row.flat_no || "—"}</td>
                      <td>{row.building || "—"}</td>
                      <td>{row.area || "—"}</td>
                      <td>{row.zone || "—"}</td>
                      <td>{row.mode || "—"}</td>
                      <td className="details">{row.details || "—"}</td>
                      <td className="mono">{row.receipt_no}</td>
                      <td className="num">{inr(row.amount)}</td>
                      <td className="num">{row.discount ? inr(row.discount) : "—"}</td>
                      <td className={`num ${row.outstanding > 0 ? "due" : ""}`}>
                        {row.outstanding ? inr(row.outstanding) : "—"}
                      </td>
                      <td>{fmtDate(row.receipt_date)}</td>
                      <td>{row.agent || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

        <Pager meta={meta} onPage={setPage} />
      </section>
    </section>
  );
}

function Field({ label, children }) {
  return <label className="search-field"><span>{label}</span>{children}</label>;
}

function Select({ label, value, options, onChange }) {
  return (
    <Field label={label}>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">-Select-</option>
        {(options || []).map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    </Field>
  );
}
