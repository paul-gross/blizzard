import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import type { KitChipOption } from './kit-chips';
import { KitSelect } from './kit-select';

const OPTIONS: readonly KitChipOption[] = [
  { value: 'all', label: 'All routines' },
  { value: 'gardening', label: 'Gardening' },
  { value: 'release', label: 'Release' },
  { value: 'retro', label: 'Retrospective analysis' },
];

const MANY: readonly KitChipOption[] = Array.from({ length: 40 }, (_, i) => ({
  value: `routine-${i}`,
  label: `Routine ${i + 1}`,
}));

const meta: Meta<KitSelect> = {
  title: 'Kit/Select',
  component: KitSelect,
  args: {
    options: OPTIONS,
    selectedValue: 'gardening',
    disabled: false,
    ariaLabel: 'Routine',
    label: null,
    testid: null,
    choose: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-select [options]="options" [selectedValue]="selectedValue" [disabled]="disabled" [ariaLabel]="ariaLabel" [label]="label" [testid]="testid" (choose)="choose($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<KitSelect>;

export const WithSelection: Story = {};
export const WithLabelPrefix: Story = { args: { label: 'Routine' } };
export const NoSelection: Story = { args: { selectedValue: null } };
export const Disabled: Story = { args: { disabled: true } };
export const LongValueTruncating: Story = {
  args: {
    options: [
      ...OPTIONS,
      {
        value: 'long',
        label:
          'A routine whose name runs well past the width the trigger is given here',
      },
    ],
    selectedValue: 'long',
  },
  render: (args) => ({
    props: args,
    template: `
      <div style="width: 180px">
        <fleet-kit-select [options]="options" [selectedValue]="selectedValue" [disabled]="disabled" [ariaLabel]="ariaLabel" [label]="label" [testid]="testid" (choose)="choose($event)" />
      </div>`,
  }),
};
/** Open the list by hand: forty options scroll inside the popup. */
export const ManyOptions: Story = {
  args: { options: MANY, selectedValue: 'routine-7' },
};
