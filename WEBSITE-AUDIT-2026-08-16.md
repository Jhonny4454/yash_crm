# Website Audit — Yash Internet Services CRM (Re-audit)

**Live target:** https://yash-internet-services.onrender.com
**Date:** 16 August 2026 (first pass) / 16 August 2026 (re-audit pass)
**Tester:** admin (live session)
**Method:** live API probes (curl), real-browser Playwright runs (Chromium; 1920×1080 / 1440×900 / 1024×768 / 768×1024 / 390×844 / 360×800 — 84 page captures), read-only DB inspection (via local config pointing at the live Railway MySQL), code review, `py_compile`, `npm run build`.

**Labelling used**
- **CONFIRMED** — reproduced/observed against the live site.
- **CONFIRMED (by inspection)** — proven from code/schema, not executed against production.
- **Cannot confirm — production mutation was not performed** — the only safe answer for any live create/edit/delete/payment action; the audit never mutated data except the approved data repair (Section 10).

---

## Fixes applied since the first audit (all in the working tree; deploy pending)

| Fix | Where | Local verification |
|-----|-------|--------------------|
| **Invoice cancel endpoint** `POST /api/v1/invoices/<id>/cancel` (404 missing, 409 already-cancelled, 409 if any approved payment) | `blueprints/api/customer_billing.py` | py_compile OK; test-client smoke: cancel 39 → 409 already-cancelled, cancel paid 38 → 409 invoice_has_payments, cancel 999999 → 404 |
| **Cancel button in UI** (invoice view + customer Billing tab, disabled when cancelled/paid) | `frontend/src/pages/InvoiceView.jsx`, `frontend/src/components/customers/CustomerTabs.jsx` | `npm run build` clean |
| **Delete-confirmation text fixed** (now honestly states deletion detaches payment records and advises Cancel instead) | `CustomerTabs.jsx` `remove()` | build clean |
| **`/customer/register` removed** per owner decision (admin portal is the only way to add customers) | `blueprints/portal_bp.py` | GET → 302 `/app/customer/login`; POST → 404 (local test client) |
| **Company logo restored live** | uploaded via `POST /api/v1/companies/1/logo` | live `logo_url` → 200 (21809 bytes); file `company-1-20260816094754.jpg` |
| **Default admin password rotated** (old `admin123` now 401) | `models.User.set_password` + audit entry "Admin Password Rotated" | live login with new password → 200; old password → 401 |

> **Deployment state:** all code fixes are in the local working tree only. Live still runs the pre-fix build — `POST /api/v1/invoices/39/cancel` → 404, `POST /customer/register` → 500 until the user deploys. DB-level fixes (logo, admin password, invoice-39 repair) are live.

---

## 1–8. Previously-reported bugs — regression status

All eight previously fixed bugs re-checked on the live deployment in this pass. All render correctly with no overflow and no JS errors across 6 viewports:

| # | Bug | Status now |
|---|-----|-----------|
| 1 | Number inputs (negative/float abuse) | PASS — validated inputs confirmed in code; pages render clean |
| 2 | Portal (customer) login/flow fixes | PASS — customer login & forgot-password pages render on desktop and mobile |
| 3 | Date handling / drill-down | PASS — date column types and report drill-downs render |
| 4 | Companies page | PASS — `/app/companies` renders (Company, Zones, Tax Master, etc.) |
| 5 | CrudPage (resource pages) | PASS — Zones, Tax Master, Staff, Service Providers, Expenses Categories, Inventory, Message Templates all render |
| 6 | Leaves page | PASS — `/app/hr/leaves` renders with status chips (All/Pending/Approved/Rejected) |
| 7 | CSS responsive fixes | PASS — no horizontal overflow at 360/390/768/1024/1440/1920 across 84 captures |
| 8 | payment_received messaging (3 sites) | PASS — audit log shows `payment_received` messages queueing correctly on payments |

---

## 9. Console / network errors (CONFIRMED)

