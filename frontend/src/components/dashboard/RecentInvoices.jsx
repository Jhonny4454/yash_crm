import { Link } from "react-router-dom";
import { useFetch } from "../../api/useFetch";
import { Empty, ErrorNote, Loading, StatusPill, fmtDate, inr } from "../ui";

export default function RecentInvoices() {
  // ✅ Use the same unified endpoint as other recent widgets
  const { data, loading, error, refetch } = useFetch("/api/v1/dashboard/recent");
  const rows = data?.recent_invoices || [];

  return (
    <div className="card panel-fill">
      <div className="card-head">
        <h2>Recent invoices</h2>
        <Link to="/invoices" className="panel-link">View all</Link>
      </div>
      <div className="card-body">
        {loading ? <Loading />
          : error ? <ErrorNote error={error} onRetry={refetch} />
          : !rows.length ? <Empty title="No invoices yet" />
          : (
            <ul className="mini-list">
              {rows.map((i) => (
                <li key={i.id}>
                  <div className="mini-main">
                    <Link to={`/invoices/${i.id}`} className="num">{i.invoice_no}</Link>
                    <span className="mini-meta">
                      {i.customer_name} · {fmtDate(i.issue_date)}
                    </span>
                  </div>
                  <div className="mini-right">
                    <strong className="num">{inr(i.total_amount)}</strong>
                    <StatusPill value={i.status} />
                  </div>
                </li>
              ))}
            </ul>
          )}
      </div>
    </div>
  );
}