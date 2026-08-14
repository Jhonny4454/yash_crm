# What changed in this release

This is the full list of what was broken, what was missing, and what is new.

---

## 0. The app could not start at all

`app.py` line 10 did `from api import api`, but **the entire `blueprints/api/`
source folder was empty** — only stale `.pyc` files were left in the zip, and
`models_api.py` was gone too. Any `python app.py` / `gunicorn wsgi:app` died
immediately with `ModuleNotFoundError: No module named 'api'`.

The `blueprints/api` docstring also expected a different entry point
(`register_api(app, csrf=csrf)`) than the one `app.py` was calling, so even
with the files present the wiring was wrong.

**Fixed.** The whole REST API package has been rebuilt and correctly mounted.

---

## 1. Rebuilt: `blueprints/api/` — the REST API (`/api/v1`)

188 endpoints across 11 modules, all mounted through
`register_api(app, csrf=csrf)`.

| File | What it does |
|---|---|
| `__init__.py` | Mounts every sub-blueprint, CSRF-exempts `/api/v1`, adds CORS |
| `utils.py` | JWT issue/verify, `staff_required` / `admin_required` / `customer_required`, pagination, `ok()` / `fail()` envelopes |
| `serializers.py` | One place that builds every JSON payload — including `company_branding()`, the single source of truth for the invoice logo |
| `auth.py` | Staff + customer login, `/me`, change password, refresh, logout |
| `resources.py` | Dashboard KPIs, customers, plans, invoices, payments, authorise/reject |
| `masters.py` | Generic CRUD factory — 20 master tables get list/get/create/update/delete from one line each |
| `portal.py` | Customer app: dashboard, invoices, payments, plans, tickets, renew, change plan, Cashfree order + signed webhook |
| `dashboard.py` | `/dashboard/summary` — rewritten to a handful of `GROUP BY` queries instead of ~33 per-row queries |
| `company.py` | Multi-company, logo upload, notification templates, notifications, device tokens |
| `staff.py` | Staff CRUD, plan-expiry / attendance / leaves / payroll / collection / expense reports, customer ledger |
| `integrations.py` | Settings, backups, CSV import/export, message log, bulk WhatsApp, ISP credentials |

Auth is stateless JWT (`Authorization: Bearer …`). Two token kinds —
`staff` and `customer` — so the admin SPA and the mobile app cannot use each
other's tokens.

New config in `config.py`: `JWT_SECRET_KEY`, `JWT_ACCESS_HOURS`,
`JWT_REFRESH_DAYS`, `CORS_ORIGINS`, `CASHFREE_RETURN_URL`, `INVOICE_DUE_DAYS`.
`PyJWT==2.8.0` added to `requirements.txt`.

---

## 2. Rebuilt: `models_api.py`

`DeviceToken`, `NotificationTemplate`, `Notification`, and
`seed_notification_templates()` (5 default templates, seeded on boot).

`mysql/migrations_api.sql` creates these three tables on an existing MySQL
database, plus 8 standard addon-charge categories. Fresh installs get them
from `db.create_all()` automatically.

---

## 3. New: the rest of the customer portal — `blueprints/portal_bp.py`

Before this, a customer could log in, see a dashboard, change their password
and renew. That was it. Now:

| Route | Screen |
|---|---|
| `/customer/register` | **Activate a portal login** using reference ID or registered mobile, verified by a 6-digit OTP over WhatsApp/SMS |
| `/customer/forgot-password` → `/customer/reset-password` | **Forgot password** with the same OTP flow |
| `/customer/invoices` | Bill list, filterable All / Unpaid / Paid |
| `/customer/invoices/<id>` | Bill detail with a Pay button and the payment history for that bill |
| `/customer/invoices/<id>/print` | Print / save-as-PDF |
| `/customer/payments` | Payment history + a log of online payment attempts |
| `/customer/payments/<id>/receipt` | Printable receipt |
| `/customer/plans` | Current plan, all published plans, upgrade/switch, plan history |
| `/customer/plans/<id>/request-change` | Raises a plan-change bill; the plan switches the moment it is paid |
| `/customer/pay/<invoice_id>` | Pay **any** open bill online, not just the renewal one |
| `/customer/tickets` + `/new` + `/<id>` + `/<id>/close` | Support tickets with common-issue presets |
| `/customer/notifications` | In-app alert inbox (auto-marks read) |
| `/customer/messages` | WhatsApp / SMS history for that customer |

`templates/customer/base.html` now has a real navigation bar (Home, Bills,
Payments, Plans, Support, Alerts with an unread badge, Profile).

**Bug fixed:** `templates/customer/login.html` had a complete second HTML
document appended after `{% endblock %}` — 120 lines of dead markup Jinja
silently discarded. Removed, and the login page now links to Register and
Forgot Password.

---

## 4. New: admin renewal — `blueprints/renewals_bp.py`

### `/customers/<id>/renew` — the full renewal screen

The old renewal was a bare POST endpoint with no screen. The new one does in
one submit what used to take three:

- pick the plan (keeping it = renewal, changing it = the old plan is closed
  and a new `CustomerPlan` row starts)
- renew for 1 / 2 / 3 / 6 / 12 periods, with the amount and expiry date
  auto-calculated and both overridable