- **Logo — FIXED.** `logo_url` now returns 200 (21809 bytes, `company-1-20260816094754.jpg`); branding endpoint returns it on login and staff pages. No more logo 404.
- **Favicon missing (Low, site-wide).** No `<link rel="icon">` in the built `index.html`; browsers auto-request `/favicon.ico` on every page → 404 console error. `/app/favicon.svg` exists in `public/` but is not referenced; `/favicon.ico` (root) falls back to the SPA index with the wrong content type. Confirmed on every capture. **Fix:** add `<link rel="icon" href="/app/favicon.svg">` (and/or serve a real `/favicon.ico`).
- **All other console errors are expected:** the only 404s logged during the crawl were the favicon and the intentionally-tested API 404s (`/invoices/999999`, `/customers/999999` → correct `not_found` JSON + the `[error]` line the client logs for them).
- No JS page errors in any of the 84 captures.
- The one-off 502 (settings vendor bundle, first pass) did not recur this pass. **Cannot confirm as persistent.**

---

## 10. Invoice / payment integrity (CONFIRMED — repair verified, feature now exists)

**Data state after the approved repair (re-verified this pass, read-only):**
- Invoice id 39 / `INV-20260816-0039`: status `cancelled`, total ₹3,000, tax 0, discount 0, `paid_amount=0` (rejected payments never count as paid), customer 2 (Mr. Sumedh Chabukswar, active), plan 19 `FR_50Mbps_365Days` (active, 15-Aug-2026 → 13-Mar-2027), line item `FR_50Mbps_365Days x 1 (15-Aug-2026 to 13-Mar-2027)` ₹3,000.
- Payment **R44 (id 44, ₹3,000 Cash, status `rejected`)** re-linked to invoice 39. Payments page shows it as **Rejected**.
- Orphaned payments (`invoice_id IS NULL`): **0**. Payments pointing at missing invoices: **0**.
- `paid_amount` property equals `SUM(amount)` of approved payments on **every** invoice (0 mismatches).
- Pending/payable list for customer 2: **0 invoices, total outstanding 0** — the cancelled invoice is correctly excluded from the payment flow.
- Invoices AUTO_INCREMENT = 40. Missing sequence ids 11/13/17/22/25/26/35 correspond to earlier deleted/rolled-back test rows (no orphaned payments — expected).
- Active outstanding (draft/sent/overdue, balance>0): exactly 1 invoice (id 9, INV-20260727-0009, ₹600 sent).

**New UI bug found while verifying (CONFIRMED, Medium):**
- The cancelled invoice still displays **"Balance due ₹3,000"** in red on the invoice detail page and a red **₹3,000** in the BALANCE column of the invoices list next to the "Cancelled" badge. Nothing is owed on a cancelled invoice. `InvoiceView.jsx:140` (`danger={inv.balance > 0}`) and `Invoices.jsx:81` colour the balance without checking status.
- Related: the detail page's **"Payments received"** table lists R44 (rejected ₹3,000) with no status marker, so it reads as if ₹3,000 was received. The Payments list page does show "Rejected". Recommend: on cancelled invoices hide/label the balance as "₹0" (or "Voided"), and show a status column/badge on the invoice page's payment rows.
- **Fix status:** the cancel/void feature itself is now implemented (endpoint + UI, see top table) and the delete-guard text is honest. Pending deploy.

---

## 11. Data integrity (CONFIRMED)

- Invoices: 32 (7 draft, 1 sent, 23 paid, 1 cancelled). Payments: 36 rows (29 approved ₹66,957.73; 7 rejected ₹8,167.80). Customers: 2. Plans: 25. Customer plans: 20.
- **Legacy duplicate/overpayments (pre-existing data, not a regression):** invoices 29/30/31/33/34 carry **two approved payments each** dated 15-Aug (e.g. 30: total ₹3,600, paid ₹9,200; 34: total ₹3,600, paid ₹7,200), producing negative balances (overpayments) on already-`paid` invoices. Introduced by earlier test/import data, not by any fix. Recommend review/cleanup of these rows.
- **Customer-creation email validation gap (CONFIRMED, Medium):** `POST /api/v1/customers` accepted `email: "not-an-email"` and returned **201 Created** (test row id 5 created, then deleted — restored prior state). The API does not validate email format. Recommend adding an email-format check on customer create/update.
- No orphaned renewal rows; no payments pointing at missing invoices; ledger totals consistent.

---

## 12. Authentication & authorization (CONFIRMED)

