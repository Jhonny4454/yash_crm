import { Link } from "react-router-dom";
import { useFetch } from "../../api/useFetch";
import { Empty, ErrorNote, Loading, fmtDate, inr } from "../ui";

export default function DuePayments() {
  // Changed endpoint to the dedicated JSON API route
  const { data, loading, error, refetch } = useFetch("/api/v1/dashboard/recent");
  const rows = data?.due_payments || [];
  const today = new Date().setHours(0, 0, 0, 0);

  return (
    <div className="card panel-fill">
      <div className="card-head">
        <h2>Due payments</h2>
        <Link to="/invoices?status=overdue" className="panel-link">View all</Link>
      </div>
      <div className="card-body">
        {loading ? <Loading />
          : error ? <ErrorNote error={error} onRetry={refetch} />
          : !rows.length ? <Empty title="Nothing outstanding" hint="Every invoice is settled." />
          : (
            <ul className="mini-list">
              {rows.map((i) => {
                const overdue = new Date(i.due_date).setHours(0, 0, 0, 0) < today;
                return (
                  <li key={i.id}>
                    <div className="mini-main">
                      <Link to={`/customers/${i.customer_id}`}>{i.customer_name}</Link>
                      <span className="num mini-meta">
                        {i.invoice_no} · due {fmtDate(i.due_date)}
                      </span>
                    </div>
                    <strong className="num"
                            style={{ color: overdue ? "var(--danger)" : "inherit" }}>
                      {inr(i.balance)}
                    </strong>
                  </li>
                );
              })}
            </ul>
          )}
      </div>
    </div>
  );
}