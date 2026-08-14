# YASH CRM — audit findings

Everything below was **measured on a running copy of the app**, not read off the
source. The whole stack was stood up in a sandbox: Flask, a built React bundle,
and a database seeded to **604 customers / 603 plans / 2,205 invoices /
763 payments** — big enough that per-row query storms actually show up.

---

## 1. Fixed

### 1.1 The admin dashboard was a hard 500 — every load, every user

`blueprints/api/dashboard.py`, `dashboard_summary()`

```python
expiring = chips(today, 7, expiry_counts)      # line 166
...
expiry_counts = {row.day: row.count for ...}   # line 189  <- defined HERE
```

The two `chips()` calls had been moved **above** the union query that builds
`expiry_counts`. `GET /api/v1/dashboard/summary` died with:

```
UnboundLocalError: cannot access local variable 'expiry_counts'
                   where it is not associated with a value
```

That is the first screen after login, so the app opened onto a broken panel for
everyone. `dashboard.py` was also the most recently modified file in the
project — this is almost certainly where the previous session was interrupted
mid-refactor.

**Fix:** the three `chips()` calls now sit after the union that feeds them.
Verified: `GET /api/v1/dashboard/summary` → `200`.

### 1.2 Three N+1 query storms

Measured as statements actually emitted per request:

| Endpoint | Before | After | What was lazy |
|---|---:|---:|---|
| `GET /invoices?per_page=100` | **69** | **7** | `invoice.customer_plan.plan` — `display_caption` falls through to the plan name for any bill with no explicit caption and no recorded payment mode, i.e. most unpaid bills |
| `GET /invoices?per_page=25` | 19 | 7 | same |
| `GET /customers/plan-status` | **31** | **6** | `cp.customer` and `cp.plan`, once per row |
| `GET /customers/<id>/ledger` | 9 | 8 | `inv.payments` + `customer_plan.plan` per invoice |

Someone had already fixed the `customer` and `payments` eager loads on the
invoice list ("53 queries became 3" in the comment) — the caption path was the
one that got missed, and it put the page straight back to 69.

**Why this matters more than the numbers suggest.** On local SQLite, 62 extra
queries is a few milliseconds. Your `DATABASE_URL` points at a hosted MySQL
(`sakura.proxy.rlwy.net`), where every one of those is a **network round
trip**. At a typical 30–60 ms RTT that is **2–4 seconds of pure latency** on the
invoice page alone, before any rendering.

**Fix:** `selectinload` added in `resources.py` (invoice list), `staff.py`
(plan-status board) and `customer_billing.py` (ledger).

### 1.3 `/payments/add/<invoice_id>` returned 500 to a logged-in admin

The Jinja templates were deleted during the React migration and a
`before_request` map redirects retired bookmarks to the SPA. Every legacy GET
route was covered **except this one**, so it fell through to
`render_template('payments/add.html')` → `TemplateNotFound` → 500.

**Fix:** added to the redirect map → `/app/invoices/<id>`, which carries the
Record payment action. Verified: `302 → /app/invoices/1`.

Full legacy sweep after the fix: **134 legacy GET routes, 0 errors** (130
redirect to the SPA, 2 serve CSV exports, 2 correctly 404).

### 1.4 Two smaller frontend hardening fixes

- `components/customers/CustomerTabs.jsx` — the Notes box seeds its state from
  the customer prop **once, on mount**, and `CustomerDetail` is not keyed on the
  id. Moving between two customer records without leaving the route would keep
  the previous customer's note text in the box, and "Save note" would then write
  it onto the **new** record. Added `key={customer.id}`. (Low probability — I
  could not construct a normal click-path that triggers it, only browser
  back/forward — but it is a silent cross-record overwrite, so it is worth the
  one line.)
- `pages/PortalPages.jsx` — `readAll()` had no `try/catch`. A failed
  "Mark all read" threw out of the handler: nothing refetched, nothing on
  screen changed, no error shown. Now surfaces an `ErrorNote` with a retry.

---

## 2. "Plan edits revert after refresh" — could not reproduce

This was the headline complaint, so it got the most attention. **The
missing-`db.session.commit()` theory does not hold.**

- An AST scan of all **209 write endpoints** found **0** that mutate without a
  commit (directly or via a helper that commits).
- A round-trip harness ran **create → re-read on a fresh connection → update →
  re-read on a fresh connection → delete → re-read** against **20 resources**.
  Every one persisted. New TCP connection and new HTTP session for each
  re-read, which is as close to "the operator pressed F5" as a script gets.
