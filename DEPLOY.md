# Deploying to GitHub + Render

## Step 1 — Push to GitHub

Open a terminal on your computer (or on the Render server) and run:

```bash
# Extract the project
tar xzf yash_crm_github_ready.tar.gz
cd yash

# The git repo is already initialised with one commit.
# Now link it to your GitHub repo:
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git

# Push
git branch -M main
git push -u origin main
```

Replace `YOUR_USERNAME` and `YOUR_REPO_NAME` with your actual GitHub details.

---

## Step 2 — Deploy on Render (easiest way)

1. Go to [render.com](https://render.com) and log in
2. Click **New → Blueprint**
3. Connect your GitHub account if you haven't already
4. Select the `yash` repository
5. Render reads `render.yaml` automatically and creates:
   - A **Web Service** (the Flask app)
   - A **PostgreSQL database** (free tier)
6. Click **Apply**

### Environment variables to add in Render dashboard

After the first deploy, go to **Web Service → Environment** and add:

| Variable | Value |
|---|---|
| `SECRET_KEY` | Click "Generate" (Render does this for you via render.yaml) |
| `ADMIN_PASSWORD` | Choose a strong password |
| `CREDENTIAL_KEY` | Click "Generate" |
| `CASHFREE_APP_ID` | From Cashfree dashboard (leave blank to disable) |
| `CASHFREE_SECRET_KEY` | From Cashfree dashboard |
| `CASHFREE_ENV` | `production` |
| `WA_ENABLED` | `1` |
| `WA_API_URL` | Your WhatsApp gateway endpoint |
| `WA_API_TOKEN` | Your WhatsApp API token |
| `WA_INSTANCE_ID` | Your instance ID |

---

## Step 3 — Deploy on Render (manual way, if Blueprint doesn't work)

1. **New → Web Service**
2. Connect to your GitHub repo, branch `main`
3. Settings:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn -c gunicorn.conf.py wsgi:application`
   - **Plan:** Free
4. Add a **PostgreSQL** database (New → PostgreSQL, free plan)
5. In the web service environment, add `DATABASE_URL` and point it at the database's **Internal Connection String**
6. Add the other environment variables from the table above

---

## Step 4 — First login

1. Open your Render URL (e.g. `https://yash-internet-services-crm.onrender.com`)
2. Log in as `admin` with the `ADMIN_PASSWORD` you set
3. **Immediately** go to My Profile and change the password
4. Go to Masters → Settings and fill in your company name, phone number, etc.

---

## Troubleshooting Render deploys

**Build fails with "ModuleNotFoundError"**
→ Check `requirements.txt` is in the root of the repo (not in a subfolder).

**App boots but database errors appear**
→ The `DATABASE_URL` env var is missing or wrong. Copy the **Internal** connection string from your Render database, not the External one.

**"SECRET_KEY is not set" error in production**
→ Add `SECRET_KEY` to the environment variables in Render.

**Scheduler sends duplicate messages**
→ Set `WEB_CONCURRENCY=1`. The APScheduler runs inside the Flask process; multiple workers = multiple schedulers.

**Free tier spins down after 15 minutes of inactivity**
→ The first request after spin-down is slow (30–60 s). This is normal on the free plan. Upgrade to Starter ($7/month) to keep it always-on.
