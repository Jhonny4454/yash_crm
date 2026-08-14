import { chromium } from "playwright";
const BASE = "http://localhost:4173/app";
const DEVICES = [["iphone", 390, 844, 3], ["android", 412, 915, 2.6], ["tablet", 820, 1180, 2]];
const ROUTES = [["dash", "/"], ["customers", "/customers"], ["pending", "/customers/1?tab=pending"],
                ["renewals", "/renewals"], ["billing", "/customers/generate-invoice"]];
const browser = await chromium.launch();
const problems = [];
for (const [dev, w, h, dpr] of DEVICES) {
  const ctx = await browser.newContext({
    viewport: { width: w, height: h }, deviceScaleFactor: 1,
    isMobile: dpr > 2, hasTouch: dpr > 2 });
  await ctx.addInitScript(() => {
    localStorage.setItem("unicrm.access", "t"); localStorage.setItem("unicrm.refresh", "t");
    localStorage.setItem("unicrm.auth", JSON.stringify({ audience: "staff",
      user: { id: 1, full_name: "Admin User", role: "admin" },
      company: { name: "YASH Internet Services" } }));
  });
  const page = await ctx.newPage();
  for (const [name, route] of ROUTES) {
    await page.goto(BASE + route, { waitUntil: "networkidle", timeout: 25000 }).catch(() => {});
    await page.waitForTimeout(400);
    // Horizontal overflow is the classic mobile failure: the page scrolls
    // sideways and half the content sits off-screen with no hint it exists.
    const overflow = await page.evaluate(() => ({
      doc: document.documentElement.scrollWidth,
      win: window.innerWidth,
      offenders: [...document.querySelectorAll("*")]
        .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 2
                     && getComputedStyle(el).position !== "fixed")
        .slice(0, 4)
        .map((el) => `${el.tagName.toLowerCase()}.${(el.className || "").toString().split(" ")[0]}`),
    }));
    const over = overflow.doc - overflow.win;
    const flag = over > 4 ? `OVERFLOW +${over}px` : "ok";
    console.log(`${dev.padEnd(8)} ${name.padEnd(10)} ${flag}${over > 4 ? "  " + overflow.offenders.join(", ") : ""}`);
    if (over > 4) problems.push(`${dev}/${name} overflows by ${over}px: ${overflow.offenders.join(", ")}`);
    if (dev === "iphone") await page.screenshot({ path: `/home/claude/work/shots/m-${name}.png` });
  }
  await ctx.close();
}
await browser.close();
console.log(problems.length ? `\n${problems.length} overflow problem(s)` : "\nNo horizontal overflow anywhere.");
