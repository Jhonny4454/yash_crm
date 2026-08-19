import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLang, useT } from "../context/LanguageContext";
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
  { to: "/customer", labelKey: "nav.home", icon: "fa-house", end: true },
  { to: "/customer/invoices", labelKey: "nav.bills", icon: "fa-file-invoice" },
  { to: "/customer/payments", labelKey: "nav.payments", icon: "fa-receipt" },
  { to: "/customer/plans", labelKey: "nav.plan", icon: "fa-wifi" },
  { to: "/customer/profile", labelKey: "nav.account", icon: "fa-user" },
];

const TITLES = {
  "/customer": "nav.home",
  "/customer/invoices": "nav.bills",
  "/customer/payments": "nav.payments",
  "/customer/plans": "nav.plan",
  "/customer/notifications": "nav.notifications",
  "/customer/profile": "nav.profile",
};

const LANG_OPTIONS = [
  { code: "en", label: "English" },
  { code: "mr", label: "\u092e\u0930\u093e\u0920\u0940" },
];

export default function PortalShell() {
  const { user, company, signOut } = useAuth();
  const { lang, setLang } = useLang();
  const t = useT();
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

  const title = t(TITLES[pathname] || "nav.home");
  const name = user?.full_name || user?.username || user?.mobile || "Customer";

  const tabLink = ({ to, labelKey, icon, end }) => (
    <NavLink
      key={to}
      to={to}
      end={end}
      className={({ isActive }) => `pt-tab${isActive ? " active" : ""}`}
    >
      <i className={`fas ${icon}`} aria-hidden="true" />
      <span>{t(labelKey)}</span>
    </NavLink>
  );

  return (
    <div className="portal-shell">
      {/* ---------------------------------------------------- desktop rail */}
      <aside className="pt-rail" aria-label="Portal navigation">
        <NavLink to="/customer" className="pt-brand" end>
          {/* Same fallback as the admin sidebar: an upload the server has
              lost 404s, and a broken-image glyph in the customer's header is
              the first thing they see of this company. */}
          <img src={company?.logo_url || logoImage} alt=""
               onError={(event) => {
                 if (event.currentTarget.src !== logoImage) {
                   event.currentTarget.onerror = null;
                   event.currentTarget.src = logoImage;
                 }
               }} />
          <span>{company?.name || "YASH Internet"}</span>
        </NavLink>

        <nav className="pt-rail-nav">{TABS.map(tabLink)}
          <NavLink to="/customer/notifications"
                   className={({ isActive }) => `pt-tab${isActive ? " active" : ""}`}>
            <i className="fas fa-bell" aria-hidden="true" />
            <span>{t("nav.notifications")}</span>
          </NavLink>
        </nav>

        <button type="button" className="pt-rail-signout" onClick={logout}>
          <i className="fas fa-right-from-bracket" aria-hidden="true" />
          <span>{t("nav.signout")}</span>
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
            {/* A plain user icon, drawn inline.
                Not <img>, which needs a file that may not exist - a company
                with no logo uploaded got a broken-image glyph. Not a Font
                Awesome <i> either: the icon font is fetched from a CDN with
                media="print" so it never blocks paint, which also means it may
                arrive late or not at all, and this is the one control in the
                bar with no text label to fall back on. Inline SVG is part of
                the document and always draws. */}
            <button type="button" className="pt-avatar"
                    onClick={() => setMenuOpen((v) => !v)}
                    aria-haspopup="menu" aria-expanded={menuOpen} aria-label="Account menu">
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <circle cx="12" cy="8" r="4" />
                <path d="M4 21v-1a8 8 0 0 1 16 0v1z" />
              </svg>
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
                    <i className="fas fa-user" aria-hidden="true" /> {t("nav.profile")}
                  </NavLink>
                  <NavLink to="/customer/notifications" className="pt-menu-item">
                    <i className="fas fa-bell" aria-hidden="true" /> {t("nav.notifications")}
                  </NavLink>
                  <div className="pt-menu-divider" />
                  <div className="pt-menu-item pt-lang-row">
                    <i className="fas fa-globe" aria-hidden="true" /> {t("lang.label")}
                    <div className="pt-lang-options">
                      {LANG_OPTIONS.map((opt) => (
                        <button key={opt.code} type="button"
                                className={`pt-lang-btn${lang === opt.code ? " active" : ""}`}
                                onClick={() => setLang(opt.code)}>
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <button type="button" className="pt-menu-item danger" onClick={logout}>
                    <i className="fas fa-right-from-bracket" aria-hidden="true" /> {t("nav.signout")}
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
