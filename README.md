# YASH Internet Services CRM

A Flask-based CRM for managing ISP customers, billing, plans, and online payments.

## Quick deploy to Render

1. Fork / push this repo to GitHub
2. In [Render](https://render.com), click **New → Blueprint** and point it at this repo
3. Render will read `render.yaml` and create the web service + PostgreSQL database automatically
4. After the first deploy, go to **Environment** in the Render dashboard and set:
   - `CASHFREE_APP_ID` / `CASHFREE_SECRET_KEY` (leave blank to disable online payment)
   - `WA_ENABLED=1` / `WA_API_URL` / `WA_API_TOKEN` (leave blank to disable WhatsApp)
5. Open the app URL and log in with `admin` / `admin123`, then **change the password immediately**

## Local development

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # fill in at least SECRET_KEY
python app.py
```

The app creates the SQLite database and a default `admin` account on first run.

## Environment variables

See `.env.example` for the full list and descriptions.

## Deploying to Railway

1. Push to GitHub
2. New project → Deploy from GitHub repo
3. Add a MySQL or PostgreSQL database service
4. Set `DATABASE_URL` to the connection string from the database service
5. Set the other required environment variables listed in `.env.example`

## Default credentials

| Account | Username | Password |
|---------|----------|---------|
| Admin   | admin    | admin123 |

**Change both immediately after first login.**

## Customer portal

Customers log in at `/customer/login` using their username and the password
set when you created their account (default: `123456`).
