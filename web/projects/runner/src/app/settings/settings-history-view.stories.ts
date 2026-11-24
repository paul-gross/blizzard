import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import type { SettingsChangeVm } from './settings.model';
import { SettingsHistoryView } from './settings-history-view';

const changes: readonly SettingsChangeVm[] = [
  {
    id: 9,
    revision: 7,
    op: 'edit',
    door: 'page',
    actor: 'paul',
    at: '2026-11-20T22:13:00Z',
    record: null,
    diff: [{ key: 'throttle.budget.usd', old: '10', new: '25' }],
  },
  {
    id: 8,
    revision: 4,
    op: 'create',
    door: 'cli',
    actor: 'paul',
    at: '2026-11-19T09:02:00Z',
    record: 'workspace ws-blizzard',
    diff: [
      { key: 'root', old: '—', new: '/home/paul/projects/blizzard-workspace' },
      { key: 'max_environments', old: '—', new: '8 (default)' },
    ],
  },
  { id: 1, revision: 1, op: 'seed', door: 'seed', actor: 'seed', at: '2026-11-01T00:00:00Z', record: null, diff: [] },
];

const meta: Meta<SettingsHistoryView> = {
  title: 'Runner/Settings history',
  component: SettingsHistoryView,
  args: { title: 'Host', recordId: 'host', state: 'ready', changes, hasNext: true, loadingNext: false, loadMore: fn() },
};
export default meta;
type Story = StoryObj<SettingsHistoryView>;

export const Changes: Story = {};
export const Oldest: Story = { args: { hasNext: false } };
export const LoadingMore: Story = { args: { loadingNext: true } };
export const Loading: Story = { args: { state: 'loading', changes: [] } };
export const Empty: Story = { args: { state: 'empty', changes: [], hasNext: false } };
export const Phone: Story = { globals: { viewport: { value: 'phone', isRotated: false } } };
export const PhoneSm: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
