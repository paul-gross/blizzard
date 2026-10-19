import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitColorInput } from './kit-color-input';

const meta: Meta<KitColorInput> = {
  title: 'Kit/ColorInput',
  component: KitColorInput,
  args: {
    value: '#14b8a6',
    ariaLabel: 'Custom color, as #rrggbb',
    testid: null,
    valueChange: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-color-input [value]="value" [ariaLabel]="ariaLabel" [testid]="testid" (valueChange)="valueChange($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<KitColorInput>;

export const Seeded: Story = {};
export const Empty: Story = { args: { value: '' } };
