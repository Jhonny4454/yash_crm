import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { get } from "../api/client";
import { Empty, ErrorNote, fmtDate, inr, Loading, ScrollArrows } from "../components/ui";
import "../styles/Forms.css";

/**
 * Customer ledger - the running statement from customers/ledger.html.
 *
 * Reachable two ways:
 *   /customers/ledger        -> pick a customer, then the statement loads
 *   /customers/:id/ledger    -> straight to that customer's statement
 *
 * The picker keeps the chosen customer in the query string so the page can be
 * refreshed, bookmarked and shared without losing context.
 */
export default function CustomerLedger() {
  const { id: routeId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = routeId || searchParams.get("customer") || "";

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>Customer ledger</h1>
          <p>Invoices debit the account, payments credit it. The balance is the amount outstanding.</p>
        </div>
      </div>

      {!routeId && (
        <CustomerPicker
          value={selectedId}
          onChange={(value) =>
            setSearchParams(value ? { customer: value } : {}, { replace: true })
          }
        />
      )}

      {selectedId ? (
        <Statement customerId={selectedId} />
      ) : (
        <Empty
          title="Choose a customer"
          hint="Search above to open a customer's running statement."
        />
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */

function CustomerPicker({ value, onChange }) {
  const [term, setTerm] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const debounce = useRef(null);

  useEffect(() => {
    if (term.trim().length < 2) {
      setResults([]);
      return undefined;
    }

    // One request per pause in typing, not one per keystroke.
    clearTimeout(debounce.current);
    let cancelled = false;
    setSearching(true);

    debounce.current = setTimeout(() => {
      get("/customers", { q: term.trim(), per_page: 10 })
        .then((payload) => {
          if (!cancelled) setResults(payload?.data || []);
        })
        .catch(() => { if (!cancelled) setResults([]); })
        .finally(() => { if (!cancelled) setSearching(false); });
    }, 300);

    return () => {
      cancelled = true;
      clearTimeout(debounce.current);
    };
  }, [term]);

  return (
    <section className="panel stack">
      <label>
        Find a customer
        <input
          type="search"
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          placeholder="Name, mobile, username or reference ID"
          autoComplete="off"
        />
      </label>

      {searching && <small>Searching…</small>}

      {results.length > 0 && (
        <ul className="picker-results">
          {results.map((customer) => (
            <li key={customer.id}>
              <button
                type="button"
                className={String(customer.id) === String(value) ? "is-selected" : undefined}
                onClick={() => { onChange(String(customer.id)); setTerm(""); setResults([]); }}
              >
                <strong>{customer.full_name || `${customer.first_name} ${customer.last_name}`}</strong>
                <span>{customer.mobile}{customer.zone ? ` · ${customer.zone}` : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {term.trim().length >= 2 && !searching && results.length === 0 && (
        <small>No customers matched “{term.trim()}”.</small>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */

function Statement({ customerId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    get(`/customers/${customerId}/ledger`)
      .then((payload) => { if (!cancelled) setData(payload?.data || payload); })
      .catch((err) => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [customerId, reloadKey]);

  const entries = useMemo(() => {
    const rows = data?.entries || [];
    return filter === "all" ? rows : rows.filter((row) => row.type === filter);
  }, [data, filter]);

  // Totals come from the API. Deriving the balance here by reading the last
  // row was wrong: entries arrive newest-first, so the "last" row holds the
  // OLDEST balance - the screen printed an out-of-date figure as the total.
  const totals = useMemo(() => ({
    debit: Number(data?.total_debit || 0),
    credit: Number(data?.total_credit || 0),
    balance: Number(data?.closing_balance || 0),
  }), [data]);

  function exportCsv() {
    const header = ["Date", "Type", "Reference", "Description", "Debit", "Credit", "Balance"];
    const lines = [header.join(",")];
    for (const row of entries) {
      lines.push([
        row.date, row.type, row.reference,
        `"${String(row.description || "").replace(/"/g, '""')}"`,
        row.debit, row.credit, row.balance,
      ].join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = `ledger-${data?.customer?.id || customerId}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(href);
  }

  if (loading) return <Loading label="Loading statement" />;
  if (error) return <ErrorNote error={error} onRetry={() => setReloadKey((k) => k + 1)} />;
  if (!data) return <Empty title="No statement available" />;

  const customer = data.customer || {};

  return (
    <>
      <section className="panel ledger-head">
        <div>
          <h2>
            <Link to={`/customers/${customer.id || customerId}`}>
              {customer.full_name || `${customer.first_name || ""} ${customer.last_name || ""}`.trim() || "Customer"}
            </Link>
          </h2>
          <p>{customer.mobile}{customer.zone ? ` · ${customer.zone}` : ""}</p>
        </div>
        <div className="ledger-totals">
          <div><span>Total billed</span><strong>{inr(totals.debit)}</strong></div>
          <div><span>Total paid</span><strong>{inr(totals.credit)}</strong></div>
          <div className={totals.balance > 0 ? "is-due" : "is-clear"}>
            <span>Balance</span><strong>{inr(totals.balance)}</strong>
          </div>
          {Number(data.wallet_balance) > 0 && (
            <div className="is-clear">
              <span>Wallet credit</span><strong>{inr(data.wallet_balance)}</strong>
            </div>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="table-toolbar">
          <div className="filter-chips" role="group" aria-label="Filter entries">
            {[["all", "All"], ["invoice", "Invoices"], ["payment", "Payments"],
              ["wallet", "Wallet"]].map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={filter === key ? "chip is-active" : "chip"}
                onClick={() => setFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <button type="button" className="btn sm" onClick={exportCsv} disabled={!entries.length}>
            Export CSV
          </button>
        </div>

        <ScrollArrows>
          {entries.length === 0 ? (
            <Empty
              title="No entries"
              hint={filter === "all"
                ? "This customer has no invoices or payments yet."
                : filter === "wallet"
                  ? "No wallet credits have been applied to this account."
                  : `No ${filter}s recorded for this customer.`}
            />
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>Date</th><th>Type</th><th>Reference</th><th>Description</th>
                  <th className="num">Debit</th><th className="num">Credit</th><th className="num">Balance</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((row, index) => (
                  <tr key={`${row.type}-${row.reference}-${index}`}>
                    <td>{fmtDate(row.date)}</td>
                    <td className="cap">{row.type}</td>
                    <td className="mono">{row.reference}</td>
                    <td>{row.description}</td>
                    <td className="num">{row.debit ? inr(row.debit) : "—"}</td>
                    <td className="num">{row.credit ? inr(row.credit) : "—"}</td>
                    <td className="num"><strong>{inr(row.balance)}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </ScrollArrows>
      </section>
    </>
  );
}
