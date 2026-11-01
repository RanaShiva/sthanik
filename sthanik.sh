#!/usr/bin/env bash
# Launcher for the .desktop entry: start Sthanik if it isn't running, then open it in the browser.
#   sthanik.sh          start (if needed) and open
#   sthanik.sh stop     stop the server (this also unloads the model)
cd "$(dirname "$(readlink -f "$0")")" || exit 1
PORT="${LFM3_UI_PORT:-7860}"
URL="http://127.0.0.1:${PORT}"
up() { curl -sf -o /dev/null "$URL/api/status"; }

if [ "$1" = "stop" ]; then
  pid=$(ss -ltnp 2>/dev/null | grep ":${PORT} " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  [ -n "$pid" ] && kill "$pid" && notify-send -i "$PWD/app/static/sthanik.svg" "Sthanik" "Stopped" 2>/dev/null
  exit 0
fi

if ! up; then
  mkdir -p data
  setsid nohup ./run.sh --no-browser >> data/server.log 2>&1 &
  for _ in $(seq 1 60); do up && break; sleep 0.5; done
  up || { notify-send -i dialog-error "Sthanik" "The server did not start. See $PWD/data/server.log" 2>/dev/null; exit 1; }
fi
exec xdg-open "$URL"
