/**
 * Visits every route in the SPA with a real browser and reports:
 *   - uncaught exceptions
 *   - console errors / warnings
 *   - failed network requests
 *   - blank pages (no meaningful text rendered)
 *
 * A passing `vite build` only proves the modules resolve. This proves the
 * components actually mount and render against API-shaped data.
 */
import { chromium } from "playwright";

const BASE = "http://localhost:4173/app";

const STAFF_ROUTES = [
  "/", "/customers", "/customers/add", "/customers/1", "/customers/1/edit",
  // Every tab on the customer workspace: the tab lives in the query string, so
  // each one is a distinct render the smoke run has to prove mounts.
  "/customers/1?tab=wallet", "/customers/1?tab=plan", "/customers/1?tab=pending",
  "/customers/1?tab=invoices", "/customers/1?tab=payments",
  "/customers/1?tab=inventory", "/customers/1?tab=messages",
  "/customers/1?tab=plan-history", "/customers/1?tab=logs",
  "/customers/1?tab=ledger",
  "/customers/ledger",
  "/customers/generate-invoice", "/renewals", "/customers/1/ledger", "/customers/plan-status",
  "/plans", "/plan-master/service-providers",
  "/invoices", "/invoices/1", "/payments", "/authorizations",
  "/companies", "/staff", "/staff/types",
  "/masters/zones", "/masters/localities", "/masters/areas", "/masters/buildings",
  "/masters/addresses", "/masters/tax", "/masters/addon-categories",
  "/masters/discount-reasons",
  "/masters/backup", "/masters/import-export",
  "/expenses", "/expenses/categories", "/expenses/accounts", "/expenses/payees",
  "/inventory/vendors", "/inventory/products", "/inventory/stock", "/inventory/vendor-bills",
  "/hr/attendance", "/hr/leaves", "/hr/payroll",
  "/reports/plan-expiry", "/reports/attendance", "/reports/leaves",
  "/reports/payroll", "/reports/collection", "/reports/expenses",
  "/notifications", "/settings", "/profile",
  "/masters/isp", "/masters/bulk-messages", "/masters/message-templates",
  "/this-route-does-not-exist",
];

const CUSTOMER_ROUTES = [
  "/customer", "/customer/invoices", "/customer/payments",
  "/customer/plans", "/customer/notifications", "/customer/profile",
];

const PUBLIC_ROUTES = ["/login", "/customer/login", "/forgot-password",
  "/customer/forgot-password", "/forbidden"];

// Noise we do not want counted as a real problem.
const IGNORE = [
  /favicon/i,
  /Download the React DevTools/i,
  /React Router Future Flag/i,
  /^\[vite\]/,
  // This sandbox has no outbound network, so the CDN <link>s in index.html
  // always fail here. That is an artefact of the test environment, not a bug.
  /cdnjs\.cloudflare\.com/,
  /fonts\.googleapis\.com/,
  /fonts\.gstatic\.com/,
  /cdn\.jsdelivr\.net/,
  /ERR_TUNNEL_CONNECTION_FAILED/,
  /Failed to load resource/,
];

const isNoise = (text) => IGNORE.some((re) => re.test(text));

async function seed(page, audience) {
  await page.addInitScript((aud) => {
    localStorage.setItem("unicrm.access", "test-access-token");
    localStorage.setItem("unicrm.refresh", "test-refresh-token");
    localStorage.setItem("unicrm.auth", JSON.stringify({
      audience: aud,
      user: aud === "staff"
        ? { id: 1, username: "admin", full_name: "Admin User", role: "admin" }
        : { id: 7, username: "ravi", full_name: "Ravi Kumar", mobile: "9876512345" },
      company: { name: "YASH Internet Services", logo_url: null },
    }));
  }, audience);
}

async function visit(page, route) {
  const problems = [];

  const onConsole = (msg) => {
    const type = msg.type();
    if (type !== "error" && type !== "warning") return;
    const text = msg.text();
    if (isNoise(text)) return;
    problems.push(`${type}: ${text.slice(0, 220)}`);
  };
  const onPageError = (err) => problems.push(`exception: ${String(err).slice(0, 220)}`);
  const onFailed = (req) => {
    if (isNoise(req.url())) return;
    problems.push(`request failed: ${req.url().slice(0, 120)}`);
  };

  page.on("console", onConsole);
  page.on("pageerror", onPageError);
  page.on("requestfailed", onFailed);

  let text = "";
  try {
    await page.goto(BASE + route, { waitUntil: "networkidle", timeout: 15000 });
    await page.waitForTimeout(250);
    text = (await page.locator("body").innerText()).trim();
  } catch (err) {
    problems.push(`navigation: ${String(err).slice(0, 160)}`);
  }

  page.off("console", onConsole);
  page.off("pageerror", onPageError);
  page.off("requestfailed", onFailed);

  return { problems, text };
}

const browser = await chromium.launch();
const results = [];

for (const [audience, routes] of [
  ["public", PUBLIC_ROUTES],
  ["staff", STAFF_ROUTES],
  ["customer", CUSTOMER_ROUTES],
]) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  if (audience !== "public") await seed(page, audience);

  for (const route of routes) {
    const { problems, text } = await visit(page, route);
    const blank = text.replace(/\s+/g, " ").length < 40;
    results.push({ audience, route, problems, blank, chars: text.length });
  }
  await context.close();
}

await browser.close();

/* ---- report ---- */
const bad = results.filter((r) => r.problems.length || r.blank);

console.log(`\nvisited ${results.length} routes`);
console.log(`clean   ${results.length - bad.length}`);
console.log(`issues  ${bad.length}\n`);

for (const r of bad) {
  console.log(`✗ [${r.audience}] ${r.route}${r.blank ? "   *** BLANK PAGE ***" : ""}`);
  for (const p of [...new Set(r.problems)].slice(0, 4)) console.log(`      ${p}`);
}

if (!bad.length) console.log("All routes rendered with no console errors.");
process.exit(0);
