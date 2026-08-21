#!/usr/bin/env bash
#
# deploy.sh — install dependencies, build the frontend, migrate, restart.
# Safe to re-run. Run as root (it drops to the service account for app steps).
#
#   sudo bash deploy/deploy.sh
#
set -euo pipefail

APP_USER=yashcrm
APP_DIR=/opt/yash-crm
VENV="$APP_DIR/.venv"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
run() { sudo -u "$APP_USER" env -C "$APP_DIR" "$@"; }

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
[[ -f "$APP_DIR/.env" ]] || { echo "missing $APP_DIR/.env -- copy the template first"; exit 1; }

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

log "Python dependencies"
run "$VENV/bin/pip" install --quiet --upgrade -r requirements.txt

log "Frontend build"
if [[ -d "$APP_DIR/frontend" ]]; then
    run npm --prefix frontend ci --silent
    run npm --prefix frontend run build --silent
else
    echo "  no frontend/ directory, skipping"
fi

log "Database migrations"
# wsgi.py calls init_database() on boot, which handles create_all(). These add
# columns to tables that already exist, which create_all() will not do.
for m in upgrade_schema.py migrate_message_status.py; do
    if [[ -f "$APP_DIR/$m" ]]; then
        echo "  $m"
        run "$VENV/bin/python" "$m" || echo "  ! $m reported a problem -- check above"
    fi
done

log "Services"
install -m 644 "$APP_DIR/deploy/yash-crm.service" /etc/systemd/system/yash-crm.service
install -m 644 "$APP_DIR/deploy/nginx-yash-crm.conf" /etc/nginx/sites-available/yash-crm
ln -sf /etc/nginx/sites-available/yash-crm /etc/nginx/sites-enabled/yash-crm
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable --now yash-crm
systemctl restart yash-crm
systemctl reload nginx

log "Health check"
sleep 4
for i in $(seq 1 10); do
    if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
        echo "  healthy"
        break
    fi
    [[ $i -eq 10 ]] && { echo "  NOT healthy -- journalctl -u yash-crm -n 50"; exit 1; }
    sleep 3
done

log "Scheduler check"
# One line per job at startup. If you see two of any job, WEB_CONCURRENCY got
# raised and every reminder is about to go out twice.
echo "  worker processes: $(pgrep -c -f 'gunicorn.*wsgi:application' || echo 0) (expect 2 -- 1 master + 1 worker)"

log "Done"
echo "  logs:  journalctl -u yash-crm -f"
