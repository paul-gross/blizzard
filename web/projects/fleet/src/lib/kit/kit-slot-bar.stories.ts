import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitSlotBar } from './kit-slot-bar';

const meta: Meta<KitSlotBar> = {
  title: 'Kit/Slot bar',
  component: KitSlotBar,
  args: { used: 2, total: 4, unit: 'slots', name: null },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-slot-bar [used]="used" [total]="total" [unit]="unit" [name]="name" />`,
  }),
};
export default meta;

type Story = StoryObj<KitSlotBar>;

export const Default: Story = {};
export const Idle: Story = { args: { used: 0 } };
export const Full: Story = { args: { used: 4 } };
export const OverCapacity: Story = { args: { used: 6 } };
export const NoCapacity: Story = { args: { used: 0, total: 0 } };
export const CustomUnit: Story = {
  args: { used: 3, total: 8, unit: 'workers' },
};
export const Named: Story = { args: { name: 'r-claude' } };
export const NamedOverCapacity: Story = {
  args: { name: 'r-chatgpt', used: 5 },
};
export const ManyCells: Story = { args: { used: 17, total: 32 } };
export const LongName: Story = {
  args: { name: 'r-claude-long-running-graduated-host-on-xps-workstation' },
};
