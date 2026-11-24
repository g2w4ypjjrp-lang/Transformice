#!/usr/bin/env bash
# Transformice private server installer for an Ubuntu 24.04 / Debian 12 VPS.
# Usage (as root, from the repository root):  sudo bash deploy/install.sh
set -euo pipefail

APP=transformice.canarslan
APP_DIR=/opt/$APP
ETC_DIR=/etc/$APP
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GAME_PORTS=(11801 12801 13801 14801)

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)."; exit 1; }

echo "==> 1/7 System packages"
apt-get update -qq
apt-get install -y -qq git curl ca-certificates openssl ufw
if ! apt-get install -y -qq openjdk-25-jdk-headless 2>/dev/null; then
  echo "openjdk-25 not in apt; installing Eclipse Temurin 25"
  install -d /etc/apt/keyrings
  curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public -o /etc/apt/keyrings/adoptium.asc
  echo "deb [signed-by=/etc/apt/keyrings/adoptium.asc] https://packages.adoptium.net/artifactory/deb $(. /etc/os-release; echo "$VERSION_CODENAME") main" > /etc/apt/sources.list.d/adoptium.list
  apt-get update -qq && apt-get install -y -qq temurin-25-jdk
fi
JAVA_HOME="$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")"
export JAVA_HOME
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

echo "==> 2/7 Secrets (generated once, stored only in $ETC_DIR, mode 600)"
install -d -m 750 "$ETC_DIR"
if [ ! -f "$ETC_DIR/server.env" ]; then
  DB_PASS="$(openssl rand -hex 24)"; ROOT_PASS="$(openssl rand -hex 24)"; ADM_PASS="$(openssl rand -hex 16)"
  cat > "$ETC_DIR/mongo.env" <<ENV
MONGO_INITDB_ROOT_USERNAME=root
MONGO_INITDB_ROOT_PASSWORD=$ROOT_PASS
TFM_DB_NAME=tfmdb69
TFM_DB_USERNAME=tfm
TFM_DB_PASSWORD=$DB_PASS
ENV
  cat > "$ETC_DIR/server.env" <<ENV
TFM_DB_HOST=127.0.0.1
TFM_DB_PORT=27017
TFM_DB_NAME=tfmdb69
TFM_DB_USERNAME=tfm
TFM_DB_PASSWORD=$DB_PASS
TFM_ADMIN_USERNAME=admin
TFM_ADMIN_PASSWORD=$ADM_PASS
ENV
  chmod 600 "$ETC_DIR"/*.env
fi

echo "==> 3/7 MongoDB (Docker, localhost only, auth enabled)"
install -d "$APP_DIR/deploy"
cp "$REPO_DIR/deploy/docker-compose.yml" "$REPO_DIR/deploy/mongo-init.sh" "$APP_DIR/deploy/"
chmod 755 "$APP_DIR/deploy/mongo-init.sh"
docker compose -f "$APP_DIR/deploy/docker-compose.yml" -p tfm up -d

echo "==> 4/7 PSFramework dependency (built from source; jitpack not required)"
BUILD_TMP="$(mktemp -d)"
git clone -q --depth 1 --branch 1.0.2 https://github.com/NoFileFound/PSFramework "$BUILD_TMP/PSFramework"
( cd "$BUILD_TMP/PSFramework"
  sed -i 's/JavaLanguageVersion.of(22)/JavaLanguageVersion.of(25)/' build.gradle
  sed -i "s/id 'java-library'/id 'java-library'\n    id 'maven-publish'/" build.gradle
  cat >> build.gradle <<'G'

publishing { publications { maven(MavenPublication) { groupId = 'com.github.NoFileFound'; artifactId = 'PSFramework'; version = '1.0.2'; from components.java } } }
G
  chmod +x gradlew && ./gradlew -q --no-daemon publishToMavenLocal )

echo "==> 5/7 Build server"
( cd "$REPO_DIR" && chmod +x gradlew && ./gradlew -q --no-daemon shadowJar )

echo "==> 6/7 Install to $APP_DIR"
id tfm >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin tfm
cp "$REPO_DIR"/build/libs/Transformice-*.jar "$APP_DIR/Transformice.jar"
rm -rf "$APP_DIR/config" && cp -r "$REPO_DIR/config" "$APP_DIR/config"
install -d -o tfm -g tfm "$APP_DIR/logs"
chown -R tfm:tfm "$APP_DIR/config"
chgrp tfm "$ETC_DIR" "$ETC_DIR/server.env" && chmod 640 "$ETC_DIR/server.env"
cp "$REPO_DIR/deploy/transformice.service" /etc/systemd/system/$APP.service
systemctl daemon-reload
systemctl enable --now $APP.service
rm -rf "$BUILD_TMP"

echo "==> 7/7 Firewall (game ports only; MongoDB stays private)"
ufw allow OpenSSH >/dev/null
for p in "${GAME_PORTS[@]}"; do ufw allow "$p/tcp" >/dev/null; done
ufw --force enable >/dev/null

sleep 8
systemctl --no-pager --lines=15 status $APP.service || true
echo
echo "Done. Logs: journalctl -u $APP -f"
echo "Secrets: $ETC_DIR (root only). Game ports: ${GAME_PORTS[*]}"
