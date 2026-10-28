#!/usr/bin/env bash
# Put the level desk on an Ubuntu or Debian box. Safe to run twice. Lives next to other services:
# it adds its own Caddy site file and never rewrites a Caddyfile that is already there.
#
#   from a copy of the project on the box:   sudo DOMAIN=api.example.com bash /path/to/level/deploy/install.sh
#   or from GitHub:   REPO=https://github.com/<owner>/level.git  curl -fsSL .../deploy/install.sh | sudo bash
#
# Nothing here asks for a key. The Grok key is set afterwards with deploy/key.sh and lives in /etc/level.env, root only.
set -euo pipefail
REPO=${REPO:-}
DOMAIN=${DOMAIN:-api.levelbot.live}
ROOT=/opt/level
U=level
PORT=8793
SRC=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd || true)
IP=$(curl -4 -s --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')
HOST=level.$(echo "$IP" | tr . -).sslip.io      # its own sslip name, so it can share a box (and a Caddy) with another agent
say(){ printf '\n\033[1m%s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run this with sudo"; exit 1; }

say "1/6  packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || echo "apt-get update reported errors (a repository is down); continuing with the package lists we have"
apt-get install -y -qq python3 python3-venv python3-pip git rsync ufw curl debian-keyring debian-archive-keyring apt-transport-https >/dev/null \
  || echo "apt-get install reported errors; continuing, checking the essentials below"
for b in python3 rsync git curl; do command -v $b >/dev/null || { echo "missing: $b. fix apt and run again"; exit 1; }; done
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi

say "2/6  user and directories"
id -u $U >/dev/null 2>&1 || useradd --system --home $ROOT --shell /usr/sbin/nologin $U
mkdir -p $ROOT/data
if [ -n "$SRC" ] && [ -f "$SRC/agent/main.py" ]; then
  mkdir -p $ROOT/app
  rsync -a --delete --exclude .git --exclude __pycache__ --exclude .pytest_cache --exclude _to_delete --exclude data "$SRC/" $ROOT/app/
elif [ -d $ROOT/app/.git ]; then git -C $ROOT/app pull --ff-only
elif [ -n "$REPO" ]; then git clone --depth 1 "$REPO" $ROOT/app
else echo "no source: run this from a copy of the project or set REPO="; exit 1; fi
# the static site zipped for a Netlify drop (index.html at the root), served at /site.zip
if [ -d $ROOT/app/site ]; then
  python3 - <<PY
import shutil; shutil.make_archive("$ROOT/data/site", "zip", "$ROOT/app/site")
PY
  chmod 644 $ROOT/data/site.zip
fi
# the source as a bare git repo over dumb http at /level.git, for GitHub's importer. never the data dir or the config.
SNAP=$(mktemp -d)
rsync -a --exclude .git --exclude __pycache__ --exclude .pytest_cache --exclude _to_delete --exclude data --exclude config.toml "$ROOT/app/" "$SNAP/"
chown -R root:root "$SNAP"
export GIT_CONFIG_PARAMETERS="'safe.directory=*'"
( cd "$SNAP" && git init -q && git add -A && git -c user.name="level" -c user.email="level@users.noreply.github.com" \
  commit -q -m "level: a Grok teammate that reads a pump.fun launch before you do" \
  -m "Chain facts, the market, and Grok reading X: one card in about ten seconds. Read-only, no key in the repo, the site shows the wall of reads." \
  && git branch -M main )
rm -rf "$ROOT/data/level.git"
git clone -q --bare "$SNAP" "$ROOT/data/level.git"
( cd "$ROOT/data/level.git" && git update-server-info )
chmod -R a+rX "$ROOT/data/level.git"
rm -rf "$SNAP"
unset GIT_CONFIG_PARAMETERS

say "3/6  python environment"
[ -d $ROOT/venv ] || python3 -m venv $ROOT/venv
$ROOT/venv/bin/pip install -q --upgrade pip
$ROOT/venv/bin/pip install -q -r $ROOT/app/requirements.txt

say "4/6  config"
[ -f $ROOT/config.toml ] || cp $ROOT/app/config.example.toml $ROOT/config.toml
sed -i "s|^data_dir = .*|data_dir = \"$ROOT/data\"|; s|^port = .*|port = $PORT|" $ROOT/config.toml
# the project links (site, github, x) follow the example on every install; the mint is set on the box
grep -q '^\[links\]' $ROOT/config.toml || printf '\n[links]\ntoken_mint = ""\ntoken_symbol = "LEVEL"\nx = ""\ngithub = ""\nsite = ""\n' >> $ROOT/config.toml
for k in github site token_symbol x; do
  v=$(grep "^$k = " $ROOT/app/config.example.toml | head -1 | sed 's/ *#.*//' | cut -d= -f2- | sed 's/^ *//')
  [ -n "$v" ] && sed -i "s|^$k = .*|$k = $v|" $ROOT/config.toml
done
[ -f /etc/level.env ] || { echo "PYTHONUNBUFFERED=1" > /etc/level.env; chmod 644 /etc/level.env; }
chown -R $U:$U $ROOT

say "5/6  services"
cp $ROOT/app/deploy/level-agent.service /etc/systemd/system/
mkdir -p /etc/caddy/sites
sed "s/__HOST__/$HOST/; s/__DOMAIN__/$DOMAIN/" $ROOT/app/deploy/level.caddy > /etc/caddy/sites/level.caddy
if [ ! -f /etc/caddy/Caddyfile ]; then
  printf 'import /etc/caddy/sites/*.caddy\n' > /etc/caddy/Caddyfile
elif ! grep -q 'import /etc/caddy/sites/\*.caddy' /etc/caddy/Caddyfile; then
  printf '\nimport /etc/caddy/sites/*.caddy\n' >> /etc/caddy/Caddyfile
fi
caddy validate --config /etc/caddy/Caddyfile >/dev/null
systemctl daemon-reload
systemctl enable level-agent >/dev/null
systemctl enable --now caddy >/dev/null || true
systemctl reload caddy || systemctl restart caddy || true
systemctl restart level-agent

say "6/6  firewall"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 80,443/tcp >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true

sleep 3
cat <<TXT

done. what runs now:
  level-agent   on the launch tape, reading what crosses the line, writing $ROOT/data/*.json, http on 127.0.0.1:$PORT
  caddy          https://$HOST ${DOMAIN:+  and  https://$DOMAIN}

key:     sudo bash $ROOT/app/deploy/key.sh            (the xAI key, typed blind, once)
doctor:  sudo -u $U $ROOT/venv/bin/python -m agent -c $ROOT/config.toml doctor
watch:   journalctl -u level-agent -f
read:    sudo -u $U $ROOT/venv/bin/python -m agent -c $ROOT/config.toml read <mint>
TXT
systemctl --no-pager --lines=5 status level-agent || true
