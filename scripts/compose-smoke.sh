#!/usr/bin/env bash
# blizzard:compose-smoke — stands up the reference compose deployment
# (packaging/docker/compose.yaml) against a locally-built image on the
# localhost http-only evaluation profile, and asserts what a docker-free unit
# test cannot: the stack comes up, the board is served *through the proxy*, the
# hub's resolved store is the postgres one, and `docker compose down` (no `-v`)
# followed by `up` loses nothing.
#
# Local-only, like image-smoke. Requires a reachable docker daemon and the
# `docker compose` plugin.
#
# Invoke as `mise run compose-smoke` or `./scripts/compose-smoke.sh`.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE_DIR="packaging/docker"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

if ! docker info >/dev/null 2>&1; then
  fail "no reachable docker daemon — compose-smoke needs 'docker compose'. Start Docker and re-run."
fi
if ! docker compose version >/dev/null 2>&1; then
  fail "'docker compose' plugin not found."
fi

IMAGE_TAG="blizzard-hub:compose-smoke"
PROJECT="blizzard-compose-smoke-$$"
HTTP_PORT="18080"
ENV_FILE="$(mktemp)"
POSTGRES_USER="smoke"
POSTGRES_PASSWORD="smoke"
POSTGRES_DB="smoke"

compose() {
  docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_DIR/compose.yaml" "$@"
}

cleanup() {
  compose down --volumes >/dev/null 2>&1 || true
  rm -f "$ENV_FILE"
}
trap cleanup EXIT

cat > "$ENV_FILE" <<EOF
BLIZZARD_SITE_ADDRESS=:80
BLIZZARD_HTTP_PORT=$HTTP_PORT
BLIZZARD_HTTPS_PORT=18443
BLIZZARD_HUB_IMAGE=$IMAGE_TAG
POSTGRES_USER=$POSTGRES_USER
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_DB=$POSTGRES_DB
EOF

log "Building the wheel + the hub image ($IMAGE_TAG) for the smoke run"
./scripts/build-wheel.sh
docker build -f packaging/docker/Dockerfile -t "$IMAGE_TAG" .

assert_ready_through_the_proxy() {
  local ready=""
  for _ in $(seq 1 30); do
    if ready="$(curl -fs "http://127.0.0.1:$HTTP_PORT/api/ready" 2>/dev/null)"; then
      [ "$(echo "$ready" | jq -r .ready)" = "true" ] && { echo "$ready"; return 0; }
    fi
    sleep 1
  done
  fail "GET /api/ready through the proxy never reported ready=true"
}

log "Bringing the stack up (localhost http-only evaluation profile)"
compose up -d --wait

log "Asserting the board is served through the proxy and ready"
READY="$(assert_ready_through_the_proxy)"
echo "OK: $READY"

log "Asserting the hub's resolved store is the postgres one"
DB_URL="$(compose exec -T hub printenv BZ_HUB_DB_URL)"
case "$DB_URL" in
  postgresql*postgres:5432/"$POSTGRES_DB") echo "OK: BZ_HUB_DB_URL=$DB_URL" ;;
  *) fail "hub is not resolving the postgres BZ_HUB_DB_URL, got: $DB_URL" ;;
esac

log "Writing a durable artifact directly into the postgres volume"
compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "CREATE TABLE IF NOT EXISTS compose_smoke_durability (id int); INSERT INTO compose_smoke_durability VALUES (1);" \
  >/dev/null

log "docker compose down (no -v) then up — the durability promise"
compose down
compose up -d --wait

READY_AFTER="$(assert_ready_through_the_proxy)"
echo "OK: $READY_AFTER"

ROW_COUNT="$(compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT count(*) FROM compose_smoke_durability;")"
[ "$(echo "$ROW_COUNT" | tr -d '[:space:]')" = "1" ] || fail "the durable artifact written before the restart is gone after it"
echo "OK: the artifact written before 'down' is still readable after 'up' — nothing lost"

log "compose-smoke OK: the stack comes up, is served through the proxy on postgres, and survives down+up."
