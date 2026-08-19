import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useDebounced, useFetch } from "../api/useFetch";
import {
  Empty, ErrorNote, Loading, Pager, ScrollArrows, StatusPill, fmtDate, inr, railFor,
} from "../components/ui";

export default function Invoices() {
  const [search, setSearch] = useState("");
  // Seed from the URL so the dashboard drill-downs land pre-filtered and the
  // view stays shareable / refresh-safe.
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState(() => params.get("status") || "");
  const from = params.get("from") || "";
  const to = params.get("to") || "";
  const label = params.get("label") || "";
  const [page, setPage] = useState(1);
  const q = useDebounced(search);

  const { data, meta, loading, error, refetch } =
    useFetch("/invoices", { q, status, from: from || undefined, to: to || undefined, page });

  return (
    <>
      {(from || to || label) && (
        <div className="bulk-bar" role="status" style={{ borderRadius: 10 }}>
          <span>
            Showing <strong>{label || "invoices"}</strong>
            {from && to ? ` for ${from} to ${to}` : ""}
          </span>
          <div className="bulk-actions">
            <button className="btn sm" onClick={() => { setStatus(""); setParams({}, { replace: true }); }}
                    style={{ borderRadius: 6 }}>
              Clear filter
            </button>
          </div>
        </div>
      )}

      <div className="page-heading">
        <div>
          <h1>Invoices</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: 2 }}>
            Manage invoices and track payment status.
          </p>
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: "1.1rem", flexWrap: "wrap" }}>
        <input
          className="input grow"
          placeholder="Search invoice number, customer name or mobile"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          style={{ flex: 1, minWidth: 200, padding: "8px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }}
        />
        <select className="select" value={status}
          onChange={(e) => { setStatus(e.target.value); setPage(1); }}
          style={{ width: 160, padding: "8px 12px", borderRadius: 8, border: "1px solid #d0d5dd", fontSize: "0.88rem" }}>
          <option value="">All statuses</option>
          {["draft", "sent", "paid", "overdue", "cancelled"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      <ErrorNote error={error} onRetry={refetch} />

      <div className="card" style={{ borderRadius: 12, overflow: "hidden" }}>
        <ScrollArrows>
          {loading ? <Loading label="Loading invoices" />
            : !data?.length ? <Empty title="No invoices match" hint="Try clearing the filters." />
            : (
              <table className="data">
                <thead>
                  <tr><th>Invoice</th><th>Customer</th><th>Issued</th><th>Due</th>
                    <th className="right">Amount</th><th className="right">Paid</th>
                    <th className="right">Balance</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {data.map((i) => (
                    <tr key={i.id} className={railFor("invoice", i.status)}>
                      <td><Link to={`/invoices/${i.id}`} className="num">{i.invoice_no}</Link></td>
                      <td>
                        <Link to={`/customers/${i.customer_id}`}>{i.customer_name}</Link>
                        <div className="num" style={{ fontSize: 12, color: "#94a3b8" }}>{i.customer_mobile}</div>
                      </td>
                      <td className="num">{fmtDate(i.issue_date)}</td>
                      <td className="num">{fmtDate(i.due_date)}</td>
                      <td className="right num">{i.status === 'cancelled' ? '₹0' : inr(i.total_amount)}</td>
                      <td className="right num">{i.status === 'cancelled' ? '₹0' : inr(i.paid_amount)}</td>
                      <td className="right num">
                        <strong style={{ color: i.balance > 0 && i.status !== 'cancelled' ? "#dc2626" : "inherit" }}>
                          {i.status === 'cancelled' ? '₹0' : inr(i.balance)}
                        </strong>
                      </td>
                      <td><StatusPill value={i.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </ScrollArrows>
        <Pager meta={meta} onPage={setPage} />
      </div>
    </>
  );
}
