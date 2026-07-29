# YASH CRM — What changed, and what you need to do

Read the **Before you deploy** section first. It is short and it matters.

---

## 1. Before you deploy

### Rotate your wabassist password
You pasted `yashinternetservices9@gmail.com` / `uni@U01263` into a chat message.
Change that password now. Nothing in this codebase uses those credentials — you
paste your gateway details into the app's own settings instead.

### Back up your database
Several tables are dropped in this release. Take a backup first:

```bash
# SQLite
cp instance/app.db instance/app.db.backup

# MySQL
mysqldump -u USER -p yash_crm > yash_crm_backup.sql
```

### Tables that are now unused
`wallets`, `wallet_transactions`, `payment_gateway_transactions`,
`referral_campaigns`, `referrals`.

The app ignores them, so nothing breaks if you leave them alone. Drop them once
you are happy the upgrade is stable:

```sql
DROP TABLE IF EXISTS wallet_transactions;
DROP TABLE IF EXISTS wallets;
DROP TABLE IF EXISTS payment_gateway_transactions;
DROP TABLE IF EXISTS referrals;
DROP TABLE IF EXISTS referral_campaigns;
```

### New columns and tables
`db.create_all()` creates the new tables on boot, but **it does not add columns
to a table that already exists**. On an existing database run these once:

```sql
ALTER TABLE payments ADD COLUMN source VARCHAR(20) DEFAULT 'admin';
ALTER TABLE message_templates ADD COLUMN channel VARCHAR(20) DEFAULT 'whatsapp';
ALTER TABLE message_templates ADD COLUMN description VARCHAR(255);
ALTER TABLE message_templates ADD COLUMN updated_at DATETIME;
```

New tables created automatically: `message_logs`, `online_payment_orders`.

---

## 2. Payment authorization now works the way you asked

Money reaches the customer's account the moment it is recorded. Authorization is
a **review step for your records** — it never gates the customer's balance or
their connection.

| Who paid | Balance updated | Appears in authorization queue |
|---|---|---|
| Customer, through the portal | Immediately | Yes, tagged **Online Payment** |
| Staff member, at the counter | Immediately | Yes, tagged **Counter Entry** |
| Admin, at the counter | Immediately | No — auto-signed |

Rejecting a payment is the one action that removes money from the account.

The queue at **Reports → Payment Authorization** shows the transaction ID, the
payment type, and a running total with online payments called out separately.

---

## 3. Connecting WhatsApp

Nothing is hard-coded, because I do not have wabassist's API documentation and I
was not willing to guess at an endpoint and tell you it worked. Instead the
gateway is a configurable HTTP call. Fill these in from your provider's
dashboard, either as environment variables or in the `settings` table:

| Setting key | What it is |
|---|---|
| `wa_enabled` | `1` to actually send, `0` to log only |
| `wa_api_url` | The send endpoint from your provider |
| `wa_api_token` | Your API token / access key |
| `wa_instance_id` | Instance ID, if your provider uses one |
| `wa_http_method` | `POST` (default) or `GET` |
| `wa_payload_template` | JSON body; see below |
| `wa_country_code` | `91` |

`{phone}`, `{message}`, `{token}`, `{instance_id}` and `{sender}` are substituted
into both the URL and the payload. The default body is shaped like most Indian
WhatsApp providers:

```json
{"number": "{phone}", "type": "text", "message": "{message}",
 "instance_id": "{instance_id}", "access_token": "{token}"}
```

**Until you fill this in, nothing is lost.** Messages are rendered in full and
written to the message log with status `dry-run`, so you can check every word of
every template before a single message goes out. Use the **Test** button on
Masters → Customer Templates to send one to your own number.

Numbers are normalised automatically: `98765 43210`, `+91-9876543210` and
`09876543210` all become `919876543210`.

---

## 4. Connecting Cashfree

Set `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY` and `CASHFREE_ENV`
(`sandbox` while testing, `production` when live).

In the Cashfree dashboard, register this webhook:

```
https://your-domain.com/webhooks/cashfree
```

