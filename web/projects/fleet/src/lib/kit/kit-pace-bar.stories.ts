import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitPaceBar } from './kit-pace-bar';

const meta: Meta<KitPaceBar> = {
  title: 'Kit/Pace bar',
  component: KitPaceBar,
  args: { label: '5h', utilizationPct: 32, elapsedPct: 55 },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-pace-bar [label]="label" [utilizationPct]="utilizationPct" [elapsedPct]="elapsedPct" />`,
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
