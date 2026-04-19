#!/usr/bin/env bash
# Fake ralph that runs until SIGTERM.
set -euo pipefail
echo "ralph: starting"
trap 'echo "ralph: caught signal"; exit 143' TERM INT
while true; do
    sleep 0.1
done
