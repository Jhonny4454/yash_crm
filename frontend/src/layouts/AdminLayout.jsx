import { useCallback, useEffect, useRef, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import Sidebar from "../components/Sidebar";
import DashboardHeader from "../components/dashboard/DashboardHeader";
import { useAuth } from "../context/AuthContext";
import { TITLES } from "../components/menu";
import Breadcrumbs from "../components/Breadcrumbs";
import ThemeToggle from "../components/ThemeToggle";
import "../styles/Dashboard.css";

const MOBILE_MAX = 991;

export default function AdminLayout() {
  const { user, company, signOut } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("unicrm.sidebar") === "1"
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const searchRef = useRef(null);

  const [quickAddOpen, setQuickAddOpen] = useState(false);
  const quickAddRef = useRef(null);
  const isAdmin = user?.role === "admin";

  useEffect(() => {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    localStorage.setItem("unicrm.sidebar", collapsed ? "1" : "0");
  }, [collapsed]);

  useEffect(() => {
    setMobileOpen(false);
    setSearchOpen(false);
    setQuickAddOpen(false);
  }, [pathname]);

  /* Stop the page behind the drawer from scrolling under your thumb. Android
     Chrome in particular will happily scroll the document while you are
     dragging a fixed overlay, which makes the menu feel broken. */
  useEffect(() => {
    if (!mobileOpen) return undefined;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, [mobileOpen]);

  /* Escape closes the drawer, same as tapping the scrim. */
  useEffect(() => {
    if (!mobileOpen) return undefined;
    const onKey = (event) => { if (event.key === "Escape") setMobileOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mobileOpen]);

  /* Growing past the mobile breakpoint has to close the drawer, or the app
     locks up: the scrim and the drawer's close button are both styled only
     inside `@media (max-width: 991px)`, so on a tablet rotated to landscape
     the scrim becomes an invisible static div nobody can click while
     `document.body.style.overflow` stays "hidden" - a staff portal that
     looks completely normal and will not scroll. */
  useEffect(() => {
    if (!mobileOpen) return undefined;
    const onResize = () => {
      if (window.innerWidth > MOBILE_MAX) setMobileOpen(false);
    };
    window.addEventListener("resize", onResize);
    window.addEventListener("orientationchange", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("orientationchange", onResize);
    };
  }, [mobileOpen]);

  useEffect(() => {
    if (searchOpen && searchRef.current) searchRef.current.focus();
  }, [searchOpen]);

  useEffect(() => {
    if (!quickAddOpen) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setQuickAddOpen(false); };
    const onPointer = (e) => {
      if (quickAddRef.current && !quickAddRef.current.contains(e.target)) setQuickAddOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [quickAddOpen]);

  const closeMobile = useCallback(() => setMobileOpen(false), []);

  const title =
    TITLES[pathname] ||
    (pathname.startsWith("/customers/") && "Customer") ||
    (pathname.startsWith("/invoices/") && "Invoice") ||
    "Dashboard";

  function submitSearch(event) {
    event.preventDefault();
    const q = search.trim();
    if (!q) return;
    setSearchOpen(false);
    navigate(`/customers?q=${encodeURIComponent(q)}`);
  }

  function handleSignOut() {
    signOut();
    navigate("/login", { replace: true });
  }

  function toggleNav() {
    if (window.innerWidth <= MOBILE_MAX) setMobileOpen((v) => !v);
    else setCollapsed((v) => !v);
  }

  return (
    <>
      {mobileOpen && <div className="scrim" onClick={closeMobile} aria-hidden="true" />}

      <Sidebar
        company={company}
        mobileOpen={mobileOpen}
        collapsed={collapsed}
        onSignOut={handleSignOut}
        onCloseMobile={closeMobile}
      />

      <header className={`top-bar${searchOpen ? " search-open" : ""}`}>
        <div className="top-bar-left">
          <button
            className="sidebar-toggle"
            onClick={toggleNav}
            aria-label={mobileOpen ? "Close menu" : "Open menu"}
            aria-expanded={mobileOpen}
          >
            <i className="fas fa-bars" />
          </button>
          <h1>{title}</h1>
        </div>

        <div className="top-bar-right">
          {/* On a phone the search box used to be hidden outright, which took
              the one thing staff use most - find a customer - off the screen.
              It is a button that opens a full-width row instead. */}
          <button
            type="button"
            className="sidebar-toggle search-trigger"
            onClick={() => setSearchOpen((v) => !v)}
            aria-label="Search customers"
            aria-expanded={searchOpen}
          >
            <i className={`fas fa-${searchOpen ? "times" : "search"}`} />
          </button>

          <form className="search-form" onSubmit={submitSearch} role="search">
            <input
              ref={searchRef}
              type="search"
              placeholder="Search customers…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search customers"
              enterKeyHint="search"
            />
            <button type="submit" aria-label="Search">
              <i className="fas fa-search" />
            </button>
          </form>

          {/* Beside the account, not buried in Settings: it is a property of
              the screen you are sitting at, and the person who wants it wants
              it now. */}
          <ThemeToggle />

          {isAdmin && (
            <div className="quick-add-wrap" ref={quickAddRef}>
              <button
                type="button"
                className="sidebar-toggle quick-add-btn"
                onClick={() => setQuickAddOpen((v) => !v)}
                aria-label="Quick add"
                aria-expanded={quickAddOpen}
                aria-haspopup="menu"
              >
                <i className="fas fa-plus" />
              </button>
              {quickAddOpen && (
                <div className="quick-add-menu" role="menu" aria-label="Quick add">
                  <button type="button" role="menuitem" onClick={() => { setQuickAddOpen(false); navigate("/staff"); }}>
                    <i className="fas fa-user-plus" /> Add Staff User
                  </button>
                  <button type="button" role="menuitem" onClick={() => { setQuickAddOpen(false); navigate("/customers/add"); }}>
                    <i className="fas fa-user" /> Add Customer
                  </button>
                </div>
              )}
            </div>
          )}

          <div className="user-greeting">
            <span className="who">
              <i className="fas fa-user-circle" style={{ marginRight: 6 }} />
              <span className="who-name">{user?.full_name || user?.username}</span>
            </span>
            <button onClick={handleSignOut} title="Sign out" aria-label="Sign out">
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
        <span className="footer-credit">Developed by Sumedh Developer</span>
      </footer>
    </>
  );
}
