# Your feature list, checked against the code

You sent a long wishlist. Rather than claim I built all of it, here is every
item with an honest status. Checked by reading the code and by clicking every
screen against a seeded database.

Legend: **✅ already worked** · **🆕 built in this pass** · **⚠️ partial** · **❌ not there**

---

## 👤 Customer portal

| Feature | Status | Notes |
|---|---|---|
| Dashboard | ✅ | Now with an expiry countdown banner and quick actions 🆕 |
| View active plan / speed / details | ✅ | |
| **Renew plan** | 🆕 | Was gateway-only and dead without Cashfree keys. Now works with or without a gateway. |
| **Upgrade / downgrade plan** | 🆕 | Pick any active plan at renewal; the switch applies on approval. |
| **Duration choice (1/3/6/12 cycles)** | 🆕 | Price and new expiry shown live before committing. |
| **Plan renewal history** | 🆕 | `/customer/renew/history` — requests + plan history, with status and reason. |
| Billing history | ✅ | |
| Download invoices | ⚠️ | Print view (`/customer/invoices/<id>/print`) is browser "Save as PDF". No server-side PDF. |
| **Submit payment (amount + UPI/UTR + screenshot)** | 🆕 | `/customer/payments/new`. Validates duplicates and overpayment. |
| Payment history | ✅ | |
| **Payment status (Pending/Approved/Rejected)** | 🆕 | Plus the rejection reason shown back to the customer. |
| Profile management | ✅ | |
| Change password | ✅ | |
| Mobile verification | ✅ | OTP over WhatsApp/SMS at registration and password reset. |
| Email verification | ❌ | No SMTP is wired up — see "Email" below. |
| Notifications | ✅ | In-app inbox + push templates. |
| Download receipts | ✅ | `/customer/payments/<id>/receipt` |
| **Auto logout after inactivity** | 🆕 | 15 min idle, with a 60-second warning so nobody loses a half-typed entry. |
| Maintenance announcements | ❌ | Not built. |

## 👨‍💼 Admin

| Feature | Status | Notes |
|---|---|---|
| Dashboard with statistics | ✅ | Includes charts. |
| Customer / staff / plan CRUD | ✅ | |
| Renewal management | ✅ | Existing renewals queue by due bucket. |
| **Payment approval / rejection** | ✅ 🆕 | Existed; now rejection captures a reason and approval also applies the renewal. |
| **UTR verification** | 🆕 | `/admin/utr-search` — search any reference, verify inline. |
| **Portal activity queue** | 🆕 | `/admin/portal-activity` — payment entries with proof, renewals, plan changes, tickets, all in one place with a nav badge. |
| Bill / invoice generation | ✅ | Manual, addon, and automatic nightly. |
| Customer balance management | ✅ | |
| Notifications management | ✅ | Templates editable in Masters. |
| **User activity logs** | 🆕 | `/admin/activity-log` |
| **Login history** | 🆕 | Same screen — successful and failed sign-ins, with IP. The rows were always being written; nothing read them back until now. |
| Reports | ✅ | Plan expiry, collections, attendance, leaves, payroll, expenses. |
| Export CSV | ✅ | `/settings/export/<target>` |
| Export Excel / PDF | ❌ | CSV only. |
| Search & filters | ✅ | Now also UTR/reference search 🆕 |
| Role-based permissions | ⚠️ | `admin` vs `support`/`field`/`accounts`, enforced by `@admin_required`. Not a per-permission matrix. |
| Backup & restore | ✅ | `/settings/backup` |
| Banner management | ❌ | Not built. |

## 💰 Billing & payments

