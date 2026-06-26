#!/bin/sh
# Drop to the configured PUID/PGID (Unraid default 99:100) so organized files
# get the right ownership, apply UMASK, then exec the web server.
set -e

PUID="${PUID:-99}"
PGID="${PGID:-100}"
UMASK="${UMASK:-022}"

umask "$UMASK"

mkdir -p /config /data
# Only the state dir must be writable by the app user; media perms are the host's.
chown -R "$PUID:$PGID" /config 2>/dev/null || true

echo "Quackaloger: starting as ${PUID}:${PGID} (umask ${UMASK}) on port ${QUACK_WEB_PORT:-8080}"

# gosu accepts a numeric uid:gid even with no matching passwd entry.
exec gosu "${PUID}:${PGID}" "$@"
