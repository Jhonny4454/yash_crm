import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import logoImage from "../assets/logo.jpg";

/**
 * The customer portal shell.
 *
 * A customer opens this on a phone, once a month, to see what they owe and pay
 * it. The old shell was the staff layout with a different list of links: a
 * 248px sidebar that, below 820px, turned into a horizontally scrolling strip
 * of text links above the content - so on the device nearly every customer
 * actually uses, the navigation was a row you had to scroll sideways to see
 * the end of, and the page began halfway down the screen.
 *
 * This is built the other way round. On a phone it is an app: a compact top
 * bar and a fixed bottom tab bar with five destinations, thumb-height targets,
 * and safe-area padding so nothing sits under the Android gesture bar or the
 * iPhone home indicator. From 900px up the tab bar becomes a proper left rail
 * and the same pages get the room they deserve.
 */

const TABS = [
  { to: "/customer", label: "Home", icon: "fa-house", end: true },
  { to: "/customer/invoices", label: "Bills", icon: "fa-file-invoice" },
  { to: "/customer/payments", label: "Payments", icon: "fa-receipt" },
  { to: "/customer/plans", label: "Plan", icon: "fa-wifi" },
  { to: "/customer/profile", label: "Account", icon: "fa-user" },
];

const TITLES = {
  "/customer": "My account",
  "/customer/invoices": "Invoices",
  "/customer/payments": "Payments",
  "/customer/plans": "Renew or change plan",
  "/customer/notifications": "Notifications",
  "/customer/profile": "My profile",
};

export default function PortalShell() {
  const { user, company, signOut } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  /* The global stylesheet pads <body> to clear the staff top bar and footer.
     The portal has neither, so it takes that padding back for the duration. */
  useEffect(() => {
    document.body.classList.add("portal-mode");
    return () => document.body.classList.remove("portal-mode");
  }, []);

  useEffect(() => setMenuOpen(false), [pathname]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (event) => { if (event.key === "Escape") setMenuOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  async function logout() {
    await signOut();
    navigate("/customer/login", { replace: true });
  }

  const title = TITLES[pathname] || "My account";
  const name = user?.full_name || user?.username || user?.mobile || "Customer";

  /* The avatar was showing "M" for what looked like every customer on the
     system. It was taking the first character of `full_name`, and full_name is
     stored WITH the title - "Mr. Sumedh Chabukswar" - so it was rendering the
     honorific, not the person. Strip those before taking the letter. */
  const initial = String(name)
    .replace(/^\s*(m\/s|mr|mrs|ms|miss|dr|smt|shri|sri)\.?\s+/i, "")
    .trim()
    .charAt(0)
    .toUpperCase() || "C";

  const logo = company?.logo_url || "";

  const tabLink = ({ to, label, icon, end }) => (
    <NavLink
      key={to}
      to={to}
      end={end}
      className={({ isActive }) => `pt-tab${isActive ? " active" : ""}`}
    >
      <i className={`fas ${icon}`} aria-hidden="true" />
      <span>{label}</span>
    </NavLink>
  );

  return (
    <div className="portal-shell">
      {/* ---------------------------------------------------- desktop rail */}
      <aside className="pt-rail" aria-label="Portal navigation">
        <NavLink to="/customer" className="pt-brand" end>
          <img src={company?.logo_url || logoImage} alt="" />
          <span>{company?.name || "YASH Internet"}</span>
        </NavLink>

        <nav className="pt-rail-nav">{TABS.map(tabLink)}
          <NavLink to="/customer/notifications"
                   className={({ isActive }) => `pt-tab${isActive ? " active" : ""}`}>
            <i className="fas fa-bell" aria-hidden="true" />
            <span>Notifications</span>
          </NavLink>
        </nav>

        <button type="button" className="pt-rail-signout" onClick={logout}>
          <i className="fas fa-right-from-bracket" aria-hidden="true" />
          <span>Sign out</span>
        </button>
      </aside>

      {/* --------------------------------------------------------- top bar */}
      <header className="pt-topbar">
        <div className="pt-topbar-title">
          {/* The mark lives on the account button to the right now, so it
              appears once rather than twice across the same bar. */}
          <div>
            <span className="pt-eyebrow">{company?.name || "YASH Internet Services"}</span>
            <strong>{title}</strong>
          </div>
        </div>

        <div className="pt-topbar-actions">
          <NavLink to="/customer/notifications" className="pt-icon-btn"
                   aria-label="Notifications">
            <i className="fas fa-bell" aria-hidden="true" />
          </NavLink>

          <div className="pt-account">
            {/* The company logo, and nothing else. The letter is only the
                fallback for a company that has not uploaded one. */}
            <button type="button" className={`pt-avatar${logo ? " has-logo" : ""}`}
                    onClick={() => setMenuOpen((v) => !v)}
                    aria-haspopup="menu" aria-expanded={menuOpen} aria-label="Account menu">
              {logo
                ? <img src={logo} alt="" />
                : <img src={logoImage} alt="" onError={(event) => {
                    // No company logo and the bundled one failed: a letter is
                    // better than a broken-image icon.
                    event.currentTarget.replaceWith(
                      document.createTextNode(initial));
                  }} />}
            </button>
            {menuOpen && (
              <>
                <div className="pt-menu-scrim" onClick={() => setMenuOpen(false)} />
                <div className="pt-menu" role="menu">
                  <div className="pt-menu-head">
                    <strong>{name}</strong>
                    {user?.reference_id && <span>ID {user.reference_id}</span>}
                  </div>
                  <NavLink to="/customer/profile" className="pt-menu-item">
                    <i className="fas fa-user" aria-hidden="true" /> My profile
                  </NavLink>
                  <NavLink to="/customer/notifications" className="pt-menu-item">
                    <i className="fas fa-bell" aria-hidden="true" /> Notifications
                  </NavLink>
                  <button type="button" className="pt-menu-item danger" onClick={logout}>
                    <i className="fas fa-right-from-bracket" aria-hidden="true" /> Sign out
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      {/* ----------------------------------------------------------- pages */}
      <main className="pt-main">
        <Outlet />
        <p className="pt-foot">
          &copy; {new Date().getFullYear()} {company?.name || "YASH Internet Services"}
        </p>
      </main>

      {/* ------------------------------------------------ mobile tab bar */}
      <nav className="pt-tabbar" aria-label="Portal sections">
        {TABS.map(tabLink)}
      </nav>
    </div>
  );
}
