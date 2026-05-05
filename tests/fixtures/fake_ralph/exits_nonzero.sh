#!/usr/bin/env bash
# Fake ralph that prints a few stdout lines and exits 1.
# Used to verify the supervisor's anomaly-detection path.
set -euo pipefail
echo "ralph: starting"
echo "ralph: doing work"
echo "ralph: hitting an error"
exit 1
