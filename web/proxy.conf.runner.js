/**
 * Dev-server proxy for the runner host's panel (`ng serve runner`).
 *
 * The twin of proxy.conf.hub.js, aimed at the OTHER daemon: the panel's queries go through
 * `runnerApi` (`runner/src/app/` query files), whose generated client also carries an empty
 * baseUrl. Both apps request `/api/*` but must reach different daemons — which is why each
 * app gets its own proxy file rather than sharing one.
 *
 * The host serves two kinds of path: its own at the root (`/api/*`), and each runner's under
 * `/runners/<id>/`. Only the API-shaped runner paths — `/runners/<id>/api/*` and the OTLP
 * receiver's `/runners/<id>/v1/*` — are forwarded, by the `^`-anchored regular-expression
 * key the dev server accepts. Every other `/runners/*` path is one of the panel's own routes
 * (`/runners/<id>/board`), which a reload must keep serving from the dev build, not from the
 * daemon's embedded bundle.
 *
 * See proxy.conf.hub.js for why the port is read from the environment.
 */
const port = process.env.BZ_RUNNER_PORT;

if (!port) {
  throw new Error(
    'BZ_RUNNER_PORT is not set. Run the dev server under `winter service up <env>`, which injects ' +
      'the env band, or set it explicitly for an ad-hoc client (e.g. `BZ_RUNNER_PORT=4583 npm run start:runner`).',
  );
}

module.exports = {
  '/api': {
    target: `http://127.0.0.1:${port}`,
  },
  '^/runners/[^/]+/(api|v1)/': {
    target: `http://127.0.0.1:${port}`,
  },
};
