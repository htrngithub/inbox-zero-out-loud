#!/usr/bin/env bash
# One command to run the whole thing: triage, serve, tunnel, call.
# Everything is checked before the phone rings, because a failure mid-call
# is a failure on camera.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-5055}"
NGROK="${NGROK:-ngrok}"

cleanup() {
  [[ -n "${SRV:-}" ]] && kill "$SRV" 2>/dev/null || true
  [[ -n "${TUN:-}" ]] && kill "$TUN" 2>/dev/null || true
}
trap cleanup EXIT

echo "1/4  Triaging the inbox..."
python3 triage.py "$@"

echo
echo "2/4  Starting the call server on :$PORT..."
PORT="$PORT" python3 -u realtime_server.py >/tmp/izol-server.log 2>&1 &
SRV=$!
sleep 3

echo "3/4  Opening the tunnel..."
"$NGROK" http "$PORT" --log=stdout >/tmp/izol-ngrok.log 2>&1 &
TUN=$!
sleep 5
URL=$(curl -s --max-time 5 http://127.0.0.1:4040/api/tunnels \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['tunnels'][0]['public_url'] if d.get('tunnels') else '')")

if [[ -z "$URL" ]]; then
  echo "     No tunnel -- falling back to a one-way call."
else
  echo "     $URL"
fi

echo
echo "4/4  Placing the call."
python3 call.py

echo
echo "Call placed. Watch /tmp/izol-server.log. Ctrl-C when the call ends."
wait "$SRV"
