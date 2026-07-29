#!/usr/bin/env bash
# blizzard:image-smoke — builds the wheel + the hub container image, boots it on
# an empty data volume, and asserts the acceptance criteria a unit test cannot
# reach: non-root uid, git on PATH, the postgres driver importable, the store at
# head *before* the daemon serves, and a live GET /api/health + GET /api/ready.
#
# Local-only (no CI arch/multi-arch proof here — see docs/ci.md and the plan's
# named gap on the GHCR publish leg). Requires a reachable docker daemon; errors
# with a clear message rather than a docker CLI stack trace when there isn't one.
#
# Invoke as `mise run image-smoke` or `./scripts/image-smoke.sh`.
set -euo pipefail

cd "$(dirname "$0")/.."

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

if ! docker info >/dev/null 2>&1; then
  fail "no reachable docker daemon — image-smoke needs 'docker build'/'docker run'. Start Docker and re-run."
fi

IMAGE_TAG="blizzard-hub:image-smoke"
# A named volume, not a host bind-mount: the image's `chown` (Dockerfile, before its
# `VOLUME` line) only seeds a fresh **named** volume's ownership — a host directory
# keeps the host uid, which the non-root `blizzard` user then can't write to.
DATA_VOLUME="blizzard-image-smoke-data-$$"
CONTAINER="blizzard-image-smoke-$$"
HOST_PORT="18421"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker volume rm "$DATA_VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "Building the wheel (mise run build)"
./scripts/build-wheel.sh

log "Building the image ($IMAGE_TAG)"
docker build -f packaging/docker/Dockerfile -t "$IMAGE_TAG" .

log "Starting a container on an empty data volume"
docker volume create "$DATA_VOLUME" >/dev/null
docker run -d --name "$CONTAINER" \
  -p "$HOST_PORT:8421" \
  -v "$DATA_VOLUME:/var/lib/blizzard/hub" \
  "$IMAGE_TAG" >/dev/null

log "Asserting the container runs as a non-root uid"
UID_IN_CONTAINER="$(docker exec "$CONTAINER" id -u)"
[ "$UID_IN_CONTAINER" != "0" ] || fail "container is running as uid 0 (root)"
echo "OK: uid=$UID_IN_CONTAINER"

log "Asserting git resolves on PATH"
docker exec "$CONTAINER" git --version >/dev/null || fail "git not resolvable on the image PATH"
echo "OK: git present"

log "Asserting the postgres driver is importable"
docker exec "$CONTAINER" python3 -c "import psycopg" || fail "psycopg not importable in the image"
echo "OK: psycopg importable"

log "Waiting for the daemon to report healthy"
READY=""
for _ in $(seq 1 30); do
  if READY="$(curl -fs "http://127.0.0.1:$HOST_PORT/api/ready" 2>/dev/null)"; then
    if [ "$(echo "$READY" | jq -r .ready)" = "true" ]; then
      break
    fi
  fi
  sleep 1
done
[ -n "$READY" ] || fail "GET /api/ready never responded"
[ "$(echo "$READY" | jq -r .ready)" = "true" ] || fail "GET /api/ready reported ready=false: $READY"
echo "OK: store at head before serving ($READY)"

log "Asserting GET /api/health is live"
HEALTH_STATUS="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$HOST_PORT/api/health")"
[ "$HEALTH_STATUS" = "200" ] || fail "GET /api/health returned $HEALTH_STATUS, expected 200"
echo "OK: /api/health 200"

log "image-smoke OK: $IMAGE_TAG builds, boots non-root, and serves a ready hub."
