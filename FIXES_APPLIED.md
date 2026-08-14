# Fixes applied — 04 Aug 2026

The project was booted against a seeded database and every screen, write flow,
API endpoint and background job was exercised. Three source files changed.
Nothing else was touched: `instance/app.db` is byte-identical to the one you
sent, and `.env` still points at your Railway MySQL instance.

---

## 1. `app.py` — nightly background jobs were dying every night

**This was the serious one.**

`log_audit()` read `current_user.is_authenticated` directly. Inside a request
that is fine. Outside one — which is exactly where the APScheduler jobs run —
`current_user` resolves to `None`, so the attribute access raised
`AttributeError: 'NoneType' object has no attribute 'is_authenticated'`.
`request.remote_addr` fails the same way.

Two of the four scheduled jobs called it and crashed:

| Job | Runs at | What actually happened |
|---|---|---|
| `auto_suspend_overdue` | 02:00 daily | Crashed on the **first** overdue customer. Worse: the suspension is committed *before* the audit call, so one customer got suspended and every other overdue customer was silently skipped. |
| `send_expiry_reminders` | 09:00 daily | Crashed on the first reminder. **No expiry reminders were ever sent** — not the 3-day, not the 2-day, not the expired notice. |

`log_audit()` now resolves the user and IP defensively, and can no longer roll
back the operation it is recording — an audit-write failure is logged and
swallowed rather than propagated.

Verified after the fix: all four jobs complete, 3 overdue customers suspended
in one pass, expiry reminders delivered, audit rows written with
`user_id=NULL` / `ip=NULL` as befits a system job.

Also added `has_request_context` to the `flask` import line.

## 2. `services/messaging.py` — invoice messages had a blank plan name

`build_context()` only set `{{plan_name}}` when the caller passed a `plan` or
`customer_plan`. Callers that only have an invoice — addon charges, the
gateway callback, "resend bill" — passed neither, so the `bill` template
rendered as:

> Dear Mr. Sharma, your invoice for **·** is ready.

`build_context()` now derives the plan from `invoice.customer_plan`, falls back
to the customer's active plan, and for addon/other invoices uses the invoice
caption instead. Same message now renders:

> Dear Mr. Sharma, your invoice for **Installation** is ready.

This fixes every caller at once rather than patching each call site.

## 3. `blueprints/settings_bp.py` — same audit pattern, hardened

Its private `_audit()` helper had the identical `current_user.is_authenticated`
construct. It is only reachable from request-bound routes so it was not
crashing, but it is now defensive for the same reasons, and an audit failure no
longer rolls back a settings save, backup or CSV import.

---

## What was checked and found healthy

| Check | Result |
|---|---|
| Every `url_for()` target in all 110 templates | 0 missing endpoints |
| Every `render_template()` target in all Python files | 0 missing files |
| Orphan templates (never rendered, never included) | 0 |
| Python syntax, all modules | clean |
| Jinja parse, all 110 templates | clean |
| Admin/staff pages crawled as a logged-in admin | 143 pages, 0 errors |
| Customer portal crawled as a logged-in customer | 29 pages, 0 errors |
| REST API `/api/v1` crawled with staff + customer JWTs | 81 endpoints, 0 errors |
| Write flows (add/edit/renew/pay/approve/export …) | 45 flows, 0 failures |
| Background scheduler jobs | 4 of 4 pass (2 were broken) |

The duplicate `/settings`, `/settings/isp/*` and `/settings/import/*` rules in
the URL map are **not** a bug — `blueprints/settings_bp.register()` registers
each view a second time under a short endpoint alias so older templates can
keep calling `url_for('settings')`. Both endpoints resolve to the same view.

---

## Re-running the checks yourself

```bash
pip install -r requirements.txt
python qa_smoketest.py          # seeds a throwaway DB, crawls, reports
```

`qa_smoketest.py` never touches `instance/app.db` — it works on a copy at
`instance/qa_smoketest.db` and deletes it on the way out.

## Running the app

```bash
python app.py                   # uses DATABASE_URL from .env (Railway MySQL)
```

To run against the bundled SQLite copy instead, without editing `.env`:

```bash
DATABASE_URL="sqlite:///instance/app.db" python app.py
```

Default admin login is `admin`; the password is whatever `ADMIN_PASSWORD` in
`.env` is set to — it is still `change_me_now`, so change it.
