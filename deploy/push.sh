#!/usr/bin/env bash
# Copy this project to the VPS and run the installer there, in one ssh session. Run on the Mac:
#   bash ~/Documents/Claude/Projects/post/level/deploy/push.sh
#   the api host defaults to api.levelwatch.live (set DOMAIN= to override)
# Asks for the root password once. Nothing is stored anywhere.
set -e
BOX=${BOX:-root@162.35.122.197}
SRC="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$(basename "$SRC")"
echo "sending $SRC to $BOX and installing (2-3 min)..."
tar czf - -C "$(dirname "$SRC")" \
  --exclude='*/__pycache__' --exclude="$NAME/_to_delete" --exclude="$NAME/data" --exclude="$NAME/.git" --exclude="$NAME/.pytest_cache" \
  "$NAME" \
| ssh -o StrictHostKeyChecking=accept-new "$BOX" "rm -rf /root/level-src && mkdir -p /root/level-src && tar xzf - -C /root/level-src && DOMAIN='${DOMAIN:-}' bash /root/level-src/$NAME/deploy/install.sh"
