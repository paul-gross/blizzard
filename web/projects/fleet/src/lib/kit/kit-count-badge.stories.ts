import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitCountBadge } from './kit-count-badge';

const meta: Meta<KitCountBadge> = {
  title: 'Kit/Count badge',
  component: KitCountBadge,
  args: { count: 3, testid: null },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-count-badge [count]="count" [testid]="testid" />`,
  }),
};
export default meta;

type Story = StoryObj<KitCountBadge>;

export const Default: Story = {};
export const Zero: Story = { args: { count: 0 } };
export const TwoDigits: Story = { args: { count: 12 } };
export const Large: Story = { args: { count: 12345 } };