- **renewing early does not lose paid-for days** — the new expiry counts from
  the current expiry, not from today
- discount + GST (none / add on top / already included), live-totalled in the
  browser and re-checked server-side
- raises the invoice with a proper `InvoiceItem` line
- optionally collects the payment in the same submit (mode, reference, book
  receipt no); non-admin entries land in the authorisation queue
- re-enables a disconnected customer on the ISP
- sends the WhatsApp/SMS confirmation
- "Renew & print" goes straight to the printable invoice

### `/renewals` — the renewal queue

Four buckets (already expired / expiring today / due in 7 / due in 30) with
live counts, zone and text filters, per-row outstanding balance, and a sticky
action bar: **bulk renew selected** and **send reminder / expiry notice** to
the selection.

### `/customers/<id>/addon-charge` — shifting, device and other charges

Multi-line charge entry with one-click presets from your Addon Invoice
Categories (Shifting, Installation, ONT, ONU, Router, Cable, Reconnection,
Other), optional link to an inventory product with serial number capture,
discount, GST, live totals, and optional payment collection. Raises a proper
`addon`-type invoice with line items.

`/addon-charges/<id>/delete` removes a wrongly-raised charge — admin only, and
refuses if any payment has already been credited against it.

The customer page now has **Renew Plan** and **Addon Charges** buttons, and
the main nav has a **Renewals** menu.

---

## 5. New: referral campaigns — `blueprints/referral_bp.py`

`templates/referral/{index,add,edit}.html` and the `ReferralCampaign` /
`Referral` models were already in the project, but **no route ever rendered
them** — every link raised a Jinja `BuildError`. Now wired up:
`referral_index`, `referral_add`, `referral_edit`, `referral_toggle`,
`referral_delete`, plus `referral_record` and `referral_mark` for logging a
referral and marking it converted / rewarded.

---

## 6. New: Razorpay counter checkout — `blueprints/gateway_bp.py`

`templates/payments/gateway.html` posted to `url_for('payment_callback')` —
an endpoint that did not exist, so the template could never render.

- `/payments/gateway/<invoice_id>` creates the Razorpay order and opens checkout
- `/payments/callback` verifies the HMAC-SHA256 signature **before** crediting
  anything (a forged POST cannot mark an invoice paid), then writes a normal
  `Payment` row so it shows up in the admin panel like any other

No new dependency — the signature check uses `hmac` from the standard library.
If `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` are blank the screen says so and
sends the operator back, instead of erroring.

---

## 7. New: staff forgot-password — `blueprints/staff_auth_bp.py`

`templates/login.html` had `<a href="#">Forgot password?</a>`. It now points at
a real OTP flow (`/forgot-password` → `/reset-password`) that texts a code to
the mobile number on the staff record. Neither screen reveals whether a
username exists.

New shared layout `templates/base_auth.html` for pre-login pages.

---

## 8. Other fixes

- `login_manager.login_view` and `session_protection` were never set — set now.
- A stray bare `...` statement sat between the login manager setup and the
  blueprint registration in `app.py`. Removed.
- `/customers/plan-status/<status>` 404'd on anything except
  `expiring` / `expired` / `renewed`. It now also accepts `active`,
  `suspended` and `all`.
- `AddonCategory` was imported ad-hoc inside six different view functions.
  Hoisted to the top-level import.
- Notification templates are now seeded on boot alongside the message
  templates and addon categories.

---

## Verification

Three smoke suites were run against a seeded SQLite database:

- **Every GET route** (403 rules) walked with a logged-in admin session:
  115 × 200, 25 × redirect, **0 × 5xx**. The remaining 4xx are the expected
  401s on `/api/v1/*` without a Bearer token, plus 404s for seed IDs that do
  not exist.
- **Customer portal**: all 15 portal screens 200, ticket creation and
  plan-change POSTs succeed, OTP register/forgot flows deliver.
- **REST API**: staff + customer JWT login, 51 staff GETs and 8 customer GETs
  all 200, 10 write endpoints (create zone / plan / customer / ticket, renew,
  change password, send notification, register device, update plan dates,
  save settings) all succeed.
- **Admin write paths**: renewal with GST verified arithmetically
  (₹1000 − ₹100 discount + 18% = ₹1062, tax ₹162), plan change closes the old
  `CustomerPlan` and opens a new one, addon invoice raises 2 line items with
  the serial number captured, bulk renew and bulk reminders both succeed,
  referral CRUD succeeds.

An endpoint audit confirms **every `url_for()` in every template resolves**,
every `render_template()` target exists, and no `href="#"` placeholders remain.

---

## Running it

```bash
pip install -r requirements.txt

# fresh database (creates tables + admin user + all seed data)
python app.py

# existing MySQL database — add the three new tables first
mysql -h HOST -u USER -p DBNAME < mysql/migrations_api.sql
```

Default admin is `admin` / `$ADMIN_PASSWORD` (from `.env`). Change it after
the first login.

> `.env` in this zip still holds your live Railway MySQL URL and
> `CREDENTIAL_KEY`. Rotate those before sharing the folder with anyone, and
> keep `.env` out of git — `.gitignore` already excludes it.
