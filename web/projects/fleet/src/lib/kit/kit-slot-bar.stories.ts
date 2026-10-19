import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitSlotBar } from './kit-slot-bar';

const meta: Meta<KitSlotBar> = {
  title: 'Kit/Slot bar',
  component: KitSlotBar,
  args: { used: 2, total: 4, unit: 'slots', name: null, slots: null },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-slot-bar [used]="used" [total]="total" [unit]="unit" [name]="name" [slots]="slots" />`,
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
/** Positional: which slot is held reads, not only how many — the second held by another, dimmed. */
export const PositionalSlots: Story = {
  args: {
    name: 'r-claude',
    slots: [
      { on: true, title: 'C-1RJ1' },
      { on: true, title: 'C-3YJ9 · r-chatgpt', dim: true },
      { on: false },
      { on: true, title: 'C-7Q2M' },
    ],
  },
};
/** Past ten cells the bar packs them closer. */
export const Dense: Story = { args: { name: 'r-claude', used: 7, total: 16 } };
/** A host's environments, as the runner panel draws one workspace: each environment in its own place, grey rather
 * than amber, dense past ten, held ones naming their chunk. */
export const EnvironmentStrip: Story = {
  args: {
    name: 'workspace-local',
    unit: 'envs',
    slots: Array.from({ length: 18 }, (_, index) => {
      const held = [1, 2, 3, 4, 7, 8, 9, 12, 13].includes(index);
      return held
        ? {
            on: true,
            title: `e${index + 1} · C-${(index * 7919).toString(36).toUpperCase().slice(-4)}`,
          }
        : { on: false, title: `e${index + 1} · free` };
    }),
  },
  render: (args) => ({
    props: args,
    styles: [
      ':host { --kit-slot-bar-on: var(--grey); --kit-slot-bar-on-border: var(--grey-dim); }',
    ],
    template: `<fleet-kit-slot-bar [used]="used" [total]="total" [unit]="unit" [name]="name" [slots]="slots" />`,
  }),
};
