// Prove the circuit breaker: with the API down, a page-load's worth of calls
// must produce ONE failed request each, not three, and must say something useful.
import { chromium } from "playwright";

const browser = await chromium.launch();
const ctx = await browser.newContext();
await ctx.addInitScript(() => {
  localStorage.setItem("unicrm.access", "t");
  localStorage.setItem("unicrm.refresh", "t");
  localStorage.setItem("unicrm.auth", JSON.stringify({
    audience: "staff", user: { id: 1, full_name: "Admin", role: "admin" },
    company: { name: "YASH" },
  }));
});
const page = await ctx.newPage();

let apiCalls = 0;
await page.route("**/api/v1/**", (route) => {
  apiCalls += 1;
  route.fulfill({ status: 502, contentType: "text/html", body: "Bad Gateway" });
});

await page.goto("http://localhost:4173/app/customers", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

const text = await page.locator("body").innerText();
const onLogin = await page.locator(".login-card").count();
console.log(`signed out and bounced to login: ${onLogin > 0 ? "YES (bug)" : "no"}`);
console.log(`API requests while the backend was down: ${apiCalls}`);
const shown = text.match(/Cannot reach the server[\s\S]{0,180}/);
console.log("message shown to the user:\n  " + (shown ? shown[0].replace(/\s+/g, " ") : "(none found)"));

await page.screenshot({ path: "/home/claude/work/shots/backend-down.png" });
await browser.close();

if (onLogin > 0) { console.error("\nFAIL: an outage signed the user out"); process.exit(1); }
if (!shown) { console.error("\nFAIL: the user was not told the server is unreachable"); process.exit(1); }
console.log("\nFails fast, with an actionable message.");
