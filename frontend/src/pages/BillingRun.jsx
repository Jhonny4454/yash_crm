import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { Empty, ErrorNote, fmtDate, inr, Loading, Pager, readableError } from "../components/ui";
import "../styles/BillingRun.css";
import MoneyInput from "../components/MoneyInput";

/**
 * Generate Invoice - the monthly billing run.
 *
 * The screen is built around one idea: the operator must see the bill before
 * they raise it. So the list is a preview of real figures, not a filter
 * summary, and every excluded customer says why it was excluded rather than
 * quietly vanishing from the count.
 *
 * Customers who cannot be billed are shown greyed out rather than hidden. An
 * operator who filtered to a zone of 40 and sees 38 rows needs to know which
 * two are missing and why - a shorter list is not an answer.
 */

const today = () => new Date().toISOString().slice(0, 10);

const inDays = (days) => {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
};

const EMPTY = {
  from: "", to: "", zone: "", area: "", building: "", locality: "",
  plan_id: "", q: "",
};

/**
 * The screen opens on "who runs out in the next week", not on the whole
 * customer base. The window is set here rather than defaulted on the server
 * so the operator can SEE it in the date field - a hidden default filter is
 * how someone ends up believing they billed everybody when they billed a
 * subset, or bills the whole base a month early.
 */
const INITIAL = { ...EMPTY, to: inDays(7) };

