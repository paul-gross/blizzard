import type { Meta, StoryObj } from '@storybook/angular-vite';

import { BoardHeader } from './board-header';
import { headerSpend } from './board-header.builders';

const meta: Meta<BoardHeader> = {
  title: 'Shell/Board header',
  component: BoardHeader,
  args: {
    connection: 'ok',
    connectionLabel: 'Hub',
    overridden: [],
    tagline: 'fleet hub · mission control',
    counts: { total: 0, terminal: 0, statuses: { not_ready: 0, ready: 0, running: 0, delivering: 0, waiting_on_human: 0, needs_human: 0, paused: 0, stopped: 0, done: 0 } },
    spendToday: headerSpend({ cost_usd: 27.5 }),
    spendYesterday: headerSpend({ cost_usd: 41.2 }),
    stats: null,
  },
};
export default meta;

type Story = StoryObj<BoardHeader>;

/** Before any read resolves: no lane cells, no spend cells. */
export const NoCounts: Story = {
  args: {
    counts: null,
    spendToday: null,
    spendYesterday: null,
    connection: '—',
  },
};
export const OrdinaryDay: Story = {};
/** Spend that runs to five figures and a partial cost, widening its column. */
export const SpendOutrunsItsColumn: Story = {
  args: {
    spendToday: headerSpend({ cost_usd: 12_345.67, cost_partial: true }),
    spendYesterday: headerSpend({ cost_usd: 98_765.43 }),
  },
};
export const OverriddenFlags: Story = {
  args: { overridden: ['fleet_flags', 'lens'] },
};
export const LongTagline: Story = {
  args: {
    tagline:
      'a tenant whose name runs well past the room the stat strip gives up',
  },
};
export const RunnerStats: Story = {
  args: {
    connectionLabel: 'Runner',
    counts: null,
    stats: [
      { key: 'envs', label: 'Envs', value: 2, capacity: 4 },
      { key: 'chunks', label: 'Chunks', value: 3 },
    ],
  },
};
