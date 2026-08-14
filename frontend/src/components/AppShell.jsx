import { NavLink, Outlet, useNavigate } from "react-router-dom";
import Breadcrumbs from "./Breadcrumbs";
import { useAuth } from "../context/AuthContext";
import "../styles/AppShell.css";

const ADMIN_NAV = [
  ["Dashboard", "/", "▦"], ["Customers", "/customers", "◉"],
  ["Plans", "/plans", "⌁"], ["Invoices", "/invoices", "▤"],
  ["Payments", "/payments", "₹"], ["Inventory", "/inventory/products", "□"],
  ["Expenses", "/expenses", "◫"], ["Staff", "/staff", "♙"],
  ["Reports", "/reports/plan-expiry", "◷"], ["Settings", "/settings", "⚙"],
];
const CUSTOMER_NAV = [
  ["Overview", "/customer", "▦"], ["Invoices", "/customer/invoices", "▤"],
  ["Payments", "/customer/payments", "₹"], ["Renew or change", "/customer/plans", "⌁"],
  ["Notifications", "/customer/notifications", "●"],
];

export default function AppShell({ audience }) {
  const { user, company, signOut } = useAuth();
  const navigate = useNavigate();
  const nav = audience === "customer" ? CUSTOMER_NAV : ADMIN_NAV;

  async function logout() {
    await signOut();
    navigate(audience === "customer" ? "/customer/login" : "/login", { replace: true });
  }

  return <div className="app-shell">
    <aside className="app-sidebar">
      <NavLink to={audience === "customer" ? "/customer" : "/"} className="brand">
        {company?.logo_url ? <img src={company.logo_url} alt="" /> : <span className="brand-mark">Y</span>}
        <span>{company?.name || "YASH Internet"}</span>
      </NavLink>
      <nav aria-label="Primary navigation">
        {nav.map(([label, path, icon]) => <NavLink key={path} end={path === "/" || path === "/customer"} to={path}>
          <span aria-hidden="true">{icon}</span>{label}
        </NavLink>)}
      </nav>
    </aside>
    <section className="app-workspace">
      <header className="app-topbar">
        <div><span className="eyebrow">{audience === "customer" ? "Customer portal" : "Operations centre"}</span><strong>{company?.name || "YASH Internet Services"}</strong></div>
        <div className="account-menu">{audience === "customer" ? <NavLink to="/customer/profile">{user?.full_name || user?.username || user?.mobile}</NavLink> : <NavLink to="/profile" title="My profile">{user?.full_name || user?.username || user?.mobile}</NavLink>}<button className="btn sm" onClick={logout}>Sign out</button></div>
      </header>
      <main className="app-main">
        {audience !== "customer" && <Breadcrumbs />}
        <Outlet />
      </main>
    </section>
  </div>;
}
