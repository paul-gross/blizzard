import type { Meta, StoryObj } from '@storybook/angular-vite';

import { SubscriptionBlocks } from './subscription-blocks';
import { paceLine, type SubscriptionLine } from './subscription-blocks.model';

const PACED: SubscriptionLine = {
  slug: 'claude-max',
  name: 'Claude Max',
  state: 'paced',
  windows: [paceLine('5h', 62, 70), paceLine('7d', 48, 30)],
  stale: false,
  age: '[5m]',
};

const meta: Meta<SubscriptionBlocks> = {
  title: 'Host/Subscription blocks',
  component: SubscriptionBlocks,
  args: { line: PACED },
  render: (args) => ({
    props: args,
    template: `<div style="width: 280px"><fleet-subscription-blocks [line]="line" /></div>`,
  }),
};
export default meta;

type Story = StoryObj<SubscriptionBlocks>;

/** Two windows, the weekly one running ahead of its clock in red. */
export const Paced: Story = {};
/** A window fully spent, its clock notched past the last block. */
export const Spent: Story = { args: { line: { ...PACED, windows: [paceLine('5h', 100, 100)] } } };
export const Stale: Story = { args: { line: { ...PACED, stale: true, age: '[2h]' } } };
export const Lapsed: Story = { args: { line: { ...PACED, state: 'lapsed', windows: [], age: null } } };
/** Sampled, but the newest reading carried no windows — its condition stands in for the blocks. */
export const Note: Story = {
  args: { line: { ...PACED, state: 'unsampled', windows: [], age: null, note: 'miss: endpoint unreachable' } },
};
export const NeverSampled: Story = { args: { line: { ...PACED, state: 'unsampled', windows: [], age: null } } };
export const LongName: Story = { args: { line: { ...PACED, name: 'Claude Max — team seat on the graduated xps workstation' } } };