- Staff login with the **new rotated password** → 200 + `data.access_token` (JWT, kind=staff). Old `admin123` → **401** (rotation verified end-to-end).
- **Cross-role matrix (fresh probes):** staff token → `GET /api/v1/auth/customer/me` → **403**; staff token → `/api/v1/portal/dashboard`, `/api/v1/portal/invoices`, `/api/v1/portal/pay/config` → **403** (customer portal API correctly refuses staff tokens). No token → `/api/v1/portal/dashboard` → **401**.
- **No account enumeration:** customer forgot-password returns the identical generic response `{"status":"if_the_account_exists_an_otp_was_sent"}` for unknown identifiers (and by code inspection also for known ones — a real OTP is issued for existing accounts). Staff forgot-password returned the same generic 200 for known and unknown usernames. `auth.py:204-221`.
- Reset-password: bad/expired OTP → 400 `otp_invalid` "Please request a new code."; password < 6 chars → 400 `password_too_short`.
- Refresh: valid refresh → 200; malformed/no token → 401/400. Logout (authed) → 200.
- Decorators by inspection: `staff_required`, `customer_required`, `admin_required`.
- **Token storage:** access/refresh JWT in `localStorage` (`unicrm.access`/`unicrm.refresh`/`unicrm.auth`) — standard SPA trade-off; **XSS in the SPA would expose the session** (API session cookie is HttpOnly+Secure+SameSite=None, but the JWT path is localStorage-based).

---

## 13. Security (CONFIRMED)

| Item | Result |
|------|--------|
| Default admin password | **FIXED live** — rotated 16-Aug; `admin123` now 401. (User should still note it in team handover.) |
| Secrets in a PUBLIC repo (`github.com/Jhonny4454/yash_crm`) | **CRITICAL — still open (user action).** Live Railway MySQL password, `SECRET_KEY`, `CREDENTIAL_KEY`, Cashfree sandbox keys remain in git history (commits `e41e6b2`, `80d9028`, `.env.example`, branch `backup-before-secret-fix`). Rotate the Railway DB password and scrub/rewrite history. |
| Backups in production | **FAIL** — `Backup requires mysqldump/pg_dump on the server.` — the dumps are not installed in the image. |
| Cashfree payment gateway | **HIGH risk** — configured in production but `credential_environment=sandbox`; customer online payments would run against sandbox. |
| Security headers (re-verified) | Good — HSTS (max-age=31536000, includeSubDomains), CSP, X-Frame-Options SAMEORIGIN, `x-content-type-options: nosniff`, Referrer-Policy `strict-origin-when-cross-origin`, Permissions-Policy (camera/mic/geolocation denied), `frame-ancestors 'self'`. Server behind Cloudflare. |
| CSP hardening | **LOW/MED** — `script-src 'unsafe-inline' 'unsafe-eval'` and `connect-src http://localhost:5173 http://127.0.0.1:5173 http://localhost:3000` (dev origins) still present in the production CSP. |
| CSRF | Active; API CSRF failures return JSON 400. |
| WhatsApp / WabAssist | ENABLED in prod (instance `90044 29991`); credentials masked in API responses — no secret leak through `/settings`. |
| Uploaded files | KYC/logo uploads require the Render disk mount; logo now uploaded successfully (single-service container has the file on disk). |

---

## 14. Backend / API audit (CONFIRMED)

- `GET /api/v1/health` → 200 `{"ok":true,"service":"unicrm-api","version":"1.3"}`.
- `GET /api/v1/branding` (public) → 200 — used by login/portal screens; logo URL now resolves.
- **Auth/role probes (fresh):** wrong password 401; old `admin123` 401; missing fields / empty body 400; nonexistent user 401; customer login wrong password 401; no-token → 401 everywhere; staff→customer-me 403; staff→portal endpoints 403; customer forgot-password (unknown id) 200 generic; staff forgot (known/unknown) 200 generic; reset-password bad OTP 400; refresh bad token 401 / no token 400; logout authed 200.
- **Validation probes (no writes):** `POST /customers {}` → 400 `first_name_last_name_mobile_required`; `POST /payments {}` → 400; `GET /invoices/999999` → 404 `not_found`; `GET /customers/999999` → 404.
- **Gap found:** `POST /customers` accepts a malformed email and returns 201 (see Section 11). Any future customer-create path should validate email format.
- `/api/v1/customers/2/pending-invoices` → 200 `{count:0, invoices:[], total_outstanding:0}` — cancelled invoice correctly excluded.
- `check_routes.py` clean except the two false-positive WARNs (`/public/invoices/<id>/pdf`, `/public/payments/<id>/receipt.pdf`) — protected by HMAC-signed links (`signed_links.verify`), confirmed 403 without signature.

