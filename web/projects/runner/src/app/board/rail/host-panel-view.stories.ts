import type { Meta, StoryObj } from '@storybook/angular-vite';

import { HostPanelView } from './host-panel-view';

const meta: Meta<HostPanelView> = {
  title: 'Runner/Host panel',
  component: HostPanelView,
  args: {
    subscriptions: [{ slug: 'claude', name: 'Claude', state: 'unsampled', windows: [], stale: false, age: null }],
    subscriptionsState: 'ready',
    runners: [{ key: 'r1', runnerId: 'r1', name: 'r-claude', tone: 'green', halted: false, haltedReasons: [], agentsInUse: 1, maxAgents: 2, bindings: [], localPause: null, hubPaused: false }],
    pools: [{ workspaceId: 'workspace', capacity: 2, inUse: 1, envs: [], slots: [{ on: true, title: 'held' }, { on: false, title: 'free' }] }],
    poolsState: 'ready',
  },
};
export default meta;
type Story = StoryObj<HostPanelView>;
export const Loaded: Story = {};
export const Loading: Story = { args: { subscriptionsState: 'loading', poolsState: 'loading' } };
export const Error: Story = { args: { subscriptionsState: 'error', poolsState: 'error' } };
export const Empty: Story = { args: { subscriptionsState: 'empty', poolsState: 'empty' } };
export const Narrow: Story = { args: Error.args, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
