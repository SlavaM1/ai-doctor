#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .runtime/socket
chmod 700 .runtime .runtime/socket
if [[ ! -x .venv/bin/python ]]; then
  echo 'Create a Python 3.12 virtualenv and install requirements.txt first; see README.md.' >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo 'Configure .env first; see .env.example.' >&2
  exit 1
fi
start_service() {
  local name="$1"
  shift
  if [[ -f ".runtime/$name.pid" ]] && kill -0 "$(cat ".runtime/$name.pid")" 2>/dev/null && grep -aq "doctor.$name" "/proc/$(cat ".runtime/$name.pid")/cmdline"; then
    echo "$name already running"
    return
  fi
  nohup setsid "$@" >".runtime/$name.log" 2>&1 &
  echo "$!" >".runtime/$name.pid"
  echo "$name started"
}
start_service runner .venv/bin/uvicorn doctor.runner:app --uds .runtime/socket/runner.sock --no-access-log
start_service collector .venv/bin/python -m doctor.collector
