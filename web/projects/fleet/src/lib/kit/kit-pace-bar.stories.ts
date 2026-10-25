import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitPaceBar } from './kit-pace-bar';

const meta: Meta<KitPaceBar> = {
  title: 'Kit/Pace bar',
  component: KitPaceBar,
  args: { label: '5h', utilizationPct: 32, elapsedPct: 55, variant: 'bars', count: 10, groupEvery: 0 },
  render: (args) => ({
    props: args,
    template: `<div style="width: 280px"><fleet-kit-pace-bar [label]="label" [utilizationPct]="utilizationPct" [elapsedPct]="elapsedPct" [variant]="variant" [count]="count" [groupEvery]="groupEvery" /></div>`,
  }),
};
export default meta;

type Story = StoryObj<KitPaceBar>;

export const Default: Story = {};
export const AheadOfPace: Story = {
  args: { utilizationPct: 81, elapsedPct: 40 },
};
export const WeeklyWindow: Story = {
  args: { label: '7d', utilizationPct: 47, elapsedPct: 62 },
};
export const Empty: Story = { args: { utilizationPct: 0, elapsedPct: 0 } };
export const Exhausted: Story = {
  args: { utilizationPct: 100, elapsedPct: 73 },
};
export const OutOfRangeClamped: Story = {
  args: { utilizationPct: 140, elapsedPct: -20 },
};
export const FractionalUtilization: Story = {
  args: { utilizationPct: 66.6, elapsedPct: 50 },
};

/** The compact block gauge: usage filled, the window's clock a notch. */
export const Blocks: Story = { args: { variant: 'blocks' } };
/** Usage past the clock turns the blocks red. */
export const BlocksAheadOfPace: Story = { args: { variant: 'blocks', utilizationPct: 81, elapsedPct: 40 } };
export const BlocksEmpty: Story = { args: { variant: 'blocks', utilizationPct: 0, elapsedPct: 0 } };
/** A window fully spent, its clock notched past the last block. */
export const BlocksSpent: Story = { args: { variant: 'blocks', utilizationPct: 100, elapsedPct: 100 } };
export const BlocksOutOfRangeClamped: Story = { args: { variant: 'blocks', utilizationPct: 140, elapsedPct: -20 } };
/** Forty blocks in groups of ten, the gaps marking the window's quarters — a subscription's pace line. */
export const BlocksGrouped: Story = { args: { variant: 'blocks', count: 40, groupEvery: 10, utilizationPct: 62, elapsedPct: 70 } };
