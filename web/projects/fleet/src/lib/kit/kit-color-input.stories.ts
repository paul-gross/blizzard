import { JsonPipe } from '@angular/common';
import { Component, input, signal } from '@angular/core';
import { FormField, disabled, form } from '@angular/forms/signals';
import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitColorInput } from './kit-color-input';

/** Form-bound stories render through this host: `form()` needs an injection context; the readout shows the field. */
@Component({
  selector: 'fleet-story-color-input-form',
  imports: [FormField, KitColorInput, JsonPipe],
  templateUrl: './kit-color-input.form-story.html',
})
class ColorInputFormHost {
  readonly locked = input(false);
  readonly f = form(signal('#3366cc'), (p) => disabled(p, () => this.locked()));
}

const meta: Meta<KitColorInput> = {
  title: 'Kit/ColorInput',
  component: KitColorInput,
  args: {
    value: '#14b8a6',
    ariaLabel: 'Custom color, as #rrggbb',
    testid: null,
    disabled: false,
    valueChange: fn(),
    touch: fn(),
    invalidChange: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-color-input [value]="value" [ariaLabel]="ariaLabel" [testid]="testid" [disabled]="disabled" (valueChange)="valueChange($event)" (touch)="touch()" (invalidChange)="invalidChange($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<KitColorInput>;

export const Seeded: Story = {};
export const Empty: Story = { args: { value: '' } };
export const Disabled: Story = { args: { disabled: true } };

const formBound = (locked: boolean): Story => ({
  decorators: [moduleMetadata({ imports: [ColorInputFormHost] })],
  render: () => ({ template: `<fleet-story-color-input-form [locked]="${locked}" />` }),
});
/** Bound to a field: edit it and the readout follows; leave it and touched flips. */
export const FormBound: Story = formBound(false);
/** A schema `disabled()` rule on the field disables the native control. */
export const FormBoundDisabledByRule: Story = formBound(true);
