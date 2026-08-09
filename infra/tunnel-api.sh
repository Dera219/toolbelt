#!/usr/bin/env bash
# Expose the local API through Expo's ngrok agent, on the sibling hostname of the
# Metro tunnel (…-8081.exp.direct → …-8000.exp.direct). The mobile app derives that
# name automatically (see mobile/src/config.ts).
#
# Use when the phone cannot reach the Mac over Wi-Fi (AP isolation on campus or
# public networks). Run AFTER `npx expo start --tunnel` is up.
set -euo pipefail

NGROK_API="http://127.0.0.1:4040/api/tunnels"
API_PORT="${API_PORT:-8000}"

metro_host=$(curl -fsS --max-time 10 "$NGROK_API" \
  | python3 -c "
import json, sys
tunnels = json.load(sys.stdin).get('tunnels', [])
hosts = [t['public_url'].split('://')[1] for t in tunnels if t['public_url'].startswith('https')]
metro = next((h for h in hosts if h.endswith('-8081.exp.direct')), None)
print(metro or '', end='')
")

if [ -z "$metro_host" ]; then
  echo "No Expo tunnel found. Start it first:  npx expo start --tunnel" >&2
  exit 1
fi

api_host="${metro_host/-8081./-$API_PORT.}"

curl -fsS --max-time 15 -X POST "$NGROK_API" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"toolbelt-api\",\"proto\":\"http\",\"addr\":\"$API_PORT\",\"hostname\":\"$api_host\"}" \
  >/dev/null

echo "API tunneled:  https://$api_host  ->  localhost:$API_PORT"
