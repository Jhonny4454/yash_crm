# YASH Internet Services — ISP CRM

Flask-based CRM and billing system for an ISP: customers, plans, invoicing,
payments, inventory with vendor purchase bills, expenses, HR/payroll, and
integrations with Log2Space / Synnefo style provisioning APIs.

---

## Quick start (local)

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then edit .env
python migrate.py                  # create / upgrade the database
python app.py
```

Open <http://localhost:5000>.
Default login: **admin / admin123** — change it immediately under *My Profile*.

---

## Deploying to a server

```bash
pip install -r requirements.txt
cp .env.example .env               # fill in SECRET_KEY, DATABASE_URL, CREDENTIAL_KEY
python migrate.py
gunicorn -c gunicorn.conf.py wsgi:application
```

A `Procfile` is included for platforms that use one.

### Required environment variables

| Variable | Needed | Purpose |
|---|---|---|
| `SECRET_KEY` | **Yes in production** | Signs sessions. The app refuses to start in production without it. |
| `FLASK_ENV` | Recommended | Set to `production` to force secure cookies and disable debug. |
| `DATABASE_URL` | No | Blank uses SQLite. For MySQL use `mysql+pymysql://user:pass@host/db?charset=utf8mb4`. |
| `CREDENTIAL_KEY` | Only for ISP integrations | Fernet key encrypting stored ISP API secrets. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | No | Online payments. Blank disables them cleanly. |

Generate the two keys:

```bash
python -c "import secrets; print(secrets.token_hex(32))"                        # SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # CREDENTIAL_KEY
```

> **Note:** use `mysql+pymysql://`, not bare `mysql://`. SQLAlchemy 2.x needs the
> explicit driver.

### Run behind a reverse proxy

The app already applies `ProxyFix`. Keep `workers = 1` in `gunicorn.conf.py`
unless you move the APScheduler jobs out of the web process — extra workers
would run the auto-invoice and auto-suspend jobs more than once.

---

## Billing vendor products (the Addon Invoice flow)

1. **Inventory → Vendors** — add the vendor.
2. **Inventory → Products** — add each product and **assign it to that vendor**,
   with a *purchase price* (what you pay the vendor) and a *selling price*
   (what the customer is charged).
3. **Inventory → Stock** — set opening quantities.
4. Open a customer → **Pending Invoice** tab → *Addon Invoice*:
   - pick the **Vendor**,
   - press **Add Product** for each device, set serial / qty / rate,
   - optionally add a flat Amount and a Discount, and choose a payment Mode.
5. Press **Submit**. In one transaction the app will:
   - create the invoice with a line item per device,
   - decrement stock,
   - assign each device to the customer,
   - **raise a purchase bill per vendor** (Inventory → Vendor Bills),
   - record the payment if a mode was chosen.

If anything fails — for example billing more units than you hold — nothing is
saved and no stock moves.

Vendor bills can also be raised by hand (**Inventory → Vendor Bills → New
Bill**), which *increases* stock, and paid off in full or in part.

---

## Invoices

Two printable formats, both matching the company's bordered layout, and both
print/PDF-ready with no internet connection:

- **Summary Invoice** — `/invoices/<id>/summary`
- **Detailed Invoice** — `/invoices/<id>/detailed` (line items, tax split,
  payment history, linked vendor bills)

Add `?download=1` to save instead of view.

Company details, GSTIN, state code and logo come from **Masters → Company**.
Terms and conditions come from *Invoice Notes* on that screen; leave it blank
to use the standard eight terms.

For real PDF files instead of print-to-PDF, install one of:

```bash
pip install weasyprint     # best output, needs libpango/libcairo
pip install xhtml2pdf      # pure python fallback
```

---

## ISP provisioning

**Masters → Settings → ISP Integrations** stores credentials per provider.
Adapters for **Log2Space** and **Synnefo** ship in
`services/isp_providers.py`. To add another provider (for example JPR),
subclass `BaseAdapter` and register it:

```python
@register('jpr')
class JPRAdapter(BaseAdapter):
    def enable(self, customer): ...
    def disable(self, customer): ...
```

API secrets are encrypted at rest, so `CREDENTIAL_KEY` must be set before you
can save them.

---

## Project layout

```
app.py                  routes and application setup
models.py               core tables
models_ext.py           settings, invoice items, referrals, ISP, backups
forms.py                WTForms definitions
config.py               configuration from environment
migrate.py              schema upgrade (compares DB against the models)
wsgi.py                 production entry point
blueprints/settings_bp.py   settings, backup, import/export, ISP screens
services/isp_providers.py   provisioning adapters
services/invoicing.py       amount-in-words, PDF rendering
templates/              Jinja templates
static/                 logo and uploads
```

---

## Maintenance

- **Backups** — Masters → Settings → Database Backup.
- **Import / Export** — Masters → Settings → Import/Export (CSV, with
  downloadable templates).
- **Upgrading** — after pulling new code, always run `python migrate.py`.
  Use `python migrate.py --dry-run` first to see what would change.
