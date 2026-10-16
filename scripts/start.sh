#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/host-services.sh
.venv/bin/python scripts/compose_start.py
echo 'Dashboard: http://127.0.0.1:3050 | aurelSpa: http://127.0.0.1:8085'
