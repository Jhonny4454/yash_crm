import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useDebounced, useFetch } from "../api/useFetch";
import {
  Empty, ErrorNote, Loading, Pager, StatusPill, fmtDate, inr, railFor,
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
        <div className="bulk-bar" role="status">
          <span>
            Showing <strong>{label || "invoices"}</strong>
            {from && to ? ` for ${from} to ${to}` : ""}
          </span>
          <div className="bulk-actions">
            <button className="btn sm" onClick={() => { setStatus(""); setParams({}, { replace: true }); }}>
              Clear filter
            </button>
          </div>
        </div>
      )}

      <div className="toolbar">
        <input
          className="input grow"
          placeholder="Search invoice number, customer name or mobile"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        />
        <select className="select" style={{ width: 160 }} value={status}
          onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All statuses</option>
          {["draft", "sent", "paid", "overdue", "cancelled"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      <ErrorNote error={error} onRetry={refetch} />

      <div className="card">
        <div className="table-wrap">
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
                        <div className="num" style={{ fontSize: 12, color: "var(--muted)" }}>{i.customer_mobile}</div>
                      </td>
                      <td className="num">{fmtDate(i.issue_date)}</td>
                      <td className="num">{fmtDate(i.due_date)}</td>
                      <td className="right num">{inr(i.total_amount)}</td>
                      <td className="right num">{inr(i.paid_amount)}</td>
                      <td className="right num">
                        <strong style={{ color: i.balance > 0 ? "var(--danger)" : "inherit" }}>
                          {inr(i.balance)}
                        </strong>
                      </td>
                      <td><StatusPill value={i.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
        <Pager meta={meta} onPage={setPage} />
      </div>
    </>
  );
}
