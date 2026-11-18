import { JsonPipe } from '@angular/common';
import { Component, input, signal } from '@angular/core';
import { FormField, disabled, form } from '@angular/forms/signals';
import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitCheckbox } from './kit-checkbox';

/** Form-bound stories render through this host: `form()` needs an injection context; the readout shows the field. */
@Component({
  selector: 'fleet-story-checkbox-form',
  imports: [FormField, KitCheckbox, JsonPipe],
  templateUrl: './kit-checkbox.form-story.html',
})
class CheckboxFormHost {
  readonly locked = input(false);
  readonly f = form(signal(false), (p) => disabled(p, () => this.locked()));
}

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
    label: 'Include stopped chunks, and every chunk whose graph has since been retired from the project',
  },
};

const formBound = (locked: boolean): Story => ({
  decorators: [moduleMetadata({ imports: [CheckboxFormHost] })],
  render: () => ({ template: `<fleet-story-checkbox-form [locked]="${locked}" />` }),
});
/** Bound to a field: edit it and the readout follows; leave it and touched flips. */
export const FormBound: Story = formBound(false);
/** A schema `disabled()` rule on the field disables the native control. */
export const FormBoundDisabledByRule: Story = formBound(true);
