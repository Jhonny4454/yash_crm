import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { MENU } from "./menu";

// ✅ Import your logo from the assets folder
import logoImage from "../assets/logo.jpg";

/** Collapsible sidebar matching templates/base.html. */

function pathsUnder(node) {
  if (node.path) return [node.path];
  return (node.children || []).flatMap(pathsUnder);
}

function isBranchActive(node, pathname) {
  return pathsUnder(node).some(
    (p) => p !== "/" && (pathname === p || pathname.startsWith(p + "/"))
  );
}

export default function Sidebar({ company, mobileOpen, collapsed = false,
                                  onSignOut }) {
  const { pathname } = useLocation();

  const [open, setOpen] = useState(() => {
    try { return JSON.parse(localStorage.getItem("unicrm.menu") || "{}"); }
    catch { return {}; }
  });

  useEffect(() => {
    setOpen((prev) => {
      const next = { ...prev };
      const walk = (nodes, trail) => {
        nodes.forEach((n) => {
          if (!n.children) return;
          const key = [...trail, n.name].join("/");
          if (isBranchActive(n, pathname)) next[key] = true;
          walk(n.children, [...trail, n.name]);
        });
      };
      walk(MENU, []);
      return next;
    });
  }, [pathname]);

  useEffect(() => {
    localStorage.setItem("unicrm.menu", JSON.stringify(open));
  }, [open]);

  const toggle = (key) => setOpen((o) => ({ ...o, [key]: !o[key] }));

  const render = (nodes, trail = []) =>
    nodes.map((node) => {
      const key = [...trail, node.name].join("/");

      if (node.path) {
        return (
          <NavLink
            key={key}
            to={node.path}
            end={node.end}
            className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            title={node.name}
          >
            <span className="link-label">
              {node.icon && <i className={`fas ${node.icon}`} />}
              <span className="nav-label">{node.name}</span>
            </span>
          </NavLink>
        );
      }

      const expanded = !!open[key];
      return (
        <div key={key}>
          <button
            type="button"
            className={`nav-link${isBranchActive(node, pathname) ? " branch-open" : ""}`}
            onClick={() => toggle(key)}
            aria-expanded={expanded}
            title={node.name}
          >
            <span className="link-label">
              {node.icon && <i className={`fas ${node.icon}`} />}
              <span className="nav-label">{node.name}</span>
            </span>
            <i className={`fas fa-chevron-${expanded ? "down" : "right"} caret`} />
          </button>
          {expanded && <div className="submenu">{render(node.children, [...trail, node.name])}</div>}
        </div>
      );
    });

  return (
    <aside className={`sidebar${mobileOpen ? " mobile-open" : ""}`}>
      <div className="brand">
        {/* ✅ Logo auto‑sizes based on sidebar collapse */}
        <img 
          src={company?.logo_url || logoImage} 
          alt={company?.name || "YASH"}
          style={{
            height: collapsed ? "40px" : "70px",
            width: "auto",
            transition: "all 0.18s ease",
            objectFit: "contain",
            flexShrink: 0,
          }}
        />
        <h5>{company?.name || "Yash Internet Services"}</h5>
      </div>
      <nav>{render(MENU)}</nav>

      {/* Sign out, pinned to the bottom of the rail below Settings.
          There is a sign-out icon in the top bar too, but it is a bare icon
          next to the user's name and reads as part of the greeting - staff
          were closing the tab instead, which leaves the session alive until
          it times out. A labelled item in the place the eye already scans for
          Settings is the one people find. */}
      {onSignOut && (
        <div className="sidebar-foot">
          <button type="button" className="nav-link nav-signout"
                  onClick={onSignOut} title="Sign out">
            <span className="link-label">
              <i className="fas fa-sign-out-alt" />
              <span className="nav-label">Logout</span>
            </span>
          </button>
        </div>
      )}
    </aside>
  );
}