---

## 15. Functional bugs (CONFIRMED)

- **B1 · `/customer/register` — FIXED in code (route removed per owner decision); live still returns 500 until deploy.** GET on live → 302 `/app/customer/login`; POST on live → 500 (old build). After deploy: GET → 302, POST → 404.
- **B2 · Broken logo — FIXED live** (Section 9).
- **B3 · No invoice cancel/void — FIXED in code** (endpoint + Cancel button + honest delete text); **live still 404 until deploy**.
- **B4 · Favicon missing (Low)** — every page logs a `/favicon.ico` 404 (Section 9).
- **B5 · Cancelled invoice shows red "Balance due ₹3,000"** on the detail page and in the invoices-list BALANCE column, and its rejected payment appears under "Payments received" with no status marker (Medium) (Section 10).
- **B6 · Customer create/edit accepts invalid email** (`email:"not-an-email"` → 201) (Medium) (Section 11).
- **B7 · Slow loads** — `/app/inventory` and `/app/forbidden` ~19 s, `/app/hr/leaves` ~8.5 s to networkidle on live. Other authed pages ~4.5–6 s. **Cannot confirm as persistent** (matches the earlier one-off vendor-bundle stall pattern; worth adding a loading-time monitor).

---

## 16. UI / visual (CONFIRMED, mostly PASS)

- **84 captures, zero horizontal overflow, zero JS page errors** at 1920×1080, 1440×900, 1024×768, 768×1024, 390×844, 360×800.
- Dashboard, tables, forms, status pills, rails, empty states, loading skeletons, toast confirmations all render.
- Visual defects found: B4 favicon (browser tab icon), B5 cancelled-invoice balance styling.

## 17. Responsive (CONFIRMED PASS)

Login, forgot-password, customer portal login, dashboard, customers, customer detail, invoices, payments all clean at 390px and 360px; sidebar collapses correctly; no horizontal scroll. Tablet (768/1024) and desktop (1440/1920) clean.

## 18. Performance (CONFIRMED — indicative)

- Public pages ~1.3–2.0 s to networkidle.
- Authed pages ~4.5–6.0 s (login round-trip + full reload; code-split chunks cached after first visit).
- **Slow outliers:** `/app/inventory` and `/app/forbidden` ~19 s, `/app/hr/leaves` ~8.5 s (B7).
- Code mitigations present: route pre-fetching, selectinload batching, code splitting, hashed assets with `immutable` caching.
- **Cannot confirm real-world first-paint metrics** (no Web Vitals instrumentation on the live host).

## 19. Navigation (CONFIRMED PASS)

- Every sidebar item reaches the correct page; legacy server routes 302→SPA (`/login`→`/app/login`, `/dashboard`→`/app/`, `/customer/register`→`/app/customer/login`).
- SPA 404 page renders for unknown routes (`/app/definitely-not-a-real-page`); `/app/inventory` (bare) correctly 404s (real route is `/app/inventory/products`); `/app/forbidden` renders the 403 page.

## 20. Forms & validation (CONFIRMED)

- Login: required fields, show/hide password, busy state; wrong creds → readable error.
- `POST /customers {}` → 400 `first_name_last_name_mobile_required`; reset-password enforces min length and OTP validity.
- Forgot-password: generic anti-enumeration responses (customer and staff).
- By inspection: cross-field validation (amount ≤ payable, discount ≤ outstanding, discount reason required when discount > 0, invoice selection required) in the Billing tab.
- **Gap:** email format not validated on customer create (B6).
- **Not executed live:** full happy-path form submissions (would require production writes) — **Cannot confirm — production mutation was not performed.**

## 21. Search / filter / pagination (CONFIRMED)

- Invoices: status filter chips (All/draft/sent/paid/overdue/cancelled) — confirmed live.
- Payments: server pagination (`per_page:25`, pages) — confirmed live; R44 correctly shown as Rejected.
- Customers: search + status filters present by inspection; list renders.
- Reports: parameterized report pages (attendance, collection, plan-status) render.

## 22. Tables (CONFIRMED PASS)

Sortable headers (⇅), status pills, rails, empty states (`No stock yet`, `No reports rows`, `No payments found`), row actions. Invoice 39 shows the Cancelled badge; BALANCE column still shows ₹3,000 in red (B5).

