import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';

// The bans a service spec keeps (blizzard-context's service-test rules): the built-in `request` fixture, the clock,
// raw `fetch`, state set up in `beforeAll`, and any harness module but the fixtures. Static analysis sees these; the
// rest of the rules are the tier's to hold by review and the cross-check.
const noRequestFixture = {
  selector: "ObjectPattern > Property[key.name='request']",
  message: "Use the harness clients (api, fleet, forge); Playwright's built-in `request` reaches no tenant.",
};
const noWaitForTimeout = {
  selector: "CallExpression[callee.property.name='waitForTimeout']",
  message: 'Wait on an event or a state the test can read, never on the clock.',
};
const noSharedHooks = {
  selector: "CallExpression[callee.object.name='test'][callee.property.name=/^(beforeAll|afterAll)$/]",
  message: 'Shared state across a file belongs in a fixture.',
};
const noFetch = { name: 'fetch', message: 'Every call goes through a harness client.' };
const noSetTimeout = { name: 'setTimeout', message: 'Wait on an event or a state the test can read, never on the clock.' };
const noSetInterval = { name: 'setInterval', message: 'Wait on an event or a state the test can read, never on the clock.' };
const onlyFixtures = {
  patterns: [
    {
      regex: '(^|/)harness/(?!fixtures(\\.ts)?$)',
      message: 'A spec imports from harness/fixtures and from nothing else in the harness.',
    },
    {
      regex: '^\\.{1,2}/(?!.*fixtures)(?=.*harness)',
      message: 'A spec imports from harness/fixtures and from nothing else in the harness.',
    },
  ],
};

// The runner world rests on `base` alone: nothing it is built from may import the hub world, which owns the shared hub,
// the shared reset, and the sweeps.
const noHubWorld = {
  patterns: [
    {
      regex: '(^|/)(tenancy|hubs|factories|people)(\\.ts)?$|(^|/)client/clients(\\.ts)?$',
      message: 'The runner world imports nothing from the hub world (tenancy, hubs, factories, people, client/clients).',
    },
  ],
};

// What every service spec is kept from: a raw process, socket, store, or clock, and Playwright itself. The fixtures
// own these, so a spec states what it expects of the system and nothing of how it is reached.
const bans = (names, message) => names.map((name) => ({ name, message }));
const sharedPaths = [
  ...bans(['node:child_process', 'child_process'], 'Run a program through a fixture (cli, runnerHost.cli); a spec spawns nothing.'),
  ...bans(['node:http', 'http', 'node:https', 'https', 'node:net', 'net'], 'Every call goes through a harness client.'),
  ...bans(['node:sqlite'], 'A spec reads the system through its public surfaces; only runner/store/ opens a store, before start or after stop.'),
  ...bans(['node:timers', 'timers', 'node:timers/promises', 'timers/promises'], 'Wait on an event or a state the test can read, never on the clock.'),
  ...bans(['@playwright/test'], 'A spec imports test and expect from harness/fixtures, which carry the isolation and clients.'),
];
const fsPaths = bans(
  ['node:fs', 'fs', 'node:fs/promises', 'fs/promises'],
  'A runner or fleet spec prepares nothing on disk; the host fixtures and the runnerHost handle do (runner/store/ is the one exception).',
);
const fixturesModule = '(^|/)harness/fixtures(\\.ts)?$|^\\.{1,2}/(?:.*/)?fixtures(\\.ts)?$';
const worldPattern = (importNames, message) => ({ regex: fixturesModule, importNames, message });
const restrictedImports = (paths, ...worlds) => ({
  'no-restricted-imports': ['error', { paths, patterns: [...onlyFixtures.patterns, ...worlds] }],
});
const noHubTest = worldPattern(['test'], 'A runner spec takes `runnerTest`: it runs on a mock hub of its own, never on a shared hub.');
const noRunnerTest = worldPattern(['runnerTest'], 'A hub or fleet spec takes `test`: `runnerTest` is the runner folder’s world, with no hub.');

export default tseslint.config(
  { ignores: ['node_modules/**', 'test-results/**', 'playwright-report/**', '.run/**', 'harness/client/wire/**'] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.ts'],
    rules: {
      // Playwright's idiom for a fixture-less hook: `async ({}, use) => …`.
      'no-empty-pattern': ['error', { allowObjectPatternsAsParameters: true }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
  {
    files: ['service/**/*.spec.ts', 'harness/checks/**/*.spec.ts'],
    rules: {
      'no-restricted-syntax': ['error', noRequestFixture, noWaitForTimeout, noSharedHooks],
      'no-restricted-globals': ['error', noFetch],
    },
  },
  {
    files: ['service/**/*.spec.ts', 'harness/checks/runner/round-trip.spec.ts'],
    rules: {
      'no-restricted-globals': ['error', noFetch, noSetTimeout, noSetInterval],
      ...restrictedImports(sharedPaths, noRunnerTest),
    },
  },
  {
    files: ['service/fleet/**/*.spec.ts'],
    rules: restrictedImports([...sharedPaths, ...fsPaths], noRunnerTest),
  },
  {
    files: ['service/runner/**/*.spec.ts', 'harness/checks/runner/round-trip.spec.ts'],
    rules: restrictedImports([...sharedPaths, ...fsPaths], noHubTest),
  },
  {
    // The store folder reads and prepares a runner's store, before a host starts or after it stops.
    files: ['service/runner/store/**/*.spec.ts'],
    rules: restrictedImports(
      sharedPaths.filter((entry) => entry.name !== 'node:sqlite'),
      noHubTest,
    ),
  },
  {
    files: [
      'harness/base.ts',
      'harness/mock-world.ts',
      'harness/auth-stub.ts',
      'harness/daemons/mock-hub.ts',
      'harness/daemons/runner-host.ts',
      'harness/runner-host.ts',
      'harness/runner-world.ts',
      'harness/vendor-usage.ts',
      'harness/client/engine.ts',
      'harness/client/mock-hub.ts',
      'harness/client/runner.ts',
    ],
    rules: { 'no-restricted-imports': ['error', noHubWorld] },
  },
);
