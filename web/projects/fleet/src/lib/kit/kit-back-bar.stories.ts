import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitBackBar } from './kit-back-bar';

const meta: Meta<KitBackBar> = {
  title: 'Kit/Back bar',
  component: KitBackBar,
  args: { label: 'Board' },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-back-bar [label]="label" />`,
  }),
};
export default meta;

type Story = StoryObj<KitBackBar>;

export const Default: Story = {};
export const LongLabel: Story = {
  args: {
    label:
      'Back to the chunk that this escalation was raised against, in the blizzard project',
  },
};
