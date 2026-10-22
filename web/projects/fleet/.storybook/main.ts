import type { StorybookConfig } from '@storybook/angular-vite';
import { fileURLToPath } from 'node:url';
import { mergeConfig } from 'vite';

const config: StorybookConfig = {
  framework: '@storybook/angular-vite',
  stories: ['../src/lib/**/*.stories.ts', '../../hub/src/app/shell/**/*.stories.ts', '../../runner/src/app/runners/**/*.stories.ts'],
  // Vite does not read `tsconfig.json`'s `paths`, so the spec-support barrel that stories draw
  // their data from, `fleet/testing`, is aliased here to the file the tsconfig maps it to.
  // The static build runs in Vite's production mode, which folds `ngDevMode` to false and strips
  // the messages Angular logs only in development mode, such as a story template binding an
  // input its part does not have. The render check relies on those messages, so the build runs
  // in development mode.
  viteFinal: (viteConfig) =>
    mergeConfig(viteConfig, {
      mode: 'development',
      resolve: { alias: { 'fleet/testing': fileURLToPath(new URL('../src/lib/testing/public-api.ts', import.meta.url)) } },
    }),
};
export default config;
