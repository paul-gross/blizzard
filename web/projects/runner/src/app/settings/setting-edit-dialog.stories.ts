import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { SettingEditDialogHost } from './setting-edit-dialog.story-host';

const meta: Meta<SettingEditDialogHost> = {
  title: 'Runner/Setting edit dialog',
  component: SettingEditDialogHost,
  args: {
    value: '10',
    recordTitle: 'r-claude',
    settingKey: 'throttle.budget.usd',
    lifetime: { label: 'pass', tone: 'running' },
    canReset: true,
    fieldError: null,
    formError: null,
    submitting: false,
    save: fn(),
    resetToDefault: fn(),
    closed: fn(),
  },
};
export default meta;
type Story = StoryObj<SettingEditDialogHost>;

export const Editing: Story = {};
export const AtDefault: Story = { args: { value: 'null', canReset: false } };
export const SessionKey: Story = {
  args: { settingKey: 'runner_prompt_file', value: '/etc/blizzard/prompt.md', lifetime: { label: 'session', tone: 'waiting' } },
};
export const RefusedValue: Story = { args: { value: '-1', fieldError: 'throttle.budget.usd must be positive' } };
export const RevisionConflict: Story = {
  args: { value: '25', formError: 'the settings record is at revision 8, not 7' },
};
export const Saving: Story = { args: { value: '25', submitting: true } };
export const LongValue: Story = {
  args: {
    settingKey: 'worker.env_passthrough',
    value: '["ANTHROPIC_API_KEY","GITHUB_TOKEN","OPENAI_API_KEY","AWS_PROFILE","AWS_REGION","SSH_AUTH_SOCK","HOME"]',
  },
};
export const Phone: Story = { ...RefusedValue, globals: { viewport: { value: 'phone', isRotated: false } } };
export const PhoneSm: Story = { ...RefusedValue, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
