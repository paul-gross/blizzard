import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import type { HostRecordVm } from './host-records.model';
import { HostRecordsView } from './host-records-view';

const verbs = { edit: true, retire: true, enable: false };

const records: readonly HostRecordVm[] = [
  {
    key: 'ws-blizzard',
    state: { label: 'active', tone: 'running' },
    retired: false,
    settings: [{ key: 'root', value: '/home/paul/projects/blizzard-workspace' }],
    verbs,
  },
  { key: 'ws-web', state: { label: 'active', tone: 'running' }, retired: false, settings: [], verbs },
  {
    key: 'ws-legacy-monorepo-with-a-long-name',
    state: { label: 'retired', tone: 'idle' },
    retired: true,
    settings: [],
    verbs: { edit: true, retire: false, enable: true },
  },
];

const meta: Meta<HostRecordsView> = {
  title: 'Runner/Host records',
  component: HostRecordsView,
  args: {
    kind: 'workspace',
    title: 'Workspaces',
    state: 'ready',
    records,
    openKey: null,
    canCreate: true,
    createDisabled: false,
    create: fn(),
    openToggled: fn(),
  },
};
export default meta;
type Story = StoryObj<HostRecordsView>;

export const Superuser: Story = {};
export const Guest: Story = { args: { canCreate: false } };
export const Creating: Story = { args: { createDisabled: true } };
export const Open: Story = { args: { openKey: 'ws-blizzard' } };
export const Subscriptions: Story = {
  args: {
    kind: 'subscription',
    title: 'Subscriptions',
    records: [{ key: 'claude-max', state: { label: 'active', tone: 'running' }, retired: false, settings: [], verbs }],
  },
};
export const Loading: Story = { args: { state: 'loading', records: [] } };
export const Unavailable: Story = { args: { state: 'error', records: [] } };
export const Empty: Story = { args: { state: 'empty', records: [] } };
export const Phone: Story = { globals: { viewport: { value: 'phone', isRotated: false } } };
export const PhoneSm: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
