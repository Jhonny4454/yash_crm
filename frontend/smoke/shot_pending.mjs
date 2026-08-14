import { chromium } from "playwright";
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(() => {
  localStorage.setItem("unicrm.access", "t"); localStorage.setItem("unicrm.refresh", "t");
  localStorage.setItem("unicrm.auth", JSON.stringify({
    audience: "staff", user: { id: 1, full_name: "Admin User", role: "admin" },
    company: { name: "YASH Internet Services" } }));
});
const page = await ctx.newPage();
await page.goto("http://localhost:4173/app/customers/1?tab=pending", { waitUntil: "networkidle" });
await page.waitForTimeout(700);
await page.screenshot({ path: "/home/claude/work/shots/pending-overlap.png" });
console.log("captured pending-overlap");
await browser.close();
