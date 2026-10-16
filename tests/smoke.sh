#!/usr/bin/env bash
# Boot the real container from the template, exactly as Unraid would create it, and check:
#   1. first start: the game downloads and the server answers A2S on the query port
#   2. enshrouded_server.json matches the template; files are owned 99:100;
#      the installed build is the one steamcmd reports (EXPECTED_BUILD)
#   3. docker stop finishes inside the stop timeout (not SIGKILLed) and leaves a save
#   4. second start reuses the install; a backup zip with the save lands in /backups
#
# Env: RUNTIME (docker|podman, default docker), IMAGE (full image ref, default the template's),
#      EXPECTED_BUILD (optional), WORK (scratch dir, default mktemp), KEEP=1 to keep it all,
#      START_TIMEOUT (seconds, default 2700).
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
template=$root/my-enshrouded.xml
runtime=${RUNTIME:-docker}
name=enshrouded-smoke
port=15637
work=${WORK:-$(mktemp -d)}
data=$work/appdata
backups=$work/backup
logs=$work/logs
start_timeout=${START_TIMEOUT:-2700}
mkdir -p "$data" "$backups" "$logs"

step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
in_ctr() { "$runtime" exec "$name" "$@"; }

cleanup() {
  "$runtime" logs "$name" >"$logs/container.log" 2>&1 || true
  if [[ ${KEEP:-0} != 1 ]]; then
    "$runtime" rm -f "$name" >/dev/null 2>&1 || true
  fi
  echo "logs: $logs"
}
trap cleanup EXIT

overrides=(
  "/opt/enshrouded=$data"
  "/backups=$backups"
  "SERVER_NAME=smoke-${GITHUB_RUN_ID:-local}"
  "SERVER_ROLE_0_PASSWORD=smoke-admin-$RANDOM"
  "SERVER_ROLE_1_PASSWORD=smoke-friend-$RANDOM"
)
set_args=()
for o in "${overrides[@]}"; do set_args+=(--set "$o"); done

mapfile -t args < <(python3 "$root/tools/template.py" render "$template" \
  --name "$name" --tz America/New_York --format lines "${set_args[@]}")
if [[ -n ${IMAGE:-} ]]; then
  args[-1]=$IMAGE
fi
image=${args[-1]}

step "create ($runtime, $image)"
printf '%q ' "$runtime" run -d "${args[@]}" | sed -E 's/(PASSWORD=)[^ ]+/\1***/g'; echo
"$runtime" rm -f "$name" >/dev/null 2>&1 || true
"$runtime" run -d "${args[@]}" >/dev/null

wait_a2s() {
  local started=$SECONDS reply
  if ! reply=$(python3 "$root/tools/a2s.py" 127.0.0.1 "$port" --wait "$1"); then
    "$runtime" logs --tail 80 "$name" >&2 || true
    fail "no A2S reply within $1 s"
  fi
  echo "A2S after $((SECONDS - started)) s: $reply" >&2
  echo "$reply"
}

step "1. first start (downloads the game)"
reply=$(wait_a2s "$start_timeout")
python3 - "$reply" "smoke-${GITHUB_RUN_ID:-local}" <<'PY'
import json, sys
r, want = json.loads(sys.argv[1]), sys.argv[2]
assert r["name"] == want, f"A2S name {r['name']!r} != {want!r}"
assert r["max_players"] == 16, f"A2S max_players {r['max_players']}"
PY

step "2. generated config, ownership, build, limits"
in_ctr cat /opt/enshrouded/server/enshrouded_server.json >"$logs/enshrouded_server.json"
python3 "$root/tests/check_config.py" "$template" "$logs/enshrouded_server.json" "${overrides[@]}"
owner=$(in_ctr stat -c %u:%g /opt/enshrouded/server/enshrouded_server.json)
[[ $owner == 99:100 ]] || fail "server files owned by $owner, want 99:100"
if [[ $runtime == docker ]]; then
  host_owner=$(stat -c %u:%g "$data/server/enshrouded_server.json")
  [[ $host_owner == 99:100 ]] || fail "host sees $host_owner, want 99:100"
fi
# Steam's own manifest; the image's current_version file only appears after its second update check.
installed=$(in_ctr sed -nE 's/^[[:space:]]*"buildid"[[:space:]]+"([0-9]+)".*/\1/p' \
  /opt/enshrouded/server/steamapps/appmanifest_2278520.acf | head -1)
echo "installed build: ${installed:-unknown}; steamcmd says: ${EXPECTED_BUILD:-not checked}"
echo "image's current_version: $(in_ctr cat /opt/enshrouded/current_version 2>/dev/null || echo 'not yet written')"
if [[ -n ${EXPECTED_BUILD:-} && $installed != "$EXPECTED_BUILD" ]]; then
  fail "installed build $installed != steamcmd build $EXPECTED_BUILD"
fi
pids=$("$runtime" inspect -f '{{.HostConfig.PidsLimit}}' "$name")
[[ $pids == 2048 ]] || fail "pids limit $pids, want Unraid's 2048"
used=$("$runtime" stats --no-stream --format '{{.PIDs}}' "$name")
echo "pids in use: $used / 2048"
(( used < 1536 )) || fail "using $used of 2048 pids; Unraid's default limit is too tight"
# shellcheck disable=SC2016 # expands inside the container
in_ctr sh -c 'echo "container clock: $(date) TZ=$TZ"'
if [[ $(in_ctr date +%Z) == UTC ]]; then
  echo "::warning::TZ=America/New_York is ignored in the container; cron schedules run in UTC"
fi

step "3. graceful stop"
stop_timeout=$("$runtime" inspect -f '{{.Config.StopTimeout}}' "$name")
t0=$SECONDS
"$runtime" stop "$name" >/dev/null
took=$((SECONDS - t0))
code=$("$runtime" inspect -f '{{.State.ExitCode}}' "$name")
echo "stopped in ${took}s (timeout ${stop_timeout}s), exit code $code"
(( code != 137 )) || fail "SIGKILLed after ${stop_timeout}s: the server did not shut down cleanly"
"$runtime" logs "$name" 2>&1 | grep -q "Shutdown complete" || fail "no 'Shutdown complete' in the log"
saves=$(find "$data/server/savegame" -maxdepth 1 -type f \( -name 'world*.ksd' -o -name '3ad85aea*' \) 2>/dev/null | wc -l)
(( saves > 0 )) || fail "no world save in server/savegame after a clean stop"

step "4. restart and backup"
"$runtime" start "$name" >/dev/null
wait_a2s 900 >/dev/null
in_ctr supervisorctl start enshrouded-backup
for _ in $(seq 30); do
  zip=$(find "$backups" -maxdepth 1 -name '*.zip' | head -1)
  [[ -n $zip ]] && break
  sleep 2
done
[[ -n ${zip:-} ]] || fail "no backup zip in /backups"
in_ctr sh -c 'ls -ln /backups'
in_ctr sh -c 'unzip -l /backups/*.zip 2>/dev/null || python3 -m zipfile -l /backups/*.zip' | tee "$logs/backup.txt"
grep -Eq 'world[0-9]*\.ksd|3ad85aea' "$logs/backup.txt" || fail "backup zip has no save file"
if [[ $runtime == docker ]]; then
  [[ $(stat -c %u:%g "$zip") == 99:100 ]] || fail "backup zip not owned by 99:100"
fi
"$runtime" stop "$name" >/dev/null

step "PASS"
