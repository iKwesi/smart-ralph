#!/usr/bin/env bash
# Fake ralph: writes three stdout lines and exits cleanly.
# Arg 1 is the PRD number, mirrored into output so the test can check wiring.
set -euo pipefail
prd="${1:-?}"
echo "iteration: start prd=${prd}"
echo "iteration: step 1"
echo "iteration: done prd=${prd}"
