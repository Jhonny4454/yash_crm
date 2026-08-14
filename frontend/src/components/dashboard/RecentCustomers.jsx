import { Link } from "react-router-dom";
import { useFetch } from "../../api/useFetch";
import { Empty, ErrorNote, Loading } from "../ui";

export default function RecentCustomers() {
  // ✅ Use the same unified endpoint as DuePayments and ExpiringPlans
  const { data, loading, error, refetch } = useFetch("/api/v1/dashboard/recent");

  return (
    <div className="card panel-fill">
      <div className="card-head">
        <h2>Recent customers</h2>
        <Link to="/customers" className="panel-link">View all</Link>
      </div>
      <div className="card-body">
        {loading ? <Loading />
          : error ? <ErrorNote error={error} onRetry={refetch} />
          : !data?.recent_customers?.length ? <Empty title="No customers yet" />
          : (
            <ul className="mini-list">
              {data.recent_customers.map((c) => (
                <li key={c.id}>
                  <div className="mini-main">
                    <Link to={`/customers/${c.id}`}>{c.full_name}</Link>
                    <span className="num mini-meta">{c.mobile}</span>
                  </div>
                  <span className={`pill ${c.is_active ? "ok" : "idle"}`}>
                    {c.is_active ? "active" : "inactive"}
                  </span>
                </li>
              ))}
            </ul>
          )}
      </div>
    </div>
  );
}