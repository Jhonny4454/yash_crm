import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { NavLink, useLocation } from "react-router-dom";
import { MENU } from "./menu";
import logoImage from "../assets/logo.jpg";
// styles/Sidebar.css is imported from main.jsx, deliberately last: it has to
// win against the copies of the rail's rules in index.css and Dashboard.css,
// and a stylesheet imported from here would be emitted before both of them.

/**
 * The staff navigation rail.
 *
 * Expanded, it is an accordion: groups open in place, the way they always did.
 *
 * Collapsed, it is an icon rail and the group's children have to go SOMEWHERE
 * ELSE - and that is the part that was broken. The old build tried to do it in
 * CSS alone, with `position: absolute; left: 100%` on the submenu. Two things
 * killed it:
 *
 *   1. `.sidebar` sets `overflow-x: hidden` (it needs `overflow-y: auto` for
 *      long menus, and the two share one clipping box), so anything positioned
 *      past its right edge was clipped away entirely. Nothing appeared.
 *   2. The submenu is a SIBLING of the button, not a child, and no ancestor
 *      between it and `.sidebar` was positioned - so `top: 0` resolved against
 *      the whole rail. Every group would have opened at the top of the screen,
 *      stacked on top of the others.
 *
 * So the fly-out is rendered through a portal into <body> and positioned
 * `fixed` against the trigger's measured rectangle. No ancestor can clip it,
 * and it lines up with the icon you actually pointed at. It flips to the left
 * and slides up when it would fall off the viewport, which is what makes
 * "Masters" usable on a laptop screen at the bottom of the rail.
 *
 * Third-level groups (Masters > Address, Masters > Company) expand INSIDE the
 * panel rather than opening a further floating panel. A second hop across a
 * diagonal gap is the classic way to lose a menu on the way to it.
 */

const DESKTOP_QUERY = "(min-width: 992px)";
const HOVER_QUERY = "(hover: hover) and (pointer: fine)";

function useMediaQuery(query, fallback = false) {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return fallback;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const mql = window.matchMedia(query);
    const onChange = (event) => setMatches(event.matches);
    setMatches(mql.matches);
    // Safari < 14 only has the deprecated listener API.
    if (mql.addEventListener) mql.addEventListener("change", onChange);
    else mql.addListener(onChange);
    return () => {
      if (mql.removeEventListener) mql.removeEventListener("change", onChange);
      else mql.removeListener(onChange);
    };
  }, [query]);

  return matches;
}

function pathsUnder(node) {
  if (node.path) return [node.path];
  return (node.children || []).flatMap(pathsUnder);
}

function isBranchActive(node, pathname) {
  return pathsUnder(node).some(
    (p) => p !== "/" && (pathname === p || pathname.startsWith(p + "/"))
  );
}

/* ------------------------------------------------------------------ fly-out */

