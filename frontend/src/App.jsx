import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import ErrorBoundary from "./components/ErrorBoundary";
import IdleWarning from "./components/IdleWarning";
import OfflineBanner from "./components/OfflineBanner";
import RouteProgress from "./components/RouteProgress";
import AppShell from "./components/AppShell";
import AdminLayout from "./layouts/AdminLayout";
import { PageSkeleton, ProtectedRoute } from "./components/ui";
import { AuthProvider } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";

const Login = lazy(() => import("./pages/Login"));
const ForgotPassword = lazy(() => import("./pages/ForgotPassword"));
const AdminDashboard = lazy(() => import("./pages/AdminDashboard"));
const ResourcePage = lazy(() => import("./pages/ResourcePage"));
const AuthorisationReport = lazy(() => import("./pages/AuthorisationReport"));
const BillingRun = lazy(() => import("./pages/BillingRun"));
const RenewalQueue = lazy(() => import("./pages/RenewalQueue"));
const RecordDetailPage = lazy(() => import("./pages/RecordDetailPage"));
const CustomerForm = lazy(() => import("./pages/CustomerForm"));
const Customers = lazy(() => import("./pages/Customers"));
const CustomerDetail = lazy(() => import("./pages/CustomerDetail"));
const Plans = lazy(() => import("./pages/Plans"));
const Invoices = lazy(() => import("./pages/Invoices"));
const InvoiceView = lazy(() => import("./pages/InvoiceView"));
const Companies = lazy(() => import("./pages/Companies"));
const Notifications = lazy(() => import("./pages/Notifications"));
const CustomerLedger = lazy(() => import("./pages/CustomerLedger"));
const Profile = lazy(() => import("./pages/Profile"));
const LeavesPage = lazy(() => import("./pages/LeavesPage"));
const IspIntegrations = lazy(() => import("./pages/IspIntegrations"));
const BulkMessages = lazy(() => import("./pages/BulkMessages"));
const PlanExpiryBoard = lazy(() => import("./pages/PlanExpiryBoard"));
const PaymentsPage = lazy(() => import("./pages/PaymentsPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const BackupsPage = lazy(() => import("./pages/AdminToolsPages").then((m) => ({ default: m.BackupsPage })));
const ImportExportPage = lazy(() => import("./pages/AdminToolsPages").then((m) => ({ default: m.ImportExportPage })));
const ReportsPage = lazy(() => import("./pages/AdminToolsPages").then((m) => ({ default: m.ReportsPage })));
const PortalDashboard = lazy(() => import("./pages/PortalPages").then((m) => ({ default: m.PortalDashboard })));
const PortalInvoices = lazy(() => import("./pages/PortalInvoices"));
const PortalPayments = lazy(() => import("./pages/PortalPages").then((m) => ({ default: m.PortalPayments })));
const PortalPlans = lazy(() => import("./pages/PortalPages").then((m) => ({ default: m.PortalPlans })));
const PortalNotifications = lazy(() => import("./pages/PortalPages").then((m) => ({ default: m.PortalNotifications })));
const PortalProfile = lazy(() => import("./pages/PortalPages").then((m) => ({ default: m.PortalProfile })));
const NotFoundPage = lazy(() => import("./pages/SystemPages").then((m) => ({ default: m.NotFoundPage })));
const ForbiddenPage = lazy(() => import("./pages/SystemPages").then((m) => ({ default: m.ForbiddenPage })));

// A skeleton, not a spinner: every route here is lazy, so this fallback is
// what the operator sees on EVERY navigation. A centred spinner blanked the
// content area and then shoved a full page into it.
function Page({ children }) { return <Suspense fallback={<PageSkeleton />}>{children}</Suspense>; }

/* Fetch the screens an operator is about to use, before they ask for one.
 *
 * Every route is code-split, which keeps the first load small but means the
 * FIRST visit to each screen pays a round trip for its chunk before anything
 * can render - measured at ~100ms on a fast connection and considerably more
 * on a slow one, on top of the API call. Customer detail is the worst of them
 * at 56KB, and it is also the screen staff open dozens of times a day.
 *
 * So once the app has settled, quietly pull the handful that get used most.
 * Vite dedupes dynamic imports, so the React.lazy() call on navigation then
 * resolves from memory instead of the network.
 *
 * Deliberately after idle, and deliberately a short list: prefetching
 * everything would compete for bandwidth with the screen the operator is
 * actually looking at, which is the opposite of the point.
 */
function usePrefetchCommonRoutes(enabled) {
  useEffect(() => {
    if (!enabled) return undefined;

    let cancelled = false;
    const warm = () => {
      if (cancelled) return;
      // Ordered by how soon they are likely to be needed.
      import("./pages/Customers");
      import("./pages/CustomerDetail");
      import("./pages/AdminDashboard");
      import("./pages/Invoices");
    };

    const idle = window.requestIdleCallback
      ? window.requestIdleCallback(warm, { timeout: 3000 })
      : window.setTimeout(warm, 1200);

    return () => {
      cancelled = true;
      if (window.cancelIdleCallback) window.cancelIdleCallback(idle);
      else window.clearTimeout(idle);
    };
  }, [enabled]);
}

function Prefetcher() {
  // Only for signed-in staff: a customer never opens these screens, and
  // somebody sitting on the login page has not asked for them yet.
  let signedIn = false;
  try {
    const saved = JSON.parse(localStorage.getItem("unicrm.auth") || "null");
    signedIn = saved?.audience === "staff" && Boolean(saved?.user);
  } catch {
    signedIn = false;
  }
  usePrefetchCommonRoutes(signedIn);
  return null;
}
function StaffLayout() { return <ProtectedRoute audience="staff"><AdminLayout /></ProtectedRoute>; }
function CustomerLayout() { return <ProtectedRoute audience="customer"><AppShell audience="customer" /></ProtectedRoute>; }

const resource = (name) => <Page><ResourcePage resource={name} /></Page>;
const report = (endpoint, title) => <Page><ReportsPage endpoint={endpoint} title={title} /></Page>;

/* The two `future` flags on BrowserRouter opt in to React Router v7 behaviour
   early. They change nothing visible - this app does not rely on either of the
   old behaviours - but without them Router logs two warnings on every page
   load, which buries the console output that actually matters when something
   is wrong. */
export default function App() {
  // `BASE_URL` is /app/ for Flask's bundled build and / for the standalone
  // static deployment. React Router has to use the same base as Vite or a
  // direct visit to an admin/customer page resolves to the wrong route.
  const routerBase = import.meta.env.BASE_URL === "/"
    ? "/" : import.meta.env.BASE_URL.replace(/\/$/, "");
  return <ErrorBoundary><ToastProvider><AuthProvider><BrowserRouter basename={routerBase} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><OfflineBanner /><RouteProgress /><IdleWarning /><Prefetcher /><Routes>
    <Route path="/login" element={<Page><Login /></Page>} />
    <Route path="/customer/login" element={<Page><Login audience="customer" /></Page>} />
    <Route path="/forgot-password" element={<Page><ForgotPassword audience="staff" /></Page>} />
    <Route path="/customer/forgot-password" element={<Page><ForgotPassword /></Page>} />
    <Route element={<StaffLayout />}>
      <Route index element={<Page><AdminDashboard /></Page>} />
      <Route path="customers" element={<Page><Customers /></Page>} />
      <Route path="customers/add" element={<Page><CustomerForm /></Page>} />
      <Route path="customers/ledger" element={<Page><CustomerLedger /></Page>} />
      <Route path="customers/generate-invoice" element={<Page><BillingRun /></Page>} />
      <Route path="customers/:id" element={<Page><CustomerDetail /></Page>} />
      <Route path="customers/:id/edit" element={<Page><CustomerForm /></Page>} />
      <Route path="customers/:id/ledger" element={<Page><CustomerLedger /></Page>} />
      <Route path="customers/plan-status" element={report("/customers/plan-status", "Customer plan status")} />
      <Route path="plans" element={<Page><Plans /></Page>} />
      <Route path="invoices" element={<Page><Invoices /></Page>} />
      <Route path="invoices/:id" element={<Page><InvoiceView /></Page>} />
      <Route path="payments" element={<Page><PaymentsPage /></Page>} />
      <Route path="authorizations" element={<Page><AuthorisationReport /></Page>} />
      <Route path="renewals" element={<Page><RenewalQueue /></Page>} />
      <Route path="companies" element={<Page><Companies /></Page>} />
      <Route path="staff" element={resource("staff")} />
      <Route path="staff/types" element={resource("staff/types")} />
      <Route path="plan-master/service-providers" element={resource("service-providers")} />
      <Route path="masters/zones" element={resource("masters/zones")} />
      <Route path="masters/localities" element={resource("masters/localities")} />
      <Route path="masters/areas" element={resource("masters/areas")} />
      <Route path="masters/buildings" element={resource("masters/buildings")} />
      <Route path="masters/addresses" element={resource("masters/addresses")} />
      <Route path="masters/tax" element={resource("masters/tax")} />
      <Route path="masters/addon-categories" element={resource("masters/addon-categories")} />
      <Route path="masters/discount-reasons" element={resource("masters/discount-reasons")} />
      <Route path="expenses" element={resource("expenses")} />
      <Route path="expenses/categories" element={resource("expenses/categories")} />
      <Route path="expenses/accounts" element={resource("expenses/accounts")} />
      <Route path="expenses/payees" element={resource("expenses/payees")} />
      <Route path="inventory/vendors" element={resource("inventory/vendors")} />
      <Route path="inventory/products" element={resource("inventory/products")} />
      <Route path="inventory/stock" element={resource("inventory/stock")} />
      <Route path="inventory/vendor-bills" element={resource("inventory/vendor-bills")} />
      <Route path="hr/attendance" element={resource("hr/attendance")} />
      <Route path="hr/leaves" element={<Page><LeavesPage /></Page>} />
      <Route path="hr/payroll" element={resource("hr/payroll")} />
      <Route path="reports/plan-expiry" element={<Page><PlanExpiryBoard /></Page>} />
      <Route path="reports/attendance" element={report("/reports/attendance", "Attendance report")} />
      <Route path="reports/leaves" element={report("/reports/leaves", "Leave report")} />
      <Route path="reports/payroll" element={report("/reports/payroll", "Payroll report")} />
      <Route path="reports/collection" element={report("/reports/collection", "Collection report")} />
      <Route path="reports/expenses" element={report("/reports/expenses", "Expense report")} />
      <Route path="notifications" element={<Page><Notifications /></Page>} />
      <Route path="settings" element={<Page><SettingsPage /></Page>} />
      <Route path="masters/backup" element={<Page><BackupsPage /></Page>} />
      <Route path="masters/import-export" element={<Page><ImportExportPage /></Page>} />
      <Route path="profile" element={<Page><Profile /></Page>} />
      <Route path="masters/isp" element={<Page><IspIntegrations /></Page>} />
      <Route path="masters/bulk-messages" element={<Page><BulkMessages /></Page>} />
      <Route path="masters/message-templates" element={resource("masters/message-templates")} />
      <Route path="payments/authorizations" element={<Navigate to="/authorizations" replace />} />
    </Route>
    <Route element={<CustomerLayout />}>
      <Route path="customer" element={<Page><PortalDashboard /></Page>} />
      <Route path="customer/invoices" element={<Page><PortalInvoices /></Page>} />
      <Route path="customer/payments" element={<Page><PortalPayments /></Page>} />
      <Route path="customer/plans" element={<Page><PortalPlans /></Page>} />
      <Route path="customer/notifications" element={<Page><PortalNotifications /></Page>} />
      <Route path="customer/profile" element={<Page><PortalProfile /></Page>} />
    </Route>
    <Route path="/forbidden" element={<Page><ForbiddenPage /></Page>} />
    <Route path="/" element={<Navigate to="/login" replace />} />
    <Route path="*" element={<Page><NotFoundPage /></Page>} />
  </Routes></BrowserRouter></AuthProvider></ToastProvider></ErrorBoundary>;
}
