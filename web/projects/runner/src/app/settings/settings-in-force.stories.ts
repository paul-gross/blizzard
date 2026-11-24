import type { Meta, StoryObj } from '@storybook/angular-vite';

import { SettingsInForce } from './settings-in-force';

const meta: Meta<SettingsInForce> = {
  title: 'Runner/Settings in-force line',
  component: SettingsInForce,
  args: { testid: 'settings-rn_1', line: { running: 6, since: '2026-11-20T22:14:00Z', pending: null, failed: null } },
};
export default meta;
type Story = StoryObj<SettingsInForce>;

export const InForce: Story = {};
export const Pending: Story = { args: { line: { running: 6, since: '2026-11-20T22:14:00Z', pending: 7, failed: null } } };
export const NeverInForce: Story = { args: { line: { running: null, since: null, pending: 2, failed: null } } };
export const FailedRebuild: Story = {
  args: {
    line: {
      running: 6,
      since: '2026-11-20T22:14:00Z',
      pending: null,
      failed: { revision: 7, error: "runner_prompt_file: no such file '/etc/blizzard/missing-prompt.md'", since: '2026-11-20T22:20:00Z' },
    },
  },
};
export const FailedThenPending: Story = {
  args: {
    line: {
      running: 6,
      since: '2026-11-20T22:14:00Z',
      pending: 8,
      failed: { revision: 7, error: 'runner_prompt_file: no such file', since: '2026-11-20T22:20:00Z' },
    },
  },
};
export const PhoneSm: Story = { ...FailedRebuild, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
