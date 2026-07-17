/**
 * Dev-server proxy for the runner local panel (`ng serve runner`).
 *
 * The twin of proxy.conf.hub.js, aimed at the OTHER daemon: the panel's queries go through
 * `runnerApi` (local-panel/src/lib/*.query.ts), whose generated client also carries an empty
 * baseUrl. Both apps therefore request the same `/api/*` path prefix but must reach different
 * daemons — which is why each app gets its own proxy file rather than sharing one.
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
};