/** One floating panel, positioned against the rail item that opened it. */
function Flyout({ anchor, node, trail, pathname, autoFocus,
                 onClose, onHoverIn, onHoverOut }) {
  const panelRef = useRef(null);
  const [placed, setPlaced] = useState(null);

  // Sub-groups start open when they contain the current page, so the item you
  // are on is visible the moment the panel appears.
  const [expanded, setExpanded] = useState(() => {
    const initial = {};
    (node.children || []).forEach((child) => {
      if (child.children && isBranchActive(child, pathname)) initial[child.name] = true;
    });
    return initial;
  });

  useLayoutEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    const GAP = 8;
    const MARGIN = 10;
    const width = el.offsetWidth;
    const height = el.offsetHeight;

    let left = anchor.right + GAP;
    // Not enough room on the right (narrow window, or a very wide panel):
    // put it on the other side of the icon instead of letting it hang off.
    if (left + width > window.innerWidth - MARGIN) {
      left = Math.max(MARGIN, anchor.left - width - GAP);
    }

    let top = anchor.top;
    if (top + height > window.innerHeight - MARGIN) {
      top = window.innerHeight - height - MARGIN;
    }
    if (top < MARGIN) top = MARGIN;

    setPlaced({ top, left });
    // `expanded` is in the deps because opening a sub-group changes the
    // panel's height: measured once at open time, expanding Masters > Company
    // on a 768px laptop pushed the last three items below the bottom of the
    // screen, on a fixed element the page cannot scroll to reach.
  }, [anchor.top, anchor.left, anchor.right, node, expanded]);

  /* Keyboard entry. Tab moves along the rail, so without this the panel is a
     dead end for anyone not using a mouse: focusing the next icon replaces
     the fly-out before its links can be reached. Enter, Space or Right arrow
     on the icon steps into the panel; Escape steps back out. */
  useLayoutEffect(() => {
    if (!autoFocus || !placed) return;
    const first = panelRef.current?.querySelector(".flyout-link");
    if (first) first.focus();
  }, [autoFocus, placed]);

  const renderChild = (child) => {
    const key = [...trail, node.name, child.name].join("/");

    if (child.path) {
      return (
        <NavLink
          key={key}
          to={child.path}
          end={child.end}
          className={({ isActive }) => `flyout-link${isActive ? " active" : ""}`}
          onClick={onClose}
        >
          {child.name}
        </NavLink>
      );
    }

    const open = !!expanded[child.name];
    return (
      <div key={key} className={`flyout-group${open ? " is-open" : ""}`}>
        <button
          type="button"
          className="flyout-link flyout-branch"
          aria-expanded={open}
          onClick={() => setExpanded((e) => ({ ...e, [child.name]: !e[child.name] }))}
        >
          <span>{child.name}</span>
          <i className={`fas fa-chevron-${open ? "down" : "right"}`} aria-hidden="true" />
        </button>
        {open && (
          <div className="flyout-sub">
            {(child.children || []).map((grand) => (
              <NavLink
                key={[key, grand.name].join("/")}
                to={grand.path}
                end={grand.end}
                className={({ isActive }) => `flyout-link${isActive ? " active" : ""}`}
                onClick={onClose}
              >
                {grand.name}
              </NavLink>
            ))}
          </div>
        )}
      </div>
    );
  };

  return createPortal(
    <div
      ref={panelRef}
      data-nav-flyout=""
      className={`nav-flyout${node.children ? "" : " is-tip"}`}
      style={{
        top: placed ? placed.top : anchor.top,
        left: placed ? placed.left : anchor.right + 8,
        visibility: placed ? "visible" : "hidden",
      }}
      onMouseEnter={onHoverIn}
      onMouseLeave={onHoverOut}
      onKeyDown={(event) => {
        if (event.key === "Escape" || event.key === "ArrowLeft") {
          event.stopPropagation();
          onClose({ restoreFocus: true });
        }
      }}
      role="menu"
      aria-label={node.name}
    >
      <div className="nav-flyout-head">
        {node.icon && <i className={`fas ${node.icon}`} aria-hidden="true" />}
        <span>{node.name}</span>
      </div>
      {node.children && (
        <div className="nav-flyout-body">{node.children.map(renderChild)}</div>
      )}
    </div>,
    document.body
  );
}

/* ------------------------------------------------------------------ sidebar */

