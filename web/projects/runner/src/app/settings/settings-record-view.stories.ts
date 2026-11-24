import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import type { SettingGroupVm, SettingRowVm } from './settings.model';
import { SettingsRecordView } from './settings-record-view';

function row(overrides: Partial<SettingRowVm>): SettingRowVm {
  return {
    key: 'agents',
    name: 'agents',
    value: '4',
    source: 'default',
    lifetime: { label: 'pass', tone: 'running' },
    writtenAt: null,
    writtenBy: null,
    editable: true,
    ...overrides,
  };
}

const groups: readonly SettingGroupVm[] = [
  {
    table: '',
    rows: [
      row({ key: 'name', name: 'name', value: 'r-claude', lifetime: { label: 'immutable', tone: 'idle' }, editable: false }),
      row({ key: 'agents' }),
      row({ key: 'runner_prompt_file', name: 'runner_prompt_file', value: 'null', lifetime: { label: 'session', tone: 'waiting' } }),
    ],
  },
  {
    table: 'throttle.budget',
    rows: [
      row({
        key: 'throttle.budget.usd',
        name: 'usd',
        value: '25',
        source: 'entry',
        writtenAt: '2026-11-20T22:13:00Z',
        writtenBy: 'paul',
      }),
      row({ key: 'throttle.budget.window_hours', name: 'window_hours', value: '24' }),
    ],
  },
];

const readOnly = groups.map((g) => ({ ...g, rows: g.rows.map((r) => ({ ...r, editable: false })) }));

const meta: Meta<SettingsRecordView> = {
  title: 'Runner/Settings record',
  component: SettingsRecordView,
  args: {
    title: 'r-claude',
    recordId: 'rn_1',
    state: 'ready',
    line: { running: 6, since: '2026-11-20T22:14:00Z', pending: 7, failed: null },
    groups,
    settingsError: null,
    editDisabled: false,
    edit: fn(),
  },
};
export default meta;
type Story = StoryObj<SettingsRecordView>;

export const Superuser: Story = {};
export const Guest: Story = { args: { groups: readOnly } };
export const Writing: Story = { args: { editDisabled: true } };
export const FailedRebuild: Story = {
  args: {
    line: {
      running: 6,
      since: '2026-11-20T22:14:00Z',
      pending: null,
      failed: { revision: 7, error: 'runner_prompt_file: no such file', since: '2026-11-20T22:20:00Z' },
    },
  },
};
export const SettingsError: Story = { args: { groups: [], settingsError: "throttle.budget.usd: expected a number, got 'lots'" } };
export const Loading: Story = { args: { state: 'loading', line: null, groups: [] } };
export const Unavailable: Story = { args: { state: 'error', line: null, groups: [] } };
export const Empty: Story = { args: { state: 'empty', groups: [] } };
export const Phone: Story = { globals: { viewport: { value: 'phone', isRotated: false } } };
export const PhoneSm: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
