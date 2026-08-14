# Settings page rebuild + dropdowns across the app

Everything here was driven in a real browser against a running copy of the app
with 600 seeded customers, not just built and eyeballed.

---

## 1. The Settings page

**What it was.** 47 rows in a key/value table, rendered as 47 identical text
boxes with labels generated from the column name. `Tax Type` was a text field
you could type "Exclde" into. `Wa Enabled` was a box you typed `1` or `0` into.
`Coll Amount Change` told you nothing about what it changed. Nothing was
validated, so a typo saved cleanly and only surfaced later when billing or
WhatsApp quietly did the wrong thing. There were no groups, no help text, and
no way to tell a boolean from a template string.

**What it is now.**

- **9 labelled sections** — Numbering & prefixes, Billing & tax, Collection
  counter, WhatsApp gateway, Outgoing email, Online payments, SMS templates,
  Branding & links, Other — each with a sentence saying what it governs.
- **Every setting has a real control:** 8 dropdowns, 9 on/off switches,
  7 number fields with ranges, 3 templates as textareas, 3 masked secrets, the
  rest typed text/url/email.
- **Every setting has an English name and a line of help** — "Plan price to
  bill: Customer — use the price agreed with that customer. Master — always
  use the price on the plan itself."
- Changed rows are highlighted and counted in a sticky save bar; **only what
  changed is sent**. Discard reverts.
- **Validation on both sides, from one list.** The dropdown options and the
  server's rules are generated from the same schema, so the screen cannot offer
  a value the API will refuse.

### New dropdowns on this page

| Setting | Was | Now |
|---|---|---|
| Plan price to bill | free text | Customer's agreed price / Plan master price |
| Tax treatment | free text | Price includes tax / Tax added on top |
| Calculate tax on | free text | Base amount / Total after discount |
| WhatsApp gateway | free text | WabAssist / Meta Cloud API / Generic |
| Default country code | free text | 8 countries with dial codes |
| WhatsApp HTTP method | free text | POST / GET |
| SMTP port | free text | 587 STARTTLS / 465 SSL / 25 / 2525 |
| Cashfree environment | free text | Sandbox (testing) / Production (live money) |

Plus 9 free-text `1`/`0` fields that are now switches: send WhatsApp, send
email, STARTTLS, SSL, allow discounts, require happy code, and the three
collection-counter permissions.

### Three real bugs fixed on the way

1. **Unknown keys were silently discarded.** `PUT /settings` skipped any key it
   did not recognise and still answered `{"status": "saved"}`. Verified before
   the fix: posting an unknown key returned 200 with `count: 0` and the value
   was gone on refresh — saved, then reverted, with no error. It now returns
   400 naming the key.

2. **Live credentials were being sent to the browser.** The WhatsApp API token,
   the Cashfree secret key and the SMTP password were serialised into the page
   in plain text and put in a password box, where any devtools session or page
   save picked them up. They are no longer sent at all: the field shows
   *"Saved — leave blank to keep it"*, and an empty submit leaves the stored
   value alone rather than blanking it.

3. **Two forms on one screen editing the same four keys.** The WhatsApp tester
   at the bottom carries its own copy of gateway, country code and API key,
   seeded once when it loads. Change the gateway at the top, save, then press
   Save in the tester — and the tester wrote its stale values straight back
   over the new ones. The tester now reloads whenever settings are saved.

### Verified in the browser

```
change dropdown + switch + number  ->  "2 unsaved changes", rows highlighted
save                               ->  "2 settings saved."
hard reload                        ->  {tax_type: 'Include', discount: false, due: '21'}
type 900 into a 0-90 field         ->  "Grace period cannot be more than 90." + Save disabled
read the secret field from the DOM ->  "" (placeholder: "Saved — leave blank to keep it")
```

---

## 2. The Attendance dropdown was corrupting its own table

Worth calling out separately, because it is the most damaging thing found in
this round and it is exactly the class of problem you asked about.

The Attendance screen's Status dropdown offered **present, absent, half-day,
leave, holiday**. The database column accepted only the first three.

Choosing "leave" or "holiday":

1. returned a 500 to the operator, but
2. **wrote the row anyway**, and
3. from then on **every load of the Attendance page was a 500**, for everyone,
   until that row was deleted by hand.

SQLite does not enforce an `Enum` at all, so the value went in and then the ORM
refused to read it back. MySQL outside strict mode is no better — it stores an
empty string instead.

