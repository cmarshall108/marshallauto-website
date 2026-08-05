#!/usr/bin/env bash
export PORT=80

pid=
cleanup() {
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
  fi
  echo "Shutting down."
  exit 0
}
trap cleanup SIGINT SIGTERM

while true; do
  python3 run.py &
  pid=$!
  wait "$pid"
  status=$?
  pid=

  # 130 = SIGINT (Ctrl-C), 143 = SIGTERM
  if [[ $status -eq 130 || $status -eq 143 ]]; then
    exit 0
  fi

  echo "run.py exited with status $status. Restarting in 2s..."
  sleep 2
done
