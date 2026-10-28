#!/usr/bin/env bash
# Give the desk its Grok key, or a better RPC url, without the value ever appearing in a chat, a shell
# history or a file in the repo. Run on the box:
#   sudo bash /opt/level/app/deploy/key.sh          # asks for the xAI API key (typed blind), writes /etc/level.env
#   sudo bash /opt/level/app/deploy/key.sh rpc      # asks for an RPC url (e.g. a free Helius key url), writes config.toml
set -e
[ "$(id -u)" -eq 0 ] || { echo "run this with sudo"; exit 1; }
ENV=/etc/level.env
CFG=/opt/level/config.toml
if [ "${1:-}" = "rpc" ]; then
  read -r -p "rpc url (https://mainnet.helius-rpc.com/?api-key=...): " URL
  [[ "$URL" =~ ^https:// ]] || { echo "that is not an https url"; exit 1; }
  sed -i "s|^rpc_url = .*|rpc_url = \"$URL\"|" "$CFG"
  echo "rpc_url set"
else
  read -r -s -p "xAI API key (nothing is echoed): " KEY; echo
  [ ${#KEY} -ge 20 ] || { echo "that is too short to be a key"; exit 1; }
  touch "$ENV"; chmod 600 "$ENV"; chown root:root "$ENV"
  grep -v '^XAI_API_KEY=' "$ENV" > "$ENV.tmp" || true
  printf 'XAI_API_KEY=%s\n' "$KEY" >> "$ENV.tmp"
  grep -q '^PYTHONUNBUFFERED=' "$ENV.tmp" || echo 'PYTHONUNBUFFERED=1' >> "$ENV.tmp"
  mv "$ENV.tmp" "$ENV"; chmod 600 "$ENV"
  echo "key stored in $ENV (root only)"
fi
systemctl restart level-agent
sleep 3
sudo -u level XAI_API_KEY="$(grep '^XAI_API_KEY=' $ENV | cut -d= -f2-)" /opt/level/venv/bin/python -m agent -c $CFG doctor || true
