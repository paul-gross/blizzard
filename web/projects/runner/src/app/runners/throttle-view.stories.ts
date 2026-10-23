import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { runnerApi } from 'fleet';
import { fn } from 'storybook/test';

import { ThrottleView } from './throttle-view';

const open: runnerApi.ThrottleStatusView = {
  state: 'admitting', declining: [],
  floor: { subscription: 'anthropic', admits: true, hold: null, clear: null },
  levers: [{ name: 'pace', setting: '7d, lead 5 points', reading: '40% spent, 50% elapsed', admits: true, until: null }],
  on_decline: 'hold', on_usage_limit: 'release',
};

const held: runnerApi.ThrottleStatusView = {
  ...open, state: 'declining', declining: ['floor'],
  floor: { ...open.floor, admits: false, hold: {
    kind: 'usage limit', harness_id: 'claude_code', window: '5h', expected_reset: '2026-10-22T14:00:00Z',
    awaits_sample: true, grace_opens_at: '2026-10-22T15:00:00Z',
  } },
};

const pacing: runnerApi.ThrottleStatusView = {
  ...open, state: 'declining', declining: ['pace'],
  levers: [{
    name: 'pace', setting: '7d, lead 5 points', reading: '7d: 86% spent, 71% elapsed, lead 5',
    admits: false, until: '2026-10-24T08:00:00Z',
  }],
};

const meta: Meta<ThrottleView> = {
  title: 'Runner/Throttle',
  component: ThrottleView,
  args: { throttle: open, state: 'ready', scope: 'r-claude and r-chatgpt', scopeReady: true, pending: false, error: null, clearFloor: fn() },
};
export default meta;

type Story = StoryObj<ThrottleView>;

export const Open: Story = {};
export const Held: Story = { args: { throttle: held } };
export const LeverDeclining: Story = { args: { throttle: pacing } };
export const Cleared: Story = { args: { throttle: {
  ...open, floor: { ...open.floor, clear: { by: 'paul', cleared_at: '2026-10-22T09:14:00Z' } },
} } };
export const Braked: Story = { args: { throttle: { ...held, state: 'braked' } } };
export const EmptyLevers: Story = { args: { throttle: { ...open, floor: { ...open.floor, subscription: null }, levers: [] }, scope: 'r-claude' } };
export const Phone: Story = { args: Held.args, globals: { viewport: { value: 'phone', isRotated: false } } };
