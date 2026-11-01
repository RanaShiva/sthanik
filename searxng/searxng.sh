#!/usr/bin/env bash
# A local SearXNG (podman) for Sthanik, on http://127.0.0.1:8888 only.
#   ./searxng.sh start     create config if needed and start (or restart) it
#   ./searxng.sh stop      stop and remove it
#   ./searxng.sh status    is it up, and which engines are suspended right now
#   ./searxng.sh logs      show the container's log
cd "$(dirname "$(readlink -f "$0")")" || exit 1
NAME=sthanik-searxng
PORT="${SEARXNG_PORT:-8888}"
IMAGE=docker.io/searxng/searxng:latest

case "${1:-status}" in
  start)
    mkdir -p config
    if [ ! -f config/settings.yml ]; then
      secret=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
      sed "s/__SECRET__/$secret/" settings.template.yml > config/settings.yml
    fi
    chmod 700 config; chmod 600 config/settings.yml
    # the image runs as an unprivileged user (uid 977); map your user onto it so it can read the owner-only config folder
    podman run -d --replace --name "$NAME" --userns "keep-id:uid=977,gid=977" -p "127.0.0.1:$PORT:8080" -v "$PWD/config:/etc/searxng:Z" \
      --cap-drop ALL --security-opt no-new-privileges --memory 768m \
      -e SEARXNG_BASE_URL="http://127.0.0.1:$PORT/" "$IMAGE" >/dev/null || exit 1
    for _ in $(seq 1 40); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/healthz" && { echo "up on http://127.0.0.1:$PORT"; exit 0; }; sleep 0.5; done
    echo "did not start; see: ./searxng.sh logs"; exit 1 ;;
  stop) podman rm -f "$NAME" >/dev/null 2>&1 && echo stopped || echo "not running" ;;
  logs) podman logs --tail 80 "$NAME" ;;
  status)
    curl -sf -o /dev/null "http://127.0.0.1:$PORT/healthz" && echo "up on http://127.0.0.1:$PORT" || { echo "not running"; exit 1; }
    curl -s "http://127.0.0.1:$PORT/search?q=test&format=json" | python3 -c "import json,sys; d=json.load(sys.stdin); print('unresponsive right now:', d.get('unresponsive_engines') or 'none')" ;;
  *) sed -n 2,6p "$0" ;;
esac
