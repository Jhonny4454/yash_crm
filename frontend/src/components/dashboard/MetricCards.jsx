import { useFetch } from "../../api/useFetch";
import { ErrorNote, Loading, inr, inrShort } from "../ui";

export default function MetricCards() {
  // ✅ Point to the new JSON endpoint
  const { data, loading, error, refetch } = useFetch("/api/v1/dashboard");

  if (loading) return <Loading label="Loading figures" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data) return null;

  const { customers, plans, revenue, payments } = data;

  const cards = [
    { label: "Active customers", value: customers.active,
      sub: `${customers.total} total · ${customers.inactive} inactive`,
      tone: "accent", icon: "fa-users" },
    { label: "Collected this month", value: inrShort(revenue.month),
      sub: `${inr(revenue.today)} today`, tone: "signal", icon: "fa-indian-rupee-sign" },
    { label: "Outstanding", value: inrShort(revenue.outstanding),
      sub: "Unpaid and overdue", tone: "danger", icon: "fa-file-circle-exclamation" },
    { label: "Awaiting authorisation", value: payments.pending_authorization,
      sub: "Recorded, not yet reviewed", tone: "warn", icon: "fa-check-double" },
    { label: "Expiring in 7 days", value: plans.expiring_7d,
      sub: `${plans.active} plans running`, tone: "warn", icon: "fa-hourglass-half" },
    { label: "Past end date", value: plans.expired,
      sub: "Need renewal or suspension", tone: "danger", icon: "fa-triangle-exclamation" },
  ];

  return (
    <div className="metric-grid">
      {cards.map((c) => (
        <div key={c.label} className={`metric ${c.tone}`}>
          <div className="metric-top">
            <span className="metric-label">{c.label}</span>
            <i className={`fas ${c.icon}`} aria-hidden="true" />
          </div>
          <div className="metric-value num">{c.value}</div>
          <div className="metric-sub num">{c.sub}</div>
        </div>
      ))}
    </div>
  );
}