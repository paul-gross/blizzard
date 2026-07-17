/**
 * Dev-server proxy for the hub board (`ng serve hub`).
 *
 * The generated hub client carries an empty baseUrl (fleet/src/lib/api/hub/client.gen.ts),
 * so the board issues same-origin `/api/*` requests. Under `blizzard hub host` that is the
 * daemon's own origin — it serves the built bundle beside its API (foundation/web.py). Under
 * the dev server there is no API on the origin, so `/api` is proxied to the hub daemon here.
 *
 * The port is read from the environment rather than pinned: every feature env binds its own
 * hub port from the winter band (workspace:/.winter/config.toml [env.feature.vars]), so a
 * literal here would be wrong in all but one env.
 *
 * `GET /api/events/stream` — the SSE spine the board invalidates its reads on — needs no
 * option of its own: SSE is a plain HTTP response the proxy streams through as it arrives.
 * (It is *not* a WebSocket, so `ws: true` would do nothing for it; the app opens no
 * WebSocket at all.)
 */
const port = process.env.BZ_HUB_PORT;

if (!port) {
  throw new Error(
    'BZ_HUB_PORT is not set. Run the dev server under `winter service up <env>`, which injects ' +
      'the env band, or set it explicitly for an ad-hoc client (e.g. `BZ_HUB_PORT=4582 npm start`).',
  );
}

module.exports = {
  '/api': {
    target: `http://127.0.0.1:${port}`,
  },
};
