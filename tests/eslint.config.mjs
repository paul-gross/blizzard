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
    files: ['service/**/*.spec.ts'],
    rules: { 'no-restricted-imports': ['error', onlyFixtures] },
  },
  {
    files: [
      'harness/base.ts',
      'harness/mock-world.ts',
      'harness/auth-stub.ts',
      'harness/daemons/mock-hub.ts',
      'harness/daemons/runner-host.ts',
      'harness/runner-host.ts',
      'harness/vendor-usage.ts',
      'harness/client/engine.ts',
      'harness/client/mock-hub.ts',
      'harness/client/runner.ts',
    ],
    rules: { 'no-restricted-imports': ['error', noHubWorld] },
  },
);