- Plans specifically: name, plan code, type, speed, price, ISP cost, validity,
  service provider and active flag all survive the round trip.
- The three other plan-editing paths were tested individually and all persist:
  `PUT /customer-plans/<id>` (the Edit Customer Plan modal, including the
  reprice-unpaid-invoices side effect), `PUT /customer-plans/<id>/dates` (the
  expiry board inline edit), and the expiry board's re-read.

**The one place a user could reasonably believe a plan edit was saved when it
was not:** the **`+30d` button on the Plan expiry board**
(`pages/PlanExpiryBoard.jsx:98`). It fills the start and end date inputs
immediately, but only stages the change in local state — persisting requires
pressing **Save** on that row. There is an "N rows with unsaved changes" bar and
a Discard button, so it is discoverable, but a button labelled with a duration
sitting next to date fields that visibly change is exactly the shape of "I
edited it and it reverted."

**What I could not test here:** your production database is MySQL; this audit
ran on SQLite. If the revert is real and reproducible on your machine, the two
remaining candidates are (a) a MySQL-specific schema drift — a column the ORM
writes that does not exist or is typed differently on the live table, and
(b) two frontends talking to two backends (see §5). Tell me the exact screen and
field and I can chase it directly.

---

## 3. Every admin and portal page, driven in a real browser

Headless Chromium, real backend, real data. Logged in as staff and as a
customer, plus the public pages from a clean session.

**65 routes visited — 0 blank pages, 0 uncaught exceptions, 0 failed API
requests, 0 console errors.**

That covers all 53 staff routes, all 6 portal routes, and 6 public/system
routes, with full-page screenshots of each.

*Environment noise, named explicitly:* this sandbox has no route to
`cdnjs.cloudflare.com`, so the Font Awesome stylesheet never resolves. On two
earlier runs that kept Chromium's `networkidle` from firing on the first page
of a fresh context and produced a false "blank page" timeout — a different
route each time, which is what gave it away. Re-driven with
`wait_until="load"`, every affected route rendered correctly, and the final
sweep came back clean on all 65. Nothing to fix in the app.

---

## 4. Write-endpoint audit — 209 endpoints

| Check | Result |
|---|---|
| Missing `db.session.commit()` | **0** |
| Missing authorization | **0** — 17 are intentionally public (staff/customer login, forgot/reset password, token refresh, logout, and the two payment webhooks); every other write is behind `@admin_required` or `@staff_required` |
| Rollback on error | Sound — `@app.errorhandler(Exception)` rolls the session back before returning JSON, and it is registered for `Exception` (not just 500) so it runs in debug too |
| `IntegrityError` handling | The generic masters CRUD rolls back and returns `409 duplicate_or_invalid` rather than a 500 |

Two behaviours worth knowing about rather than fixing blind:

- **`PUT /settings` silently drops unknown keys.** `settings_update()` skips any
  key not in `SETTING_DEFAULTS` and not already a row, then returns
  `{"status": "saved"}` regardless. Verified live: posting an unknown key
  returns 200 `{"count": 0, "status": "saved"}` and the value is gone on
  refresh. The Settings screen only ever sends keys the server handed it, so it
  does not bite today — but it is precisely the "saved, then reverted" shape,
  and it would be better to report the skipped keys.
- **`message_templates.template_type` is UNIQUE.** Creating a second template
  of an existing type fails with `409 duplicate_or_invalid`. That may be
  intentional (one template per event) but the Message templates screen offers
  the type as a free dropdown, so the operator finds out by hitting an error.

---

## 5. Things to check on your side

1. **Run `python upgrade_schema.py` against the live database.** It adds 17
   indexes on the hot columns — `customer_plans(status, end_date)`,
   `invoices(customer_id / status / issue_date)`,
   `payments(invoice_id / customer_id / status / payment_date)`,
   `customers(mobile / zone / is_active)`. These are already written and the
   script is idempotent, but `db.create_all()` never adds an index to a table
   that already exists, so **they only exist if that script has actually been
   run against Railway**. Without them, every dashboard load is a full table
   scan of `customer_plans` and `invoices`. It ran clean here (17 indexes
   created).

2. **Your local `.env` points at the production database with
   `FLASK_ENV=development`.** That combination means: secure-cookie flags off,
   and **full Python tracebacks returned in API error responses** (`app.py`
   only suppresses them when `FLASK_ENV=production` or a hosting env var is
   set). Anyone hitting an error on a locally-run instance sees file paths and
   query values. `render.yaml` sets `FLASK_ENV=production` correctly, so this
   is a local-only exposure — but it is also production data.

