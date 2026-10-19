import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitSkeleton } from './kit-skeleton';

const meta: Meta<KitSkeleton> = {
  title: 'Kit/Skeleton',
  component: KitSkeleton,
  args: { rows: 3, variant: 'line' },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-skeleton [rows]="rows" [variant]="variant" />`,
  }),
};
export default meta;

type Story = StoryObj<KitSkeleton>;

export const Lines: Story = {};
export const Cards: Story = { args: { variant: 'card' } };
export const SingleRow: Story = { args: { rows: 1 } };
export const ManyRows: Story = { args: { rows: 12 } };
export const NoRows: Story = { args: { rows: 0 } };
