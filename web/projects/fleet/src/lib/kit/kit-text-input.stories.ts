import { JsonPipe } from '@angular/common';
import { Component, input, signal } from '@angular/core';
import { FormField, disabled, form } from '@angular/forms/signals';
import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitTextInput } from './kit-text-input';

/** Form-bound stories render through this host: `form()` needs an injection context; the readout shows the field. */
@Component({
  selector: 'fleet-story-text-input-form',
  imports: [FormField, KitTextInput, JsonPipe],
  templateUrl: './kit-text-input.form-story.html',
})
class TextInputFormHost {
  readonly locked = input(false);
  readonly f = form(signal('draft'), (p) => disabled(p, () => this.locked()));
}

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
    type: { control: 'inline-radio', options: ['text', 'password', 'number'] },
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
export const Numeric: Story = {
  args: { value: '7890123', type: 'number', placeholder: 'Installation id', ariaLabel: 'Installation id' },
};
export const Multiline: Story = {
  args: {
    multiline: true,
    value: 'Sweep the kit for\nparts without a stories file.',
  },
};
export const MultilinePassword: Story = {
  args: {
    multiline: true,
    type: 'password',
    rows: 4,
    value: '-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo\n-----END RSA PRIVATE KEY-----',
  },
};
export const MultilinePasswordPhone: Story = {
  ...MultilinePassword,
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const MultilinePasswordPhoneSm: Story = {
  ...MultilinePassword,
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
export const LongValue: Story = {
  args: {
    value:
      'Use the hosted hub for the runner, but keep the board pointed at the local mock until the migration for the new lease columns has been applied everywhere',
  },
};

const formBound = (locked: boolean): Story => ({
  decorators: [moduleMetadata({ imports: [TextInputFormHost] })],
  render: () => ({ template: `<fleet-story-text-input-form [locked]="${locked}" />` }),
});
/** Bound to a field: edit it and the readout follows; leave it and touched flips. */
export const FormBound: Story = formBound(false);
/** A schema `disabled()` rule on the field disables the native control. */
export const FormBoundDisabledByRule: Story = formBound(true);
