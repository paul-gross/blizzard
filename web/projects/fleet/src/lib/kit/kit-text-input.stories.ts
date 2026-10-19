import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitTextInput } from './kit-text-input';

const meta: Meta<KitTextInput> = {
  title: 'Kit/Text input',
  component: KitTextInput,
  args: {
    value: '',
    placeholder: 'Answer the question',
    ariaLabel: 'Answer',
    testid: null,
    multiline: false,
    rows: 3,
    type: 'text',
    valueChange: fn(),
  },
  argTypes: {
    type: { control: 'inline-radio', options: ['text', 'password'] },
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-text-input [value]="value" [placeholder]="placeholder" [ariaLabel]="ariaLabel" [testid]="testid" [multiline]="multiline" [rows]="rows" [type]="type" (valueChange)="valueChange($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<KitTextInput>;

export const EmptyWithPlaceholder: Story = {};
export const Filled: Story = { args: { value: 'Use the hosted hub' } };
export const Password: Story = {
  args: { value: 'hunter2-hunter2', type: 'password' },
};
export const Multiline: Story = {
  args: {
    multiline: true,
    value: 'Sweep the kit for\nparts without a stories file.',
  },
};
export const LongValue: Story = {
  args: {
    value:
      'Use the hosted hub for the runner, but keep the board pointed at the local mock until the migration for the new lease columns has been applied everywhere',
  },
};
