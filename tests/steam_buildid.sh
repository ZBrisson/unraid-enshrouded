#!/usr/bin/env bash
# Print the current Steam build id of the template's GAME_BRANCH, as steamcmd sees it.
# Fails if steamcmd can't log in anonymously or the app/branch doesn't exist.
# Env: RUNTIME (docker|podman, default docker).
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
runtime=${RUNTIME:-docker}
branch=$(python3 "$root/tools/template.py" get "$root/my-enshrouded.xml" GAME_BRANCH)

for attempt in 1 2 3; do
  # The first app_info_print after login is sometimes empty; the parser takes the last block.
  out=$("$runtime" run --rm docker.io/steamcmd/steamcmd:latest \
    +login anonymous +app_info_update 1 +app_info_print 2278520 +app_info_print 2278520 +quit 2>&1 || true)
  if id=$(python3 "$root/tools/steam_build.py" --branch "$branch" <<<"$out"); then
    echo "$id"
    exit 0
  fi
  # Retrying can't fix a runtime that blocks steamcmd's sockets (Docker 29.4.2's seccomp
  # profile does); say so instead of looking like a Steam outage.
  if grep -q 'CreateBoundSocket: failed to create socket' <<<"$out"; then
    printf '%s\n' "$out" | tail -40 >&2
    echo "::error::steamcmd cannot open a socket in the container; check the runtime version ($("$runtime" version --format '{{.Server.Version}}' 2>/dev/null || echo unknown))" >&2
    exit 1
  fi
  echo "attempt $attempt: no build id yet" >&2
  sleep 10
done
printf '%s\n' "$out" | tail -40 >&2
exit 1
