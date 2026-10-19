import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitBeacon } from './kit-beacon';

const meta: Meta<KitBeacon> = {
  title: 'Kit/Beacon',
  component: KitBeacon,
  args: {
    active: false,
    tone: 'amber',
    blink: 'pulse',
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-beacon [active]="active" [tone]="tone" [blink]="blink" />`,
  }),
};
export default meta;

type Story = StoryObj<KitBeacon>;

export const Inactive: Story = {};
export const Amber: Story = { args: { active: true } };
export const Red: Story = { args: { active: true, tone: 'red' } };
export const Green: Story = { args: { active: true, tone: 'green' } };
export const Step: Story = { args: { active: true, blink: 'step' } };
export const Hold: Story = {
  args: { active: true, tone: 'green', blink: 'hold' },
};
