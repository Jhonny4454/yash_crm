/**
 * Interaction pass: the smoke run proves each route mounts, but the parts of
 * this screen that only exist after a click - the Options menu, the addon
 * invoice form, the plan dialogs - are exactly where a broken render hides.
 */
import { chromium } from "playwright";

const BASE = "http://localhost:4173/app";
const problems = [];

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 950 } });
await context.addInitScript(() => {
  localStorage.setItem("unicrm.access", "t");
  localStorage.setItem("unicrm.refresh", "t");
  localStorage.setItem("unicrm.auth", JSON.stringify({
    audience: "staff",
    user: { id: 1, username: "admin", full_name: "Admin User", role: "admin" },
    company: { name: "YASH Internet Services", logo_url: null },
  }));
});

const page = await context.newPage();
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error" && !/cdnjs|fonts\.|Failed to load resource/.test(m.text())) {
    problems.push(`console: ${m.text()}`);
  }
});

async function shot(name) {
  await page.screenshot({ path: `/home/claude/work/shots/${name}.png` });
  console.log("captured", name);
}

// 1. Options dropdown
await page.goto(`${BASE}/customers/1`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: /^Options/ }).click();
await page.waitForSelector(".cd-options-menu");
const items = await page.locator(".cd-options-menu button").allTextContents();
console.log("options:", items.join(" | "));
await shot("cd-options-open");
await page.keyboard.press("Escape");

// 2. Addon invoice: raises a bill and must NOT ask for money.
await page.goto(`${BASE}/customers/1?tab=pending`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: "Addon Invoice" }).click();
await page.waitForSelector(".addon-form");
await page.fill('.addon-form input >> nth=1', "1200");
await page.waitForTimeout(200);
const addonText = await page.locator(".addon-form").innerText();
if (/select mode|book receipt|amount received/i.test(addonText)) {
  problems.push("the addon form still asks for payment - it should only raise the bill");
}
if (!/no money is taken here/i.test(addonText)) {
  problems.push("the addon form does not say that no money is taken");
}
console.log(`addon form: raise-only, total line "${
  (addonText.match(/₹[\d,.]+ will be added[^.]*/) || ["(not found)"])[0]}"`);
await shot("cd-addon-invoice");

// 3. Assign plan dialog
await page.goto(`${BASE}/customers/1?tab=plan`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: "Assign/Change" }).click();
await page.waitForSelector(".plan-picker table");
await page.locator(".plan-picker tbody tr").first().click();
await shot("cd-assign-plan");

// 4. Edit customer plan dialog
await page.keyboard.press("Escape");
await page.getByRole("button", { name: "Edit" }).click();
await page.waitForSelector(".modal-card");
await shot("cd-edit-plan");

// 5. Add-customer plan picker
await page.keyboard.press("Escape");
await page.goto(`${BASE}/customers/add`, { waitUntil: "networkidle" });
await page.waitForSelector(".plan-picker table");
await page.locator(".plan-picker tbody tr").first().click();
await page.locator(".seg-toggle button", { hasText: "FUP" }).click();
await page.waitForTimeout(600);
await page.locator("fieldset", { hasText: "Assign Plan" }).scrollIntoViewIfNeeded();
await shot("cd-add-plan-picker");

// 6. Authorising report: select-all, then the confirm dialog on Submit.
await page.goto(`${BASE}/authorizations`, { waitUntil: "networkidle" });
await page.waitForSelector(".auth-table tbody tr");
const rowCount = await page.locator(".auth-table tbody tr").count();
await page.locator(".auth-table thead input[type=checkbox]").check();
const selectedRows = await page.locator(".auth-table tbody tr.is-selected").count();
console.log(`authorising report: ${selectedRows}/${rowCount} rows selected by the header tick`);
if (selectedRows !== rowCount) problems.push("select-all did not select every row on the page");
const label = await page.locator(".selection-count").textContent();
if (!/\d+ selected/.test(label)) problems.push(`selection count did not update: "${label}"`);
await shot("cd-auth-selected");

await page.getByRole("button", { name: "Submit" }).click();
await page.waitForSelector(".confirm-card");
await shot("cd-auth-confirm");
await page.getByRole("button", { name: /Authorise/ }).click();
await page.waitForTimeout(700);

// 7. Billing run: select-all skips the blocked row, and the confirm dialog
//    quotes the right count and total.
await page.goto(`${BASE}/customers/generate-invoice`, { waitUntil: "networkidle" });
await page.waitForSelector(".run-table tbody tr");
const totalRows = await page.locator(".run-table tbody tr").count();
const blockedRows = await page.locator(".run-table tbody tr.is-blocked").count();
await page.locator(".run-table thead input[type=checkbox]").check();
const picked = await page.locator(".run-table tbody tr.is-selected").count();
console.log(`billing run: ${totalRows} rows, ${blockedRows} blocked, ${picked} selected`);
if (picked !== totalRows - blockedRows) {
  problems.push(`select-all picked ${picked} of ${totalRows - blockedRows} billable rows`);
}
if (await page.locator(".run-table tbody tr.is-blocked input:checked").count()) {
  problems.push("select-all ticked a row that cannot be billed");
}
const runLabel = await page.locator(".billing-run .selection-count").textContent();
console.log(`  selection reads: ${runLabel}`);
await shot("cd-run-selected");

