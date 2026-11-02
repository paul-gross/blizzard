import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { runnerApi } from 'fleet';
import { fn } from 'storybook/test';

import type { SelftestRow } from './selftest.model';
import { SelftestView } from './selftest-view';

const CHECKS: runnerApi.SelfTestCheckView[] = [
  { name: 'spawn', passed: true, detail: 'spawned in the scratch repo' },
  { name: 'edit and commit', passed: true, detail: 'committed 1 file' },
  { name: 'verdict', passed: true, detail: 'verdict done' },
  { name: 'resume', passed: true, detail: 'resumed the session' },
];

const row = (overrides: Partial<SelftestRow> = {}): SelftestRow => ({
  harness: 'claude_code', available: true, run: null, busy: false, error: null, ...overrides,
});
const run = (status: string, overrides: Partial<runnerApi.SelfTestView> = {}): runnerApi.SelfTestView => ({
  id: 'st_1', harness: 'claude_code', status, checks: [], error: null, ...overrides,
});

const meta: Meta<SelftestView> = {
  title: 'Runner/Selftest',
  component: SelftestView,
  args: { rows: [row(), row({ harness: 'codex' })], state: 'ready', runSelftest: fn() },
};
export default meta;

type Story = StoryObj<SelftestView>;

export const Idle: Story = {};
export const Running: Story = { args: { rows: [row({ run: run('running'), busy: true }), row({ harness: 'codex' })] } };
export const Passed: Story = { args: { rows: [row({ run: run('passed', { checks: CHECKS }) })] } };
export const Failed: Story = { args: { rows: [row({ run: run('failed', {
  checks: [...CHECKS.slice(0, 3), { name: 'resume', passed: false, detail: 'no session to resume' }],
  error: 'the resume check failed',
}) })] } };
export const StartError: Story = { args: { rows: [row({ error: 'harness claude_code is unavailable' })] } };
export const NoHarness: Story = { args: { rows: [], state: 'empty' } };
export const Phone: Story = { args: Failed.args, globals: { viewport: { value: 'phone', isRotated: false } } };