export default function Sidebar({
  company,
  mobileOpen,
  collapsed = false,
  onSignOut,
  onCloseMobile,
}) {
  const { pathname } = useLocation();
  const asideRef = useRef(null);
  const isDesktop = useMediaQuery(DESKTOP_QUERY, true);
  const canHover = useMediaQuery(HOVER_QUERY, true);

  // Fly-outs are a desktop-rail affordance. On a phone the rail is a full
  // drawer, so the accordion is both reachable and correct there.
  const flyoutMode = collapsed && isDesktop;

  const [open, setOpen] = useState(() => {
    try { return JSON.parse(localStorage.getItem("unicrm.menu") || "{}"); }
    catch { return {}; }
  });

  const [flyout, setFlyout] = useState(null); // { key, node, anchor, viaKeyboard }
  const closeTimer = useRef(null);
  const triggerRef = useRef(null);
  const suppressFocusOpen = useRef(false);

  const clearCloseTimer = useCallback(() => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);
  const closeFlyout = useCallback((options) => {
    clearCloseTimer();
    setFlyout(null);
    // Escape out of the panel puts the caret back on the icon it came from,
    // rather than dumping focus on <body> and losing the user's place. The
    // flag is what stops that focus from immediately re-opening the panel the
    // user just dismissed - the trigger's onFocus opens it.
    if (options && options.restoreFocus && triggerRef.current) {
      suppressFocusOpen.current = true;
      triggerRef.current.focus();
      setTimeout(() => { suppressFocusOpen.current = false; }, 0);
    }
  }, [clearCloseTimer]);
  // A short grace period, so crossing the few pixels between the icon and the
  // panel does not shut the menu in your face.
  const scheduleClose = useCallback(() => {
    clearCloseTimer();
    closeTimer.current = setTimeout(() => setFlyout(null), 240);
  }, [clearCloseTimer]);

  const openFlyout = useCallback((node, key, element, viaKeyboard = false) => {
    clearCloseTimer();
    triggerRef.current = element;
    const rect = element.getBoundingClientRect();
    setFlyout({
      key,
      node,
      viaKeyboard,
      anchor: { top: rect.top, left: rect.left, right: rect.right, bottom: rect.bottom },
    });
  }, [clearCloseTimer]);

  useEffect(() => clearCloseTimer, [clearCloseTimer]);

  /* Expand the branch that contains the current page. */
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

  /* Close the fly-out on navigation, Escape, an outside press, a resize, or
     scrolling the rail - any of which leaves it pointing at nothing. */
  useEffect(() => { closeFlyout(); }, [pathname, closeFlyout]);
  useEffect(() => { if (!flyoutMode) closeFlyout(); }, [flyoutMode, closeFlyout]);

  useEffect(() => {
    if (!flyout) return undefined;
    const onKey = (event) => { if (event.key === "Escape") closeFlyout(); };
    const onPointerDown = (event) => {
      const target = event.target;
      if (target && target.closest
          && (target.closest("[data-nav-flyout]") || target.closest(".sidebar"))) return;
      closeFlyout();
    };
    const onReflow = () => closeFlyout();
    const rail = asideRef.current;

    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("resize", onReflow);
    if (rail) rail.addEventListener("scroll", onReflow, { passive: true });
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("resize", onReflow);
      if (rail) rail.removeEventListener("scroll", onReflow);
    };
  }, [flyout, closeFlyout]);

  /* Swipe the drawer away on a touch screen. Staff on a phone reach for this
     before they look for a close button, and the scrim tap is easy to miss. */
  useEffect(() => {
    const el = asideRef.current;
    if (!mobileOpen || !onCloseMobile || !el) return undefined;

    let startX = 0;
    let startY = 0;
    let delta = 0;
    let dragging = false;

    const onStart = (event) => {
      const touch = event.touches[0];
      startX = touch.clientX;
      startY = touch.clientY;
      delta = 0;
      dragging = false;
    };
    const onMove = (event) => {
      const touch = event.touches[0];
      delta = touch.clientX - startX;
      const vertical = touch.clientY - startY;
      if (!dragging && Math.abs(delta) > 12 && Math.abs(delta) > Math.abs(vertical)) {
        dragging = true;
      }
      if (dragging && delta < 0) {
        el.style.transition = "none";
        el.style.transform = `translateX(${Math.max(delta, -360)}px)`;
      }
    };
    const onEnd = () => {
      el.style.transition = "";
      el.style.transform = "";
      if (dragging && delta < -70) onCloseMobile();
      dragging = false;
    };

    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: true });
    el.addEventListener("touchend", onEnd);
    el.addEventListener("touchcancel", onEnd);
    return () => {
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("touchcancel", onEnd);
      el.style.transition = "";
      el.style.transform = "";
    };
  }, [mobileOpen, onCloseMobile]);

  const toggle = (key) => setOpen((o) => ({ ...o, [key]: !o[key] }));

  /* Handlers shared by every item on the collapsed rail. Hover opens (fast,
     no click needed - the behaviour people expect from an icon rail), click
     toggles, and focus opens so the keyboard reaches it too. */
  const railHandlers = (node, key) => {
    if (!flyoutMode) return {};
    const handlers = {
      onClick: (event) => {
        if (flyout && flyout.key === key) closeFlyout();
        else openFlyout(node, key, event.currentTarget);
      },
      onFocus: (event) => {
        if (suppressFocusOpen.current) return;
        openFlyout(node, key, event.currentTarget);
      },
      onMouseLeave: scheduleClose,
      onKeyDown: (event) => {
        // Enter / Space / Right arrow step INTO the panel. Without this the
        // panel opens on focus and Tab walks straight past it to the next
        // icon, which closes it again - so its links were unreachable from
        // the keyboard entirely.
        if (!node.children) return;
        if (event.key === "ArrowRight" || event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openFlyout(node, key, event.currentTarget, true);
        } else if (event.key === "Escape") {
          closeFlyout();
        }
      },
    };
    if (canHover) {
      handlers.onMouseEnter = (event) => openFlyout(node, key, event.currentTarget);
    }
    return handlers;
  };

  const render = (nodes, trail = []) =>
    nodes.map((node) => {
      const key = [...trail, node.name].join("/");
      const topLevel = trail.length === 0;

      if (node.path) {
        return (
          <NavLink
            key={key}
            to={node.path}
            end={node.end}
            className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            title={collapsed ? undefined : node.name}
            {...(topLevel ? railHandlers(node, key) : {})}
            onClickCapture={topLevel && flyoutMode ? closeFlyout : undefined}
          >
            <span className="link-label">
              {node.icon && <i className={`fas ${node.icon}`} />}
              <span className="nav-label">{node.name}</span>
            </span>
          </NavLink>
        );
      }

      const expanded = !!open[key];
      const branchActive = isBranchActive(node, pathname);
      const flyoutOpen = !!flyout && flyout.key === key;

      return (
        <div key={key} className="nav-group">
          <button
            type="button"
            className={`nav-link${branchActive ? " branch-open" : ""}${flyoutOpen ? " flyout-open" : ""}`}
            onClick={flyoutMode ? undefined : () => toggle(key)}
            aria-expanded={flyoutMode ? flyoutOpen : expanded}
            aria-haspopup={flyoutMode ? "menu" : undefined}
            title={collapsed ? undefined : node.name}
            {...(topLevel ? railHandlers(node, key) : {})}
          >
            <span className="link-label">
              {node.icon && <i className={`fas ${node.icon}`} />}
              <span className="nav-label">{node.name}</span>
            </span>
            <i className={`fas fa-chevron-${expanded ? "down" : "right"} caret`} />
          </button>
          {!flyoutMode && expanded && (
            <div className="submenu">{render(node.children, [...trail, node.name])}</div>
          )}
        </div>
      );
    });

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const menu = useMemo(() => render(MENU), [pathname, open, collapsed, flyout, flyoutMode, canHover]);

  return (
    <>
      <aside
        ref={asideRef}
        className={`sidebar${mobileOpen ? " mobile-open" : ""}`}
        aria-label="Main navigation"
      >
        {/* The logo alone. The name sat beside it in a 240px rail and had to
            be truncated to fit - "Yash Intern..." - which reads as a rendering
            fault rather than a brand. The logo carries the name inside it, and
            it is the same mark on the bill and the customer portal, so nothing
            is lost by letting it speak for itself. The company name is still
            announced to a screen reader through the image's alt text. */}
        <div className="brand brand-logo-only">
          <img
            className="brand-logo"
            src={company?.logo_url || logoImage}
            alt={company?.name || "YASH Internet Services"}
          />
          {onCloseMobile && (
            <button type="button" className="drawer-close" onClick={onCloseMobile}
                    aria-label="Close menu">
              <i className="fas fa-times" />
            </button>
          )}
        </div>

        <nav>{menu}</nav>

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

      {flyoutMode && flyout && (
        <Flyout
          key={flyout.key}
          anchor={flyout.anchor}
          node={flyout.node}
          trail={[]}
          pathname={pathname}
          autoFocus={flyout.viaKeyboard}
          onClose={closeFlyout}
          onHoverIn={clearCloseTimer}
          onHoverOut={scheduleClose}
        />
      )}
    </>
  );
}
