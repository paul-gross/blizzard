import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitBadge } from './kit-badge';

const meta: Meta<KitBadge> = {
  title: 'Kit/Badge',
  component: KitBadge,
  args: {
    tone: 'running',
    variant: 'text',
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-badge [tone]="tone" [variant]="variant">Running</fleet-kit-badge>`,
  }),
};
export default meta;

type Story = StoryObj<KitBadge>;

export const Default: Story = {};
export const Pill: Story = { args: { variant: 'pill' } };
export const Soft: Story = { args: { variant: 'soft' } };
export const Needs: Story = { args: { tone: 'needs' } };
export const Waiting: Story = { args: { tone: 'waiting' } };
export const Takeover: Story = { args: { tone: 'takeover' } };
export const Spawning: Story = { args: { tone: 'spawning' } };
export const Stale: Story = { args: { tone: 'stale' } };
export const Done: Story = { args: { tone: 'done' } };
export const Idle: Story = { args: { tone: 'idle' } };
export const SoftNeeds: Story = { args: { tone: 'needs', variant: 'soft' } };
export const PillDone: Story = { args: { tone: 'done', variant: 'pill' } };
export const LongLabel: Story = {
  args: { variant: 'soft' },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-badge [tone]="tone" [variant]="variant">Awaiting a human answer on the plan</fleet-kit-badge>`,
  }),
};