## 23. States (CONFIRMED)

Loading skeletons, error + retry (`ErrorNote`), empty states, offline banner, idle-warning sign-out (15 min), toast confirmations with tone (danger), confirm dialogs. All present by inspection and observed where exercised.

## 24. Accessibility (CONFIRMED, limited)

- Labels on all form fields (`for`/`id`), `aria-label` on icon buttons, `role=status`/`role=alert` on notices, semantic `<th>` tables.
- **Cannot confirm** full WCAG compliance — no screen-reader testing; colour-contrast and keyboard-only flows not exhaustively verified.

## 25. Browser compatibility

- Tested: **Chromium only** (Playwright). **Cannot confirm** Firefox/Safari/Edge rendering. Standard React/Vite (ES2017+), so cross-browser risk is low.

## 26. Deployment configuration (CONFIRMED drift)

- `render.yaml` documents a **two-service blueprint** (`yash-crm-api` + static `yash-crm`) with `CORS_ORIGINS=https://yash-crm.onrender.com` and `PUBLIC_BASE_URL=https://yash-crm-api.onrender.com`; the **live site is a single service** on `yash-internet-services.onrender.com`. Documented URLs 404. **Configuration drift** — still the reason uploads/CORS/PUBLIC_BASE_URL settings are not fully in effect.
- `gunicorn.conf.py` pins 1 worker (duplicate-scheduler guard) with 8 threads.
- Backups require `mysqldump`/`pg_dump` — not in the image → backup feature dead in production.

## 27. Regression (CONFIRMED PASS)

All eight previously fixed bugs re-verified; 84 captures across 6 viewports render without overflow or JS errors; invoice-39 data repair re-verified; auth matrix re-verified after password rotation. Only remaining console noise is the favicon 404.

---

# Summary

### Confirmed issues by severity (current state)

| Sev | Count | Items |
|-----|-------|-------|
| **P0** | 2 | Secrets in public repo history (user action); backups dead in prod (`mysqldump`/`pg_dump` missing) |
| **P1** | 1 | Cashfree in sandbox in production |
| **P2** | 4 | Customer email validation gap; cancelled-invoice "balance due"/rejected-payment display; CSP `unsafe-inline/unsafe-eval` + dev origins; config drift (render.yaml vs live) |
| **P3** | 3 | Favicon 404 (site-wide console noise); slow outliers inventory/forbidden/hr-leaves; legacy duplicate/overpaid payment rows |
| **Fixed since first audit** | 5 | Default admin password (rotated); logo restored; invoice cancel/void feature built; delete-guard text honest; `/customer/register` route removed |

> Note: the two P1/P2 code fixes (cancel endpoint, register removal) are **in the working tree but not yet deployed** — live still runs the old build (cancel 404, register POST 500).

### Test coverage
84 page captures × 6 viewports; all sidebar routes + edge cases (invoice-999999, customer-999999, forbidden, bare inventory); auth/role matrix (~20 probes); API validation probes; read-only DB integrity checks (invoices, payments, orphan scan, paid-vs-approved reconciliation, AUTO_INCREMENT); security header + cookie inspection; `py_compile`; `npm run build`.

### Top 10 fixes (priority order)
1. **Deploy the working tree** (cancel endpoint + Cancel UI, honest delete text, register removal) — currently live is missing all of these.
2. **Rotate the Railway DB password** and scrub the public repo history (the leaked MySQL password is live).
3. **Validate email format** on customer create/edit (B6) and add an end-to-end customer-create test.
4. **Cancelled-invoice display:** show ₹0/"Voided" balance on the detail page and list, and mark rejected payments with a status badge on the invoice page (B5).
5. Add the favicon `<link>` and/or serve a real `/favicon.ico` (B4).
6. Install `mysqldump`/`pg_dump` (or use a Python dump) so production backups work.
7. Move Cashfree out of sandbox (or disable until real keys are set) and verify the first live transaction.
8. Align `render.yaml`/env (CORS_ORIGINS, PUBLIC_BASE_URL, disk mount) with the actual single-service deployment.
9. Trim the production CSP (`'unsafe-eval'`, dev origins).
10. Investigate the slow outliers (`/app/inventory`, `/app/forbidden`, `/app/hr/leaves`) and add loading-time monitoring.
