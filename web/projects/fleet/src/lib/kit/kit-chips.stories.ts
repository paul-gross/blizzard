import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitChips, type KitChipOption } from './kit-chips';

const OPTIONS: readonly KitChipOption[] = [
  { value: 'all', label: 'All' },
  { value: 'running', label: 'Running' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'landed', label: 'Landed' },
];

const MANY: readonly KitChipOption[] = Array.from({ length: 24 }, (_, i) => ({
  value: `routine-${i}`,
  label: `Routine ${i + 1}`,
}));

const meta: Meta<KitChips> = {
  title: 'Kit/Chips',
  component: KitChips,
  args: {
    options: OPTIONS,
    selectedValue: 'running',
    action: false,
    disabled: false,
    choose: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-chips [options]="options" [selectedValue]="selectedValue" [action]="action" [disabled]="disabled" (choose)="choose($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<KitChips>;

export const Selection: Story = {};
export const NoneSelected: Story = { args: { selectedValue: null } };
export const ActionRow: Story = { args: { action: true, selectedValue: null } };
export const RowDisabled: Story = { args: { disabled: true } };
export const OneOptionDisabled: Story = {
  args: {
    options: OPTIONS.map((o) =>
      o.value === 'landed' ? { ...o, disabled: true } : o,
    ),
  },
};
export const ManyWrapping: Story = {
  args: { options: MANY, selectedValue: 'routine-3' },
};
export const NarrowPhone: Story = {
  args: { options: MANY },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const NarrowPhoneSmall: Story = {
  args: { options: MANY },
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