3. **Two deployed frontends.** `render.yaml` builds a static SPA pointed at
   `https://yash-crm-api.onrender.com/api/v1`, while Flask also serves its own
   bundle from `/app` out of `frontend/dist`. If `frontend/dist` on the server
   is older than `frontend/src`, `/app` serves stale UI while the static site
   serves current UI — worth confirming which one people actually use.

---

## 6. Dead code found (nothing deleted — listed for you to remove)

I can't delete files on your machine from here. All of these are **provably**
unreferenced — verified by reading each file and checking both import
directions, not just grepping.

**Orphaned React files (nothing imports them):**

```
frontend/src/layouts/Base.jsx                     superseded by AdminLayout/AppShell
frontend/src/components/Footer.jsx                only imported by Base.jsx
frontend/src/components/Topbar.jsx                only imported by Base.jsx
frontend/src/components/MobileSidebar.jsx         only imported by Base.jsx; body is `return null`
frontend/src/components/customers/CustomerTable.jsx      superseded by CustomerTabs
frontend/src/components/customers/CustomerToolbar.jsx    superseded by CustomerTabs
frontend/src/components/dashboard/DuePayments.jsx
frontend/src/components/dashboard/ExpiringPlans.jsx
frontend/src/components/dashboard/MetricCards.jsx
frontend/src/components/dashboard/QuickActions.jsx
frontend/src/components/dashboard/RecentCustomers.jsx
frontend/src/components/dashboard/RecentInvoices.jsx
frontend/src/components/dashboard/RevenueChart.jsx
```

`AdminDashboard.jsx` reimplements all seven dashboard widgets inline. Worth
noting before anyone tries to revive them: each of those files calls
`useFetch("/api/v1/dashboard...")` with a hard-coded `/api/v1` prefix, but
`api/client.js` already prepends `/api/v1` — so wiring them up as-is would
request `/api/v1/api/v1/dashboard`. `DashboardHeader.jsx` is the one file in
that folder that **is** live (used by `AdminLayout.jsx`) — keep it.

**Imported but never routed:** `pages/RecordDetailPage.jsx` is lazy-imported at
`App.jsx:20` and no `<Route>` ever renders it. Superseded by `CustomerDetail`
and `InvoiceView`.

**Dead config:** `RESOURCES` in `pages/ResourcePage.jsx` carries full CRUD
configs for `customers`, `plans`, `companies` and `hr/leaves` that the router
never calls — each has a dedicated, richer page instead.

**Routed but unreachable by clicking:** `reports/collection` and
`reports/expenses` have working routes and working endpoints, but no entry in
the `MENU` array in `menu.jsx` and no link anywhere. Either add them to the
Reports menu or drop them.

**Unreferenced API endpoints:** 38 under `/api/v1`, of which 25 are one
systemic pattern — `CrudPage` never fetches a single record by id (its edit
dialog reuses the row already in the table), so every `GET /<resource>/<id>`
for a pure-CrudPage resource has no caller. Those are cheap to keep. The 13
genuine one-offs include `GET /users`, `GET /staff-types` (a flat duplicate of
`/staff/types`), `GET /invoices/<id>/full`, `POST /payments` (superseded by the
nested `/customers/<id>/payments`), and the push-notification
`POST|DELETE /portal/device-token` pair, which has no registration flow in the
frontend at all.

---

## 7. What was not done

- **Nothing was tested against MySQL.** Everything here ran on SQLite. Schema
  drift between the ORM and your live MySQL tables would not show up in this
  audit, and it is the most likely remaining home for a "revert" bug.
- **No writes were exercised against your production database.** Deliberate.
- **Write-endpoint coverage is static + sampled, not exhaustive live.** All 209
  were checked by AST for commits and authorization; roughly 60 were driven
  live end to end. The remaining ones — billing runs, bulk messaging, ISP sync,
  payment webhooks, backup/restore, CSV import — have side effects (money,
  outbound WhatsApp, external APIs) that should not be fired blind.
- **`GET /reports/plan-expiry` is unpaginated.** At 604 customers it returns
  96 KB in ~115 ms, and almost all of that is Python serialisation, not the
  database. At 10,000 customers it is roughly 1.5 MB per load. Not fixed here
  because paginating it changes the board's UI contract — flagging it as the
  next thing that will hurt.