await page.getByRole("button", { name: "Generate invoices" }).click();
await page.waitForSelector(".confirm-card");
const dialog = await page.locator(".confirm-card").textContent();
if (!/does not extend|expiry moves when/i.test(dialog) && !/Invoices cannot be deleted/i.test(dialog)) {
  problems.push("the generate dialog does not spell out the consequences");
}
console.log(`  dialog: ${dialog.replace(/\s+/g, " ").slice(0, 120)}…`);
await shot("cd-run-confirm");
await page.getByRole("button", { name: /Generate \d+/ }).click();
await page.waitForTimeout(700);

// 8. Renewal queue: the header tick must select only the PAID rows.
await page.goto(`${BASE}/renewals`, { waitUntil: "networkidle" });
await page.waitForSelector(".renewal-table tbody tr");
const rTotal = await page.locator(".renewal-table tbody tr").count();
const rUnpaid = await page.locator(".renewal-table tbody tr.is-unpaid").count();
await page.locator(".renewal-table thead input[type=checkbox]").check();
const rPicked = await page.locator(".renewal-table tbody tr.is-selected").count();
console.log(`renewal queue: ${rTotal} rows, ${rUnpaid} unpaid, ${rPicked} selected`);
if (rPicked !== rTotal - rUnpaid) {
  problems.push(`select-all picked ${rPicked}, expected ${rTotal - rUnpaid} paid rows`);
}
if (await page.locator(".renewal-table tbody tr.is-unpaid input:checked").count()) {
  problems.push("select-all ticked an UNPAID renewal - approving it would extend service for free");
}
await shot("cd-renewal-selected");

// Approving one unpaid row individually must warn, not proceed quietly.
await page.locator(".renewal-table tbody tr.is-unpaid .btn.primary").first().click();
await page.waitForSelector(".confirm-card");
const rDialog = await page.locator(".confirm-card").textContent();
if (!/outstanding|has not arrived/i.test(rDialog)) {
  problems.push("approving an unpaid renewal did not warn that the money is missing");
}
console.log(`  unpaid dialog: ${rDialog.replace(/\s+/g, " ").slice(0, 110)}…`);
await shot("cd-renewal-unpaid-warning");

// 9. Pending Invoice: one payment entry across a renewal AND an addon.
await page.goto(`${BASE}/customers/1?tab=pending`, { waitUntil: "networkidle" });
await page.waitForSelector(".pending-table tbody tr");
const pRows = await page.locator(".pending-table tbody tr").count();
const pSelected = await page.locator(".pending-table tbody tr.is-selected").count();
console.log(`pending invoices: ${pRows} rows, ${pSelected} pre-selected`);
if (pSelected !== pRows) problems.push("the pending list did not pre-select every invoice");

const summary = await page.locator(".pay-summary strong").textContent();
console.log(`  payable reads: ${summary}`);
if (!summary.includes("1,500")) {
  problems.push(`combined total wrong: ${summary} (expected 1,500 across renewal + addons)`);
}

// A discount must come off the combined figure, not one invoice.
await page.locator(".pay-grid input[type=number]").nth(0).fill("100");
await page.waitForTimeout(200);
const discounted = await page.locator(".pay-summary strong").textContent();
console.log(`  after a 100 discount: ${discounted}`);
if (!discounted.includes("1,400")) {
  problems.push(`discount did not come off the combined total: ${discounted}`);
}

// Only the unpaid ones can be withdrawn.
const deletable = await page.locator(".pending-table .link-danger").count();
console.log(`  deletable invoices: ${deletable} of ${pRows} (the part-paid one must not be)`);
if (deletable !== pRows - 1) {
  problems.push("an invoice with money against it was offered for deletion");
}

// Online modes reveal the transaction fields.
await page.locator(".pay-grid select").nth(1).selectOption({ label: "GooglePay" });
await page.waitForTimeout(250);
const ref = await page.getByText("Transaction no.", { exact: false }).count();
console.log(`  GooglePay reveals the transaction fields: ${ref > 0}`);
if (!ref) problems.push("an online mode did not reveal the transaction fields");
await shot("cd-payment-entry");

await browser.close();

if (problems.length) {
  console.log("\nISSUES");
  problems.forEach((p) => console.log(" -", p));
  process.exit(1);
}
console.log("\nInteraction pass clean.");