export default function BillingRun() {
  const { isAdmin } = useAuth();
  const { toast, confirm } = useToast();

  const [filters, setFilters] = useState(INITIAL);
  const [applied, setApplied] = useState(INITIAL);
  const [issueDate, setIssueDate] = useState(today);
  const [dueDays, setDueDays] = useState(15);
  const [sendMessage, setSendMessage] = useState(false);
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [totals, setTotals] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [options, setOptions] = useState(null);

  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    const params = Object.fromEntries(
      Object.entries(applied).filter(([, value]) => value !== ""),
    );
    return get("/billing/run/preview",
               { ...params, page, issue_date: issueDate, due_days: dueDays })
      .then((response) => {
        const data = Array.isArray(response?.data) ? response.data : [];
        setRows(data);
        setMeta(response?.meta || null);
        setTotals(response?.totals || null);
        // Drop anything no longer billable from the selection, so Generate
        // never posts a customer the preview has since excluded.
        setSelected((previous) => {
          const billable = new Set(
            data.filter((r) => r.billable).map((r) => r.customer_plan_id));
          return new Set([...previous].filter((id) => billable.has(id)));
        });
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [applied, page, issueDate, dueDays]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    get("/billing/run/filters")
      .then((response) => {
        const data = response?.data ?? response;
        setOptions(data);
        if (data?.default_due_days) setDueDays(data.default_due_days);
      })
      .catch(() => setOptions(null));
  }, []);

  const billableRows = useMemo(() => rows.filter((r) => r.billable), [rows]);
  const allSelected = billableRows.length > 0
    && billableRows.every((r) => selected.has(r.customer_plan_id));

  const selectedTotal = useMemo(
    () => rows.filter((r) => selected.has(r.customer_plan_id))
      .reduce((sum, r) => sum + Number(r.net_amount || 0), 0),
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
      billableRows.forEach((r) => {
        if (allSelected) next.delete(r.customer_plan_id);
        else next.add(r.customer_plan_id);
      });
      return next;
    });
  }

  async function generate() {
    const ids = [...selected];
    if (!ids.length) return;

    const confirmed = await confirm({
      title: `Raise ${ids.length} invoice${ids.length > 1 ? "s" : ""}?`,
      message: `${inr(selectedTotal)} will be billed, dated ${fmtDate(issueDate)} `
        + `and due in ${dueDays} days.`
        + (sendMessage ? " A bill message goes out to each customer." : "")
        + " Invoices cannot be deleted once raised, only cancelled.",
      confirmLabel: `Generate ${ids.length}`,
    });
    if (!confirmed) return;

    setBusy(true);
    setResult(null);
    try {
      const response = await post("/billing/run/generate", {
        customer_plan_ids: ids,
        issue_date: issueDate,
        due_days: Number(dueDays),
        send_message: sendMessage,
      });
      const data = response?.data ?? response;
      setResult(data);
      toast.success(`${data.created_count} invoice(s) raised, ${inr(data.total_amount)} billed.`);
      setSelected(new Set());
      await load();
    } catch (runError) {
      toast.error(runError.detail || readableError(runError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page billing-run">
      <div className="page-heading">
        <div>
          <h1>Generate invoices</h1>
          <p>
            Pick the customers due for billing, check the figures, then raise
            them together. Anyone already invoiced for the period is excluded.
          </p>
        </div>
      </div>

      <form className="panel-card run-panel"
            onSubmit={(event) => { event.preventDefault(); setPage(1); setApplied(filters); }}>
        <div className="panel-head">Select customers</div>
        <div className="run-grid">
          <Field label="Expiring from">
            <input type="date" value={filters.from}
                   onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
          </Field>
          <Field label="Expiring to">
            <input type="date" value={filters.to}
                   onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
          </Field>
          <Choice label="Zone" value={filters.zone} options={options?.zones}
                  onChange={(v) => setFilters({ ...filters, zone: v })} />
          <Choice label="Area" value={filters.area} options={options?.areas}
                  onChange={(v) => setFilters({ ...filters, area: v })} />
          <Choice label="Building" value={filters.building} options={options?.buildings}
                  onChange={(v) => setFilters({ ...filters, building: v })} />
          <Field label="Plan">
            <select value={filters.plan_id}
                    onChange={(e) => setFilters({ ...filters, plan_id: e.target.value })}>
              <option value="">All plans</option>
              {(options?.plans || []).map((plan) => (
                <option key={plan.id} value={plan.id}>{plan.name}</option>
              ))}
            </select>
          </Field>
          <Field label="Search">
            <input type="search" value={filters.q} placeholder="Name, username or mobile"
                   onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
          </Field>
          <div className="run-actions">
            <button type="submit" className="btn primary">Search</button>
            <button type="button" className="btn"
                    onClick={() => { setFilters(INITIAL); setApplied(INITIAL); setPage(1); }}>
              Reset
            </button>
          </div>
        </div>
      </form>

      <div className="panel-card run-panel">
        <div className="panel-head">Invoice settings</div>
        <div className="run-grid">
          <Field label="Invoice date">
            <input type="date" value={issueDate}
                   onChange={(e) => setIssueDate(e.target.value)} />
          </Field>
          <Field label="Payable within (days)">
            <MoneyInput max={180} value={dueDays}
                        onChange={(e) => setDueDays(e.target.value)} />
          </Field>
          <p className="run-note">
            Raising a bill does not extend the connection. The plan's expiry
            moves when the customer pays.
          </p>
          <label className="run-check">
            <input type="checkbox" checked={sendMessage}
                   onChange={(e) => setSendMessage(e.target.checked)} />
            <span>Send each customer their bill message</span>
          </label>
        </div>
      </div>

      <ErrorNote error={error} onRetry={load} />

      {result && <RunResult result={result} onDismiss={() => setResult(null)} />}

      <section className="panel-card">
        <div className="run-bar">
          <div>
            {totals && (
              <>
                <strong>{totals.billable}</strong> ready to bill
                {totals.blocked > 0 && <> · <span className="muted">{totals.blocked} excluded</span></>}
                {" · "}<strong>{inr(totals.amount)}</strong> on this page
              </>
            )}
          </div>
          <div className="row-actions">
            <span className="selection-count">
              {selected.size
                ? `${selected.size} selected · ${inr(selectedTotal)}`
                : "Nothing selected"}
            </span>
            {isAdmin ? (
              <button type="button" className="btn primary"
                      disabled={!selected.size || busy} onClick={generate}>
                {busy ? "Generating…" : "Generate invoices"}
              </button>
            ) : (
              <span className="muted">Only an administrator can raise invoices.</span>
            )}
          </div>
        </div>

        {loading ? <Loading label="Working out who is due" />
          : !rows.length ? (
            <Empty title="Nobody is due in that window"
                   hint="The list opens on plans expiring within seven days. Widen the expiry dates to see more." />
          ) : (
            <div className="table-wrap">
              <table className="tbl run-table">
                <thead>
                  <tr>
                    <th className="tick">
                      <input type="checkbox" checked={allSelected}
                             disabled={!billableRows.length}
                             aria-label="Select every billable customer on this page"
                             onChange={toggleAll} />
                    </th>
                    <th>Customer</th><th>Username</th><th>Zone</th><th>Plan</th>
                    <th>Expires</th><th>Bill period</th>
                    <th className="num">Amount</th><th className="num">Discount</th>
                    <th className="num">Net</th><th>Due</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const isSelected = selected.has(row.customer_plan_id);
                    return (
                      <tr key={row.customer_plan_id}
                          className={[!row.billable && "is-blocked",
                                      isSelected && "is-selected"]
                            .filter(Boolean).join(" ")}>
                        <td className="tick">
                          <input type="checkbox" checked={isSelected}
                                 disabled={!row.billable}
                                 aria-label={`Bill ${row.name}`}
                                 onChange={() => toggle(row.customer_plan_id)} />
                        </td>
                        <td>
                          <Link to={`/customers/${row.customer_id}`}>{row.name}</Link>
                          {!row.billable && (
                            <small className="blocked-why">{row.blocked_reason}</small>
                          )}
                        </td>
                        <td className="mono">{row.username || "—"}</td>
                        <td>{row.zone || "—"}</td>
                        <td>{row.plan_name || "—"}</td>
                        <td>{fmtDate(row.current_expiry)}</td>
                        <td className="period">
                          {fmtDate(row.period_start)} – {fmtDate(row.period_end)}
                        </td>
                        <td className="num">{inr(row.amount)}</td>
                        <td className="num">{row.discount ? inr(row.discount) : "—"}</td>
                        <td className="num"><strong>{inr(row.net_amount)}</strong></td>
                        <td>{fmtDate(row.due_date)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

        <Pager meta={meta} onPage={setPage} />
      </section>
    </section>
  );
}

function RunResult({ result, onDismiss }) {
  return (
    <section className="panel-card run-result">
      <div className="panel-head">
        Run complete
        <button type="button" className="btn sm" onClick={onDismiss}>Dismiss</button>
      </div>
      <div className="run-result-body">
        <p>
          <strong>{result.created_count}</strong> invoice(s) raised for a total of{" "}
          <strong>{inr(result.total_amount)}</strong>, dated {fmtDate(result.issue_date)}.
        </p>

        {result.created?.length > 0 && (
          <details>
            <summary>Show the {result.created.length} invoice(s) raised</summary>
            <ul className="run-list">
              {result.created.map((entry) => (
                <li key={entry.invoice_id}>
                  <Link to={`/invoices/${entry.invoice_id}`}>{entry.invoice_no}</Link>
                  {" — "}{entry.name} · {inr(entry.net_amount)}
                  {entry.message_status && (
                    <span className={`msg-${entry.message_status.split(":")[0]}`}>
                      {" "}({entry.message_status})
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}

        {/* Skipped rows are shown open, not folded away. A run that quietly
            omitted 12 customers is the thing the operator most needs to see. */}
        {result.skipped?.length > 0 && (
          <div className="run-skipped">
            <strong>{result.skipped.length} were not billed:</strong>
            <ul className="run-list">
              {result.skipped.map((entry, index) => (
                <li key={`${entry.customer_plan_id}-${index}`}>
                  {entry.name || `Plan #${entry.customer_plan_id}`} — {entry.reason}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function Field({ label, children }) {
  return <label className="run-field"><span>{label}</span>{children}</label>;
}

function Choice({ label, value, options, onChange }) {
  return (
    <Field label={label}>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">All</option>
        {(options || []).map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    </Field>
  );
}
