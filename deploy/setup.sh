#!/usr/bin/env bash
#
# setup.sh — one-time provisioning for the Yash Internet Services CRM.
# Target: Ubuntu 24.04 LTS (ships Python 3.12, which runtime.txt asks for).
#
#   sudo bash setup.sh
#
# Installs system packages, MySQL, an unprivileged service account, the Python
# virtualenv, nginx and cloudflared. It does NOT start the app -- run deploy.sh
# for that, once .env is filled in.
#
set -euo pipefail

APP_USER=yashcrm
APP_DIR=/opt/yash-crm
DB_NAME=yashcrm
DB_USER=yashcrm

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

log "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3.12 python3.12-venv python3-pip \
    mysql-server \
    nginx \
    git curl ca-certificates \
    build-essential pkg-config default-libmysqlclient-dev \
    ufw fail2ban

log "Node 20 (for the Vite frontend build)"
if ! command -v node >/dev/null || [[ $(node -v | cut -c2-3) -lt 20 ]]; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
fi

log "Timezone -> Asia/Kolkata"
# Every "today" in this app is a business day in Navi Mumbai. The scheduler's
# cron times, the dashboard's date arithmetic and the logs all have to agree.
timedatectl set-timezone Asia/Kolkata

log "Service account: $APP_USER"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home \
    --home-dir /home/"$APP_USER" --shell /usr/sbin/nologin "$APP_USER"

install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"/instance
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"/backups
install -d -o "$APP_USER" -g "$APP_USER" /var/log/yash-crm

log "MySQL database and user"
DB_PASS=$(openssl rand -base64 27 | tr -d '/+=' | head -c 32)
mysql <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

# MySQL binds to localhost only by default on Ubuntu; make it explicit so a
# later config change cannot quietly expose it.
cat > /etc/mysql/mysql.conf.d/99-yash.cnf <<'MYCNF'
[mysqld]
bind-address = 127.0.0.1
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
max_connections = 100
MYCNF
systemctl restart mysql

log "Python virtualenv"
sudo -u "$APP_USER" python3.12 -m venv "$APP_DIR"/.venv
sudo -u "$APP_USER" "$APP_DIR"/.venv/bin/pip install --quiet --upgrade pip wheel

log "cloudflared (Cloudflare Tunnel)"
if ! command -v cloudflared >/dev/null; then
    mkdir -p /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
        -o /usr/share/keyrings/cloudflare-main.gpg
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
        > /etc/apt/sources.list.d/cloudflared.list
    apt-get update -qq && apt-get install -y -qq cloudflared
fi

log "Firewall"
# With a Cloudflare Tunnel nothing needs to be reachable from the internet --
# cloudflared dials OUT. Only SSH stays open, and even that is worth moving
# behind the tunnel later.
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

log "Done"
cat <<SUMMARY

  Database : ${DB_NAME}
  DB user  : ${DB_USER}
  DB pass  : ${DB_PASS}

  ^ Copy that password into DATABASE_URL in ${APP_DIR}/.env now.
    It is not stored anywhere else and this is the only time it is printed.

  DATABASE_URL=mysql+pymysql://${DB_USER}:${DB_PASS}@127.0.0.1/${DB_NAME}?charset=utf8mb4

  Next:
    1. Put the code in ${APP_DIR}  (git clone, or rsync from your machine)
    2. cp deploy/env.production.template ${APP_DIR}/.env  and fill it in
    3. bash deploy/deploy.sh
    4. Set up the tunnel  (see deploy/README.md)

SUMMARY
