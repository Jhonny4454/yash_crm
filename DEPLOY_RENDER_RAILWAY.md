# Deploying to Render (app) + Railway (MySQL)

Two Render services and one Railway database. Nothing here needs a paid tier
to try, but the API's free instance sleeps after 15 minutes and takes ~30
seconds to wake — use Starter for anything a customer touches.

---

## 1. Database on Railway

1. railway.app → **New Project** → **Add MySQL**.
2. Open the service → **Variables** → copy `MYSQL_PUBLIC_URL`.
   It looks like `mysql://root:PASS@containers-us-west-x.railway.app:6543/railway`.
3. Use the **public** URL, not the internal one. Render cannot reach Railway's
   private network — the internal hostname resolves to nothing from outside and
   the API will hang on its first query rather than failing clearly.

`config.py` rewrites `mysql://` to `mysql+pymysql://` for you; SQLAlchemy 2
rejects the bare prefix.

---

## 2. API on Render

New → **Blueprint**, point it at this repository. `render.yaml` defines both
services. Fill in the values marked `sync: false`:

| Variable | Value |
|---|---|
| `DATABASE_URL` | the Railway public URL from step 1 |
| `SECRET_KEY` | leave as generated |
| `JWT_SECRET_KEY` | leave as generated |
| `CORS_ORIGINS` | the front end's URL, e.g. `https://yash-crm.onrender.com` |

**`CORS_ORIGINS` is not optional.** The two services are on different hosts, so
without it the browser blocks every API call the UI makes, and it surfaces as
an unexplained network error rather than anything mentioning CORS.

The app refuses to boot in production with the development `SECRET_KEY`. That
is deliberate — with the shipped default, anyone who has read this repository
can forge a session token.

### After the first deploy

In the API service's **Shell** tab, once:

```
python upgrade_schema.py
```

That adds the columns and indexes the current models expect, seeds the settings
rows and the discount reasons. It is safe to re-run.

---

## 3. Front end on Render

The blueprint creates it as a static site. The only variable is:

```
VITE_API_URL = https://yash-crm-api.onrender.com/api/v1
```

Vite bakes this in **at build time**, so changing it needs a redeploy, not a
restart. If the API's URL changes, update this and trigger a rebuild.

The `rewrite /* → /index.html` route matters: React Router owns the paths, and
without it a refresh on `/app/customers` returns a CDN 404.

---

## 4. Uploads

KYC documents and the invoice logo are written to
`static/uploads`. The blueprint mounts a 1 GB disk there. Without it every
redeploy wipes them — Render's filesystem is ephemeral otherwise.

If you outgrow 1 GB, move to S3 or Cloudflare R2 rather than growing the disk;
a mounted disk pins the service to one instance and blocks horizontal scaling.

---

## 5. Checks

```
curl https://yash-crm-api.onrender.com/api/v1/health
```

Then, from the front end's URL, sign in and open the dashboard. If the panels
show "Cannot reach the server", it is almost always `CORS_ORIGINS` not matching
the front end's origin exactly — including `https://` and no trailing slash.

Run `python check_routes.py` locally after any endpoint change; it fails on
duplicate routes and on any endpoint missing its auth decorator.

---

## What is not automated

- **Backups.** Railway snapshots its volume, but a logical dump is a separate
  concern. `Masters → Backup` in the app writes one server-side; on Render that
  lands on the mounted disk.
- **The messaging gateway and Cashfree** are off until their keys are set under
  Settings. Until then the app reports sends as `dry-run` rather than claiming
  a message was delivered.
- **Scheduled jobs.** Nothing runs on a timer. Expiry reminders are sent from
  the Renewal Requests screen by hand. If you want them nightly, that is a
  Render Cron Job calling `POST /api/v1/renewals/send-reminders`.
