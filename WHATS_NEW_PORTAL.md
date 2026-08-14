# Customer portal renewals & payment entry

What was added in this pass, how it hangs together, and how to run it.

---

## The problem

The portal's Renew button called Cashfree directly. Your `.env` has
`CASHFREE_APP_ID=` empty, so `cashfree.is_configured()` returned False and
every customer who pressed Renew got:

> Online payment is not available right now. Please contact the office.

There was no way for a customer to tell you they had already paid by UPI, and
no way for you to see what customers had submitted.

## The flow now

```
  CUSTOMER                          ADMIN
  ────────                          ─────
  /customer/renew
    pick plan (keep or change)
    pick 1 / 3 / 6 / 12 cycles
    see price + new expiry
        │
        ▼
  RenewalRequest (pending)  ───────────────►  /admin/portal-activity
  + Invoice raised                             shows the request
        │
        ├──► Pay online (Cashfree, when configured)
        │
        └──► /customer/payments/new
             amount + mode + UTR + screenshot
                 │
                 ▼
             Payment (status=pending, source=portal)
             ┌────────────────────────────────┐
             │ CREDITS NOTHING.               │
             │ Invoice balance unchanged.     │
             │ Plan does not move.            │
             └────────────────────────────────┘
                 │
                 ▼                              admin checks the UTR against
                                                the bank statement, sees the
                                                screenshot, then:
                                                  ✔ Verify  →  credited,
                                                     invoice paid, plan
                                                     extended/switched,
                                                     customer un-suspended
                                                  ✘ Reject  →  reason sent
                                                     to the customer, invoice
                                                     stays open
```

The customer sees the status the whole way through: **Pending verification →
Approved**, or **Rejected** with your reason.

## Rules the code enforces

* **A customer can never move their own expiry date.** `create_request()` only
  records intent and raises an invoice. Only `approve()` — reachable solely
  from admin routes — touches `CustomerPlan.end_date`.
* **Renewing early wastes nothing.** The extension is measured from the current
  expiry, not from today. Renewing *after* expiry restarts from today, so you
  do not bill for dead time. That is `renewals.extension_base()`.
* **Approving twice is a no-op.** Idempotent, so a double-click cannot give
  away 90 free days.
* **A UTR can only be used once.** A reference already on a non-rejected
  payment is refused at entry time.
* **A customer cannot overpay a bill.** The amount is capped at the balance.
* **Only one renewal in flight at a time.** Pay it or cancel it first.
* **Approving from the old authorisation queue does the same thing.**
  `/payments/<id>/approve` and the new screen both call
  `services/payments.py`, so a renewal is applied either way.

## New files

| File | What it does |
|---|---|
| `services/renewals.py` | The only code that changes a plan. Pricing, duration, the extension-base rule, approve/reject/cancel. |
| `services/payments.py` | Approve/reject a payment in one place — settles the invoice, applies the renewal, un-suspends, notifies. |
| `services/schema_sync.py` | Adds missing tables/columns on boot. Shared with `migrate.py`. |
| `blueprints/portal_admin_bp.py` | The admin queue, UTR search, activity log. |
| `templates/customer/renew.html` | Plan + duration picker with live pricing. |
| `templates/customer/renew_history.html` | Renewal and plan history. |
| `templates/customer/payment_new.html` | Payment entry with UTR + screenshot. |
| `templates/admin/portal_activity.html` | The review queue. |
| `templates/admin/utr_search.html` | Reference lookup. |
| `templates/admin/activity_log.html` | Activity + login history. |

## New routes

**Customer**

```
GET   /customer/renew                    pick a plan and duration
POST  /customer/renew/confirm            raise the request + invoice
GET   /customer/renew/history            renewal + plan history
POST  /customer/renewals/<id>/cancel     change of mind
GET   /customer/payments/new             payment entry form
POST  /customer/payments/new             submit UTR + screenshot
```

**Admin**

```
GET   /admin/portal-activity                          the queue
POST  /admin/portal-activity/payments/<id>/approve    verify a UTR
POST  /admin/portal-activity/payments/<id>/reject     with a reason
POST  /admin/portal-activity/renewals/<id>/approve    extend/switch the plan
POST  /admin/portal-activity/renewals/<id>/reject     cancel the invoice
GET   /admin/utr-search?q=                            find any reference
GET   /admin/activity-log                             who did what, sign-ins
```

## Schema changes

New table `renewal_requests`, and five new columns on `payments`:
`utr`, `proof_file`, `rejection_reason`, `rejected_at`, `rejected_by_user_id`.

**You do not need to run a migration by hand.** `init_database()` now calls
`services/schema_sync.py`, which adds any missing table or column on boot —
verified against a database created before these columns existed. It only ever
adds; it never drops or rewrites. To see it explicitly:

```bash
python migrate.py --dry-run     # show what would change
python migrate.py               # apply
```

## Setting your UPI ID

The payment-entry screen shows your UPI ID with a copy button when a setting
named `upi_id` exists. Add it under Settings, or:

```python
from models_ext import Setting
Setting.set('upi_id', 'yourbusiness@okhdfcbank')
```

Without it the screen still works — customers just pay however they already do.

## Four new message templates

Seeded on boot, editable under Masters → Customer Templates:
`payment_submitted`, `payment_approved`, `payment_rejected`, `renewal_approved`.

## Verifying it

```bash
python qa_smoketest.py
```

Seeds a throwaway copy of the database, crawls every admin page, portal page
and API endpoint, runs the nightly jobs, then walks the full renewal flow:
raise a request → confirm the plan did **not** move → submit a payment entry →
confirm nothing was credited → find it in the admin queue → find it by UTR →
approve → confirm the expiry moved. `instance/app.db` is never touched.
