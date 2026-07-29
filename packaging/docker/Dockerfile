# The blizzard hub container image — the public distribution artifact a stranger
# `docker compose up`s. Built from the release wheel (`mise run build` /
# `scripts/build-wheel.sh`), which already embeds both Angular frontends and both
# migration trees, so this image carries no Node and no separate frontend tier.
#
# Build (from the repo root, after `mise run build` has populated dist/):
#   docker build -f packaging/docker/Dockerfile -t blizzard-hub:local .
#
# See packaging/docker/README.md for the mounts, env vars, and the ordered
# entrypoint steps (migrate before host — `bzh:manual-migrations`).
FROM python:3.12-slim

# git: delivery `run:` scripts clone into hub workdirs (bzh:deterministic-shell).
# ca-certificates: TLS to a real forge/work-source origin and (when configured)
# a postgres server requiring a verified certificate.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root: the daemon runs as `blizzard`, owning the one durable-state mount.
RUN groupadd --system blizzard \
    && useradd --system --gid blizzard --create-home --home-dir /home/blizzard blizzard \
    && mkdir -p /var/lib/blizzard/hub \
    && chown -R blizzard:blizzard /var/lib/blizzard

COPY dist/blizzard-*.whl /tmp/
# The postgres driver so the store URL is a real choice, not just a declared one
# (bzh:sql-portable) — installed as the `postgres` extra, kept out of the base wheel
# so the sqlite/systemd deployment stays lean.
RUN pip install --no-cache-dir "$(ls /tmp/blizzard-*.whl)[postgres]" \
    && rm -f /tmp/blizzard-*.whl

COPY packaging/docker/entrypoint.sh /usr/local/bin/blizzard-hub-entrypoint
RUN chmod +x /usr/local/bin/blizzard-hub-entrypoint

USER blizzard
WORKDIR /var/lib/blizzard/hub

# The single documented mount for everything durable — store (sqlite default),
# signing keys, hub workdirs, and the config file (packaging/docker/README.md).
VOLUME ["/var/lib/blizzard/hub"]

ENV BZ_HUB_DIR=/var/lib/blizzard/hub \
    BZ_HUB_HOST=0.0.0.0 \
    BZ_LOG_FORMAT=json

EXPOSE 8421

# No curl in the base image — a stdlib request keeps the image lean. Reads
# $BZ_HUB_PORT at healthcheck time so an operator override (docs/deployment.md)
# is honored without editing the image.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python3 -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:' + os.environ.get('BZ_HUB_PORT', '8421') + '/api/health', timeout=2)"

ENTRYPOINT ["/usr/local/bin/blizzard-hub-entrypoint"]