The webhook signature is verified with HMAC-SHA256 against the raw request body,
so a forged POST cannot mark an order as paid. Payment status is always
confirmed server-side — the browser redirect is never trusted on its own.

Leave the credentials blank and online payment is simply switched off: the portal
hides the Pay button rather than throwing errors.

---

## 5. Customer portal

The portal was completely non-functional in the version you sent me:
`CustomerLoginForm` was never imported, and the entire `templates/customer/`
folder was missing. Every portal route returned a 500.

It now works end to end:

- `/customer/login` — rate-limited, blocks disabled connections
- `/customer/dashboard` — plan, days remaining, dues, bills, payments
- `/customer/profile` — change password (current password required)
- `/customer/renew` — Cashfree checkout
- `/customer/invoice/<id>` — print-ready bill

Customers can renew, check their remaining days, print bills and reset their
password. Nothing else.

The old download route depended on `pdfkit`, which was never in
`requirements.txt` and needs a system binary that is not present on Render or
Railway — it could not have worked. Bills now open print-ready and the browser's
own "Save as PDF" handles the rest, which removes the dependency entirely.

---

## 6. Addon Invoice

Rebuilt as a white panel matching your UniCRM screenshots.

- **Charge presets** — one click fills the caption: Installation Charges,
  Shifting Charges, Extra Device, Router, Repair / Service Visit, Cable / Wiring
- **Bill from Vendor** is now a collapsed section, not a dropdown cluttering the
  entry bar. Open it, pick a vendor, add devices from stock. Each line reduces
  stock and raises a purchase bill against that vendor automatically.
- Running invoice total updates live
- Full-width fields on mobile

For a simple charge — say ₹500 installation — you click the preset, type the
amount, and submit. The vendor section stays out of your way.

---

## 7. Interface

- Sidebar collapses to icons; the choice is remembered across pages and sessions
- Header, sidebar and footer are fixed; only content scrolls
- Navigation is defined once as a Jinja macro instead of being duplicated for
  desktop and mobile — one edit now updates both
- Mobile breakpoint raised to 992px so tablets get the mobile layout too
- Direct-download buttons removed from customer plans; Print and Send remain
- WhatsApp icon in the Plan tab, bell icon for due reminders in Overview
- SMS / WhatsApp Log tab per customer

---

## 8. Speed on Render / Railway

| Change | Why |
|---|---|
| `pool_pre_ping` + `pool_recycle=280` | Managed databases drop idle connections. This was almost certainly your main source of slow and failed page loads. |
| Static cache 7 days | The CSS and JS stop re-downloading on every page |
| Chart.js only on the dashboard | ~200 KB removed from every other page |
| Login animations removed | The infinite gradient repainted forever; `backdrop-filter` is one of the most expensive things a phone GPU can do |
| `prefers-reduced-motion` respected | Honours the OS accessibility setting |
| Scheduler `coalesce` + `misfire_grace_time` | A sleeping free-tier dyno no longer fires a backlog of duplicate reminders when it wakes |
| Double-submit guard | Buttons disable briefly on submit, so a slow connection cannot create duplicate payments |

**If you run more than one worker**, set `RUN_SCHEDULER=0` on every worker except
one. Otherwise each worker sends its own copy of every reminder.

---

## 9. What I did not do

Being straight with you about the limits of this pass:

- **I could not test against your real data.** Everything is verified against a
  seeded database in a sandbox. Restore your backup to a staging copy and test
  there before this touches live customers.
- **The WhatsApp endpoint is unverified.** I have no wabassist documentation. The
  transport is correct and configurable; the specific URL and payload shape must
  come from their dashboard.
- **Cashfree is untested against live credentials.** The integration follows the
  2023-08-01 API and verifies signatures properly, but I have no keys to test
  with. Run it in `sandbox` first.
- **Email is still a stub.** It logs rather than sends. No SMTP provider is
  configured and you did not ask for one.
- **I have not audited every one of the ~180 routes** for logic correctness. I
  smoke-tested every page for runtime errors — all pass — but that is not the
  same as verifying business logic.

Do not treat "all tests pass" as "bug free". This is a billing system handling
real money. Test it properly.