Fixed three ways:

- the column now accepts `leave` and `holiday`, because both are real things an
  office marks (the dropdown was right and the column was wrong);
- `upgrade_schema.py` widens the live MySQL column to match — **run it**;
- and every generic-CRUD write now validates enum values before assigning them,
  returning `400 Status must be one of: … (got "vacation")` instead of writing
  a value that poisons later reads. The hand-written staff endpoint got the
  same guard — a staff role of "wizard" used to save and then break every read
  of the users table, **including the login lookup**.

Verified: 7 bad values across attendance, expenses, vendor bills, leaves and
staff all rejected with a 400 naming the field and its legal values, and all
five list endpoints still return 200 afterwards.

---

## 3. Dropdowns everywhere else

| Screen | Field | Was | Now |
|---|---|---|---|
| Staff | Role | free text | admin / support / field / accounts |
| Expenses | Status | free text | draft / pending / approved / rejected |
| Vendor bills | Status | free text | draft / pending / partial / paid / cancelled |
| Attendance | Status | dropdown with 2 illegal values | 5 values the column accepts |
| Plans | Plan type | free text | Prepaid / Postpaid + every type already in use |
| Customers (generic) | Connection | free text | Ethernet / FTTH / Lease Line |

`plan_type` is a plain string column rather than an enum, so that list is built
from the types already in your data plus the two conventional ones — an
existing plan with a custom type keeps it. The others are enums and the lists
match the column exactly.

Payment mode, customer title, customer type, tax type, billing type, zone,
locality, area and building were already dropdowns and were left alone.

**Error messages got readable too.** A rejected value used to put the literal
string `invalid_values` on the screen. The API's own sentence is now shown
instead — "Status must be one of: draft, pending, approved, rejected (got
'Approved')." That change is in the shared error helper, so every screen in the
app benefits.

---

## 4. "View all" on the dashboard lifecycle rows

Each of the three rows — Expired, Customer renewed, Expiring — now carries a
second control beside the week's count.

The pill in front of it is **this week**: the sum of the seven chips, and what
the row is actually showing. **View all** is the whole book. They used to be the
same control, which read as a total and was not one — a customer whose plan
lapsed three weeks ago appeared in neither the chips nor the count, so the row
could truthfully say `(0)` with a hundred dead connections behind it.

On the seeded data the difference is stark:

| Row | This week | View all |
|---|---:|---:|
| Expired | 52 | **334** |
| Customer renewed | 44 | **542** |
| Expiring | 54 | **268** |

Each button opens the Plan expiry board on a matching new range — **All
upcoming**, **Already expired**, **Renewed (all)** — and the board gained
*Renewed (last 30 days)* as well. The report endpoint accepts `days=all` to drop
the far edge of the window, so "expiring" is no longer silently capped at 30
days, which was wrong for anyone on a quarterly or yearly plan.

The count on the button and the number of rows the board opens are computed
from the same filter and were checked against each other: 268/268, 334/334,
542/542.

---

## 5. WhatsApp 131026 — a real cause in the phone normaliser

`normalize_phone()` decided whether a number already carried the country code
by asking *"does it start with 91?"*. For India that is wrong: an ordinary
ten-digit mobile can itself begin 91. Those numbers had no country code added,
so a 10-digit MSISDN went to the gateway and WhatsApp answered **131026 Message
undeliverable** — for that slice of customers only, which is why it read as
"WhatsApp works, except for some people".

It now decides by length. Verified against 12 formats (`+91…`, `0091…`, spaced,
hyphenated, already-prefixed): all correct, and nothing that used to send stops
sending.

To find who this was affecting:

```sql
SELECT id, mobile FROM customers
WHERE mobile LIKE '91%' AND LENGTH(REPLACE(mobile, ' ', '')) = 10;
```

---

## 6. Still to do on your side

1. **Run `python upgrade_schema.py` against the live database.** It now also
   widens `attendance.status`. Until it runs, choosing "leave" or "holiday"
   will still be rejected by MySQL (rejected cleanly now — a 400, not a
   corrupted table).
2. **Rebuild the frontend where you deploy it** (`npm run build` in `frontend/`)
   — the Settings page, the dropdowns and the View all buttons are all
   frontend changes.
3. **Check your WhatsApp template categories in Meta Business Manager.** Bill,
   renewal and expiry templates should be **Utility**, not Marketing; Marketing
   category triggers stricter per-user delivery limits and is the other common
   source of 131026.
