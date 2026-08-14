import { Link } from "react-router-dom";

const ACTIONS = [
  { label: "Add Customer", to: "/customers/add", icon: "fa-user-plus", primary: true },
  { label: "New Invoice", to: "/invoices", icon: "fa-file-invoice" },
  { label: "Record Payment", to: "/payments", icon: "fa-indian-rupee-sign" },
  { label: "Authorisations", to: "/authorizations", icon: "fa-check-double" },
  { label: "Expiry Report", to: "/reports/plan-expiry", icon: "fa-hourglass-half" },
];

export default function QuickActions() {
  return (
    <div className="toolbar no-print">
      {ACTIONS.map((a) => (
        <Link 
          key={a.to} 
          to={a.to} 
          className={`btn sm ${a.primary ? "primary" : ""}`}
        >
          <i className={`fas ${a.icon}`} aria-hidden="true" />
          {a.label}
        </Link>
      ))}
    </div>
  );
}