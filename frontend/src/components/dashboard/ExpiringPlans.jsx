import { Link } from "react-router-dom";
import { useFetch } from "../../api/useFetch";
import { Empty, ErrorNote, Loading, fmtDate } from "../ui";

export default function ExpiringPlans() {
  // Using the same endpoint as DuePayments – now returns both due payments and expiring plans
  const { data, loading, error, refetch } = useFetch("/api/v1/dashboard/recent");
  const rows = data?.expiring_plans || [];

  return (
    <div className="card panel-fill">
      <div className="card-head">
        <h2>Expiring in 7 days</h2>
        <Link to="/reports/plan-expiry" className="panel-link">Full report</Link>
      </div>
      <div className="card-body">
        {loading ? <Loading />
          : error ? <ErrorNote error={error} onRetry={refetch} />
          : !rows.length ? <Empty title="Nothing expiring" hint="No plans end in the next week." />
          : (
            <ul className="mini-list">
              {rows.map((p) => (
                <li key={p.id}>
                  <div className="mini-main">
                    <Link to={`/customers/${p.customer_id}`}>{p.customer_name}</Link>
                    <span className="num mini-meta">
                      {p.plan?.name} · {fmtDate(p.end_date)}
                    </span>
                  </div>
                  <span className={`pill ${p.days_remaining <= 2 ? "danger" : "warn"}`}>
                    {p.days_remaining}d
                  </span>
                </li>
              ))}
            </ul>
          )}
      </div>
    </div>
  );
}