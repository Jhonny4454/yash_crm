/** Screenshots a few routes so the layout can be eyeballed, not just asserted. */
import { chromium } from "playwright";

const BASE = "http://localhost:4173/app";
const SHOTS = [
  ["dashboard", "/", 1440, 900],
  ["plan-expiry", "/reports/plan-expiry", 1440, 900],
  ["customers", "/customers", 1440, 900],
  ["customer-detail", "/customers/1", 1440, 1100],
  ["customer-plan", "/customers/1?tab=plan", 1440, 780],
  ["customer-pending", "/customers/1?tab=pending", 1440, 900],
  ["customer-payments", "/customers/1?tab=payments", 1440, 780],
  ["customer-ledger", "/customers/1?tab=ledger", 1440, 780],
  ["customer-add", "/customers/add", 1440, 1400],
  ["authorising-report", "/authorizations", 1560, 900],
  ["billing-run", "/customers/generate-invoice", 1440, 1000],
  ["renewal-queue", "/renewals", 1500, 1050],
  ["bulk-messages", "/masters/bulk-messages", 1440, 900],
  ["dashboard-mobile", "/", 420, 860],
];

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

await page.addInitScript(() => {
  localStorage.setItem("unicrm.access", "t");
  localStorage.setItem("unicrm.refresh", "t");
  localStorage.setItem("unicrm.auth", JSON.stringify({
    audience: "staff",
    user: { id: 1, username: "admin", full_name: "Admin User", role: "admin" },
    company: { name: "YASH Internet Services", logo_url: null },
  }));
});

for (const [name, route, w, h] of SHOTS) {
  await page.setViewportSize({ width: w, height: h });
  await page.goto(BASE + route, { waitUntil: "networkidle", timeout: 20000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `/home/claude/work/shots/${name}.png`, fullPage: false });
  console.log("captured", name);
}

// Public pages need a context with no seeded session - a signed-in user
// visiting /login is correctly redirected to the dashboard.
const anon = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const anonPage = await anon.newPage();
// Customer portal, seeded as a customer rather than staff.
const portal = await browser.newContext({ viewport: { width: 1200, height: 820 } });
await portal.addInitScript(() => {
  localStorage.setItem("unicrm.access", "t");
  localStorage.setItem("unicrm.refresh", "t");
  localStorage.setItem("unicrm.auth", JSON.stringify({
    audience: "customer",
    user: { id: 7, full_name: "Ravi Kumar", mobile: "9876512345", username: "ravi" },
    company: { name: "YASH Internet Services", logo_url: null },
  }));
});
const portalPage = await portal.newPage();
for (const [name, route] of [["portal-invoices", "/customer/invoices"],
                             ["portal-plans", "/customer/plans"]]) {
  await portalPage.goto(BASE + route, { waitUntil: "networkidle", timeout: 20000 });
  await portalPage.waitForTimeout(500);
  await portalPage.screenshot({ path: `/home/claude/work/shots/${name}.png` });
  console.log("captured " + name);
}
await portal.close();

for (const [name, route] of [["login", "/login"], ["portal-login", "/customer/login"],
                             ["staff-forgot", "/forgot-password"]]) {
  await anonPage.goto(BASE + route, { waitUntil: "networkidle", timeout: 20000 });
  await anonPage.waitForTimeout(500);
  await anonPage.screenshot({ path: `/home/claude/work/shots/${name}.png` });
  console.log("captured", name, "(anonymous)");
}
await anon.close();

await browser.close();
