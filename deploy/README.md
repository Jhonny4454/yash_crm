# Deploying the CRM to your own VPS

Moves the app off Render and the database off Railway, onto one server in
Mumbai, reached through a Cloudflare Tunnel.

**No nginx.** `blueprints/spa_bp.py` already serves the Vite SPA at `/app` with
its own gzip cache and immutable asset headers, Flask serves `/static`, and
Cloudflare does TLS, compression and edge caching. A reverse proxy in between
would have nothing to do except maintain a second routing table that can
disagree with Flask's — which is exactly the bug an earlier draft of this had:
it listed Flask's prefixes explicitly and missed 14 of the 21 in `app.py`, so
`/masters`, `/inventory`, `/hr`, `/dashboard` and `/login` would have returned
a blank SPA page instead of the real screen. cloudflared points straight at
gunicorn. `deploy/nginx-yash-crm.conf` is kept, corrected, for the case where
you want nginx for some other reason.

**One correction to something I said earlier.** I estimated ₹500–900/month for
the VPS. That was low. Realistic pricing for a box that comfortably runs Flask
*and* MySQL together:

| Spec | Roughly | Verdict |
|---|---|---|
| 1 vCPU / 2 GB | ₹800–1,100/mo | Works, but tight once MySQL warms up |
| **2 vCPU / 4 GB / 80 GB SSD** | **₹1,600–2,200/mo** | What I'd actually run |

Providers with an India region: DigitalOcean (Bangalore), Akamai/Linode
(Mumbai), AWS Lightsail (Mumbai), E2E Networks (Indian, often cheapest).
Pick Mumbai or Bangalore — your customers and your OLTs are in Navi Mumbai and
latency to the database is most of what your request time is made of today.

---

## 1. Provision

Ubuntu 24.04 LTS. It ships Python 3.12, which is what `runtime.txt` asks for —
on 22.04 you would be adding a PPA on day one.

```bash
ssh root@YOUR_SERVER_IP
apt update && apt -y upgrade && reboot
```

## 2. Get the code on the server

```bash
mkdir -p /opt/yash-crm
# from your Windows machine, or git clone if the repo is hosted
rsync -avz --exclude .venv --exclude node_modules --exclude __pycache__ \
      "D:/Yash Internet Services/" root@YOUR_SERVER_IP:/opt/yash-crm/
```

## 3. Provision the server

```bash
cd /opt/yash-crm
sudo bash deploy/setup.sh
```

Installs Python, MySQL, nginx, Node 20, cloudflared and a `yashcrm` service
account; sets the clock to Asia/Kolkata; locks the firewall to SSH only.

**It prints a generated MySQL password once and never again.** Copy it straight
into `.env` in the next step.

## 4. Configure

```bash
cp deploy/env.production.template /opt/yash-crm/.env
nano /opt/yash-crm/.env      # fill every REPLACE_ME
chmod 600 /opt/yash-crm/.env
```

Generate the three secrets with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

`CREDENTIAL_KEY` is the one to be careful with — it encrypts your `ISPCredential`
rows. Copy the existing value from your current environment rather than
generating a new one, or those records become unreadable.

## 5. Move the data off Railway

Do this before cutover, while both databases still exist.

```bash
# On the server. Credentials are in migrate_locations.py lines 4-7.
mysqldump --host=RAILWAY_HOST --port=RAILWAY_PORT \
          --user=RAILWAY_USER --password \
          --single-transaction --quick --routines --triggers \
          --default-character-set=utf8mb4 \
          railway > /root/railway-dump.sql

mysql yashcrm < /root/railway-dump.sql

# Sanity check before trusting it
mysql yashcrm -e "SELECT
  (SELECT COUNT(*) FROM customers)     AS customers,
  (SELECT COUNT(*) FROM invoices)      AS invoices,
  (SELECT COUNT(*) FROM payments)      AS payments,
  (SELECT COUNT(*) FROM message_logs)  AS messages;"
```

Compare those four numbers against the same query on Railway. If they differ,
stop and work out why before going further.

## 6. Deploy

```bash
sudo bash deploy/deploy.sh
```

Installs dependencies, builds the Vite frontend, runs `upgrade_schema.py` and
`migrate_message_status.py`, installs the systemd unit, starts the service and
health-checks `/api/v1/health`.

## 7. Cloudflare Tunnel

```bash
cloudflared tunnel login                    # opens a URL; authorise your domain
cloudflared tunnel create yash-crm          # note the tunnel ID it prints
cloudflared tunnel route dns yash-crm crm.yashinternetservices.com

install -m 644 deploy/cloudflared-config.yml /etc/cloudflared/config.yml
nano /etc/cloudflared/config.yml            # paste the tunnel ID in both places
                                            # and set your real hostname

cloudflared service install
systemctl enable --now cloudflared
systemctl status cloudflared
```

In the Cloudflare dashboard set **SSL/TLS → Overview → Full (strict)**. Not
Flexible — Flexible means Cloudflare talks to your origin unencrypted, and with
a tunnel it is meaningless anyway.

No port forwarding, no inbound firewall rules, nothing exposed to scanners.
The tunnel dials out.

## 8. Verify before you cut over

```bash
systemctl status yash-crm cloudflared mysql
curl -I https://crm.yashinternetservices.com/api/v1/health
journalctl -u yash-crm -n 50
```

Then check, in order:

1. **Log in.** Admin account, then a staff account.
2. **One customer page** loads with correct plan and balance.
2b. **The SPA at `/app`** loads — that path is served by Flask, not a web
   server, so it is worth checking separately from the Jinja screens.
3. **Send a WhatsApp test** from Settings to your own phone.
4. **Watch the message log.** Within ~3 minutes the row should leave `queued` —
   that is the new reconcile job proving itself.
5. **Invoice PDF.** Open one over the public URL. If Meta cannot fetch it,
   `invoice_attachment` sends nothing, and `PUBLIC_BASE_URL` is usually why.
6. **Scheduler.** `pgrep -c -f 'gunicorn.*wsgi'` must print **2** — one master,
   one worker. Three or more means `WEB_CONCURRENCY` got raised and every
   nightly job is about to fire twice.

## 9. Backups

```bash
echo '0 3 * * * root /opt/yash-crm/deploy/backup-mysql.sh' \
  > /etc/cron.d/yash-crm-backup
```

Nightly dump, 14 days kept, verified readable after writing. That covers the
box failing, not the box being stolen or the disk dying — set `STORAGE_BACKEND`
to S3/R2/B2 to get copies off the machine. `boto3` is already installed.

## 10. Cutover and rollback

Keep Render running until the new server has served real traffic for a day.
DNS moves you over; DNS moves you back. Do not delete the Railway database for
a fortnight — a backup you have never restored is a hope, not a backup.

Rollback is: point the Cloudflare record back at Render, and `systemctl stop
yash-crm cloudflared`.

---

## What changes about how you operate this

| | Render + Railway | Your VPS |
|---|---|---|
| Deploys | git push, automatic | `sudo bash deploy/deploy.sh` |
| Logs | web dashboard | `journalctl -u yash-crm -f` |
| DB backups | Railway's problem | yours — cron above |
| OS updates | Render's problem | yours — `unattended-upgrades` |
| Uptime | their SLA | your VPS provider's |
| Cost | ~$7 + Railway usage | one fixed monthly bill |
| Latency | app and DB in different datacentres | same box, dramatically faster |

That last row is the real win here. Your `render.yaml` notes that requests
spend "most of their life waiting on Railway". Moving MySQL onto the same
machine removes that wait from every single query.
