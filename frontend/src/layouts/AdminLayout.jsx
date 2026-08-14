import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import Sidebar from "../components/Sidebar";
import DashboardHeader from "../components/dashboard/DashboardHeader"; // ✅ Correct path
import { useAuth } from "../context/AuthContext";
import { TITLES } from "../components/menu";
import Breadcrumbs from "../components/Breadcrumbs";
import "../styles/Dashboard.css";

export default function AdminLayout() {
  const { user, company, signOut } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("unicrm.sidebar") === "1"
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const [search, setSearch] = useState("");

  useEffect(() => {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    localStorage.setItem("unicrm.sidebar", collapsed ? "1" : "0");
  }, [collapsed]);

  useEffect(() => setMobileOpen(false), [pathname]);

  const title =
    TITLES[pathname] ||
    (pathname.startsWith("/customers/") && "Customer") ||
    (pathname.startsWith("/invoices/") && "Invoice") ||
    "Dashboard";

  function submitSearch(e) {
    e.preventDefault();
    const q = search.trim();
    if (q) navigate(`/customers?q=${encodeURIComponent(q)}`);
  }

  function handleSignOut() {
    signOut();
    navigate("/login", { replace: true });
  }

  return (
    <>
      {mobileOpen && <div className="scrim" onClick={() => setMobileOpen(false)} />}

      {/* ✅ Now passing the 'collapsed' prop so the sidebar logo resizes automatically */}
      <Sidebar
        company={company}
        mobileOpen={mobileOpen}
        collapsed={collapsed}
        onSignOut={handleSignOut}
      />

      <header className="top-bar">
        <div className="top-bar-left">
          <button
            className="sidebar-toggle"
            onClick={() => {
              if (window.innerWidth <= 991) setMobileOpen((v) => !v);
              else setCollapsed((v) => !v);
            }}
            aria-label="Toggle sidebar"
          >
            <i className="fas fa-bars" />
          </button>
          <h1>{title}</h1>
        </div>

        <div className="top-bar-right">
          <form className="search-form" onSubmit={submitSearch}>
            <input
              type="search"
              placeholder="Search customers…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search customers"
            />
            <button type="submit" aria-label="Search">
              <i className="fas fa-search" />
            </button>
          </form>

          <div className="user-greeting">
            <span>
              <i className="fas fa-user-circle" style={{ marginRight: 6 }} />
              {user?.full_name || user?.username}
            </span>
            <button onClick={handleSignOut} title="Sign out">
              <i className="fas fa-sign-out-alt" />
            </button>
          </div>
        </div>
      </header>

      <div className="main-content">
        <div className="content-wrapper">
          
          {/* The greeting header belongs to the dashboard, not every page. */}
          {pathname === "/" ? <DashboardHeader collapsed={collapsed} /> : <Breadcrumbs />}

          <Outlet />
        </div>
      </div>

      <footer className="app-footer">
        <span>
          &copy; {new Date().getFullYear()} {company?.name || "YASH Internet Services"}
        </span>
        <span>Developed by Sumedh Developer</span>
      </footer>
    </>
  );
}