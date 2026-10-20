#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11 or newer is required; install Python 3 and try again." >&2
    exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "Python 3.11 or newer is required; install Python 3 and try again." >&2
    exit 1
fi

cd -- "$SCRIPT_DIR" || exit 1
exec python3 -m hwmonitor_mqtt.platforms.install --project-root "$SCRIPT_DIR" "$@"
