import type { StorybookConfig } from '@storybook/angular-vite';
import { mergeConfig } from 'vite';

const config: StorybookConfig = {
  framework: '@storybook/angular-vite',
  stories: ['../src/lib/**/*.stories.ts', '../../hub/src/app/shell/**/*.stories.ts'],
  // The static build runs in Vite's production mode, which folds `ngDevMode` to false and strips
  // the messages Angular logs only in development mode, such as a story template binding an
  // input its part does not have. The render check relies on those messages, so the build runs
  // in development mode.
  viteFinal: (viteConfig) => mergeConfig(viteConfig, { mode: 'development' }),
};
export default config;
