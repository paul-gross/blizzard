#!/bin/sh
# The hub container's ordered boot: scaffold-if-absent, then migrate, then serve
# — three distinct steps, never collapsed into one (`bzh:manual-migrations`). The
# daemon's own startup path carries no migrate call; this entrypoint is the only
# place that ever runs one, mirroring packaging/systemd/blizzard-hub.service's
# ExecStartPre.
#
# Scaffolding is conditional (a fresh empty volume has no config yet) so a
# re-created container against an already-initialized volume never re-scaffolds —
# an unconditional `init` would migrate twice and blur the ordering this
# entrypoint exists to keep literal.
#
# An explicit command (`docker run <image> <cmd>...` / `docker compose run
# hub <cmd>...`) bypasses the default boot sequence entirely and execs that
# command instead — the standard entrypoint escape hatch, and what
# `docs/rollback.md` uses to run a one-off `migrate --down` without booting the
# daemon. No args (the normal `docker compose up` path) keeps the three-step
# ordering exactly as before.
set -eu

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

: "${BZ_HUB_DIR:=/var/lib/blizzard/hub}"

if [ ! -f "$BZ_HUB_DIR/blizzard-hub.toml" ]; then
    blizzard-hub init "$BZ_HUB_DIR"
fi
blizzard-hub migrate --dir "$BZ_HUB_DIR"
exec blizzard-hub host --dir "$BZ_HUB_DIR"