| Feature | Status | Notes |
|---|---|---|
| Monthly / manual bill generation | ✅ | |
| Auto due date | ✅ | `INVOICE_DUE_DAYS` |
| Outstanding balance | ✅ | |
| Payment reminders | ✅ | Nightly — **and these were silently broken before this work; see FIXES_APPLIED.md** |
| **UPI payment entry** | 🆕 | |
| **UTR verification** | 🆕 | |
| Payment receipts / status / history | ✅ | |
| Partial payment support | ✅ | Balance is computed per payment. |
| Advance payments | ⚠️ | Paying early extends from the current expiry, so nothing is lost. No separate wallet ledger. |
| Wallet | ❌ | Deliberately skipped — it changes the money model and deserves its own pass. |

## 📄 Invoices

Auto numbers ✅ · GST ✅ (per-item tax percent + GSTIN on company/customer) ·
Print ✅ · Invoice history ✅ · **Download PDF** ⚠️ (browser print-to-PDF) ·
**Email invoice** ❌ (`send_email()` only writes to the log — no SMTP configured).

## 🌐 Plans

Create / edit / delete ✅ · Upload/download speed ⚠️ (single `speed_mbps`) ·
Monthly/quarterly/yearly validity ✅ 🆕 (via the duration picker) ·
Plan categories ⚠️ (`plan_type` is a free-text field) · Data limit ❌ ·
Promotional offers ❌.

## 🔄 Renewals

Renew same plan 🆕 · Change plan 🆕 · Advance renewal 🆕 ·
Renewal reminders ✅ (fixed) · Renewal history 🆕 · Pending renewals 🆕 ·
Admin approval 🆕.

## 📢 Notifications

Renewal / payment / bill / expiry templates ✅ · **Payment approved,
payment rejected, payment submitted, renewal approved** 🆕 (four new templates,
seeded on boot) · WhatsApp ✅ · SMS ⚠️ (same gateway hook) · Email ❌.

## 📊 Reports

Daily collections ✅ · Monthly revenue ✅ · Expired / active customers ✅ ·
Pending payments ✅ · Outstanding dues ✅ · Customer growth ⚠️ (data is there,
no dedicated chart) · Renewal reports ⚠️ (the new `renewal_requests` table
holds the data; no report screen yet).

## 🔐 Security

CSRF ✅ · Password hashing ✅ (scrypt) · Secure sessions ✅ ·
Failed-login lockout ✅ (5 attempts / 5 min) · Audit logs ✅ →
**now viewable** 🆕 · Role-based access ⚠️ (see above).

## ⚙️ Settings

Company profile ✅ · Logo ✅ · GST details ✅ · Bank details ✅ ·
Invoice settings ✅ · Backup settings ✅ ·
**UPI ID** ⚠️ — the portal payment screen reads a `upi_id` setting and shows it
with a copy button when present. Add the row under Settings and it appears.

## 📱 Responsive

Desktop / tablet / mobile ✅ · Dark mode ❌.

## 🚀 Advanced

Referral system ✅ · Bulk import/export ✅ · Bulk notifications ✅ ·
Dashboard charts ✅ · API support ✅ (188 JWT endpoints) ·
Multiple admin accounts ✅ · Customer document upload ✅ (ID/address proof) ·
**KYC management** ⚠️ (documents upload but there is no verification workflow) ·
Usage graph ❌ · Outage announcements ❌ · QR code for UPI ❌ ·
Coupons / discount codes ❌ (flat and percentage discounts exist) ·
Scheduled maintenance messages ❌ · File manager ❌.

---

## What I deliberately did not build

Four things on your list change the money model or need infrastructure you
have not set up, and guessing at them would have been worse than leaving them:

1. **Wallet / advance balance ledger** — needs decisions about refunds,
   expiry of credit, and how it interacts with partial payments.
2. **Email anything** — `send_email()` is a stub. It needs SMTP credentials
   (host, port, user, password, from-address) before it can do more than log.
3. **PDF generation** — the print views are already styled for it; a real
   server-side PDF needs WeasyPrint or wkhtmltopdf added to the image.
4. **UPI QR codes** — trivial to add (`qrcode` + your VPA), but I did not want
   to render a payment QR pointing at a UPI ID I could not verify.

Say the word on any of these and I will build them next.
