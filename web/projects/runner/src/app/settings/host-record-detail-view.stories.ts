import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import type { HostRecordVm } from './host-records.model';
import { HostRecordDetailView } from './host-record-detail-view';

const record: HostRecordVm = {
  key: 'web',
  state: { label: 'active', tone: 'running' },
  retired: false,
  settings: [
    { key: 'base_branch', value: 'master' },
    { key: 'envs', value: '["web-1","web-2"]' },
    { key: 'max_environments', value: '2' },
    {
      key: 'repo',
      value: '[{"name":"blizzard","url":"https://github.com/example/blizzard.git"},{"name":"context","url":"https://github.com/example/context.git"}]',
    },
    { key: 'root', value: '/srv/web' },
  ],
  verbs: { edit: true, retire: true, enable: false },
};

const meta: Meta<HostRecordDetailView> = {
  title: 'Runner/Host record detail',
  component: HostRecordDetailView,
  args: {
    prefix: 'records-workspace-web',
    record,
    editDisabled: false,
    retireDisabled: false,
    enableDisabled: false,
    verbError: null,
    edit: fn(),
    retire: fn(),
    enable: fn(),
  },
};
export default meta;
type Story = StoryObj<HostRecordDetailView>;

export const Superuser: Story = {};
export const Guest: Story = { args: { record: { ...record, verbs: { edit: false, retire: false, enable: false } } } };
export const Retired: Story = {
  args: { record: { ...record, state: { label: 'retired', tone: 'idle' }, retired: true, verbs: { edit: true, retire: false, enable: true } } },
};
export const Retiring: Story = { args: { retireDisabled: true } };
export const Refused: Story = { args: { verbError: 'workspace web holds 2 environments; release them before retiring it' } };
export const NoKeys: Story = { args: { record: { ...record, settings: [] } } };
export const Phone: Story = { globals: { viewport: { value: 'phone', isRotated: false } } };
export const PhoneSm: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
