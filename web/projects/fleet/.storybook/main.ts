import type { StorybookConfig } from '@storybook/angular-vite';

const config: StorybookConfig = {
  framework: '@storybook/angular-vite',
  stories: ['../src/lib/**/*.stories.ts', '../../hub/src/app/shell/**/*.stories.ts'],
};
export default config;
