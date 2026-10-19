import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitCheckbox } from './kit-checkbox';

const meta: Meta<KitCheckbox> = {
  title: 'Kit/Checkbox',
  component: KitCheckbox,
  args: {
    label: 'Include stopped chunks',
    checked: false,
    disabled: false,
    ariaLabel: null,
    testid: null,
    checkedChange: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-checkbox [label]="label" [checked]="checked" [disabled]="disabled" [ariaLabel]="ariaLabel" [testid]="testid" (checkedChange)="checkedChange($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<KitCheckbox>;

export const Unchecked: Story = {};
export const Checked: Story = { args: { checked: true } };
export const Disabled: Story = { args: { disabled: true } };
export const LongLabel: Story = {
  args: {
    label:
      'Include stopped chunks, and every chunk whose graph has since been retired from the project',
  },
};
