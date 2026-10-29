#!/bin/sh
# Starts as root only to hand a fresh volume (Fly.io mounts volumes owned by root) to appuser,
# then runs the server as appuser. A folder that already has an owner is left alone.
set -e

data_dir=$(dirname "${TOKEN_STORE_PATH:-/app/data/tokens.sqlite3}")
mkdir -p "$data_dir"
if [ "$(stat -c %u "$data_dir")" = "0" ]; then
    chown appuser:appuser "$data_dir"
fi

exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
