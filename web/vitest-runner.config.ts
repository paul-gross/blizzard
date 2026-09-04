import { defineConfig } from 'vitest/config';

/** Layered into every project's own config via the `test` builder's `runnerConfig`
 * option (angular/angular-cli#32832 — see `vitest-global-teardown.ts` for why). */
export default defineConfig({
  test: {
    globalSetup: ['./vitest-global-teardown.ts'],
  },
});
