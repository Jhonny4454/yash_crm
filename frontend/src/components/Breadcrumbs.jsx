import { Link, useLocation } from "react-router-dom";
import { TITLES } from "./menu";

/**
 * Breadcrumb trail derived from the current path.
 *
 * Every ancestor segment that maps to a real route becomes a link; segments
 * that are only groupings (`/masters`, `/hr`, `/reports`) render as plain
 * text rather than links to routes that do not exist. Numeric segments are
 * record ids and show as "#12" unless the page supplies a better label.
 */

// Path prefixes that group pages in the menu but have no page of their own.
const NON_ROUTES = new Set([
  "/masters", "/hr", "/reports", "/expenses/", "/inventory", "/plan-master", "/customer",
]);

const FALLBACK_LABELS = {
  masters: "Masters",
  hr: "HR & Payroll",
  reports: "Reports",
  inventory: "Inventory",
  expenses: "Expenses",
  staff: "Staff",
  customers: "Customers",
  plans: "Plans",
  invoices: "Invoices",
  payments: "Payments",
  "plan-master": "Plan Master",
  add: "Add",
  edit: "Edit",
  ledger: "Ledger",
  profile: "My Profile",
  types: "Types",
  "plan-status": "Plan Status",
  "import-export": "Import / Export",
  "vendor-bills": "Vendor Bills",
  "addon-categories": "Addon Categories",
  "service-providers": "Service Providers",
  "plan-expiry": "Plan Expiry",
};

function labelFor(segment, path) {
  if (TITLES[path]) return TITLES[path];
  if (/^\d+$/.test(segment)) return `#${segment}`;
  if (FALLBACK_LABELS[segment]) return FALLBACK_LABELS[segment];
  return segment
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function Breadcrumbs({ currentLabel }) {
  const { pathname } = useLocation();
  const segments = pathname.split("/").filter(Boolean);

  // The dashboard is the root - no trail to show.
  if (!segments.length) return null;

  const crumbs = segments.map((segment, index) => {
    const path = "/" + segments.slice(0, index + 1).join("/");
    const isLast = index === segments.length - 1;
    return {
      path,
      label: isLast && currentLabel ? currentLabel : labelFor(segment, path),
      isLast,
      // Only link to somewhere that actually resolves.
      linkable: !isLast && !NON_ROUTES.has(path) && Boolean(TITLES[path]),
    };
  });

  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol>
        <li><Link to="/">Dashboard</Link></li>
        {crumbs.map((crumb) => (
          <li key={crumb.path} aria-current={crumb.isLast ? "page" : undefined}>
            {crumb.linkable
              ? <Link to={crumb.path}>{crumb.label}</Link>
              : <span>{crumb.label}</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}
