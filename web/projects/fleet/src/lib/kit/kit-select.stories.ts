import { JsonPipe } from '@angular/common';
import { Component, input, signal } from '@angular/core';
import { FormField, disabled, form } from '@angular/forms/signals';
import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
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

/** Form-bound stories render through this host: `form()` needs an injection context; the readout shows the field. */
@Component({
  selector: 'fleet-story-select-form',
  imports: [FormField, KitSelect, JsonPipe],
  templateUrl: './kit-select.form-story.html',
})
class SelectFormHost {
  readonly options = [
    { value: 'a', label: 'Alpha' },
    { value: 'b', label: 'Beta' },
    { value: 'c', label: 'Gamma' },
  ];
  readonly locked = input(false);
  readonly f = form(signal('b'), (p) => disabled(p, () => this.locked()));
}

const meta: Meta<KitSelect> = {
  title: 'Kit/Select',
  component: KitSelect,
  args: {
    options: OPTIONS,
    selectedValue: 'gardening',
    disabled: false,
    wrapOptions: false,
    ariaLabel: 'Routine',
    label: null,
    testid: null,
    choose: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-select [options]="options" [selectedValue]="selectedValue" [disabled]="disabled" [wrapOptions]="wrapOptions" [ariaLabel]="ariaLabel" [label]="label" [testid]="testid" (choose)="choose($event)" />`,
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
        <fleet-kit-select [options]="options" [selectedValue]="selectedValue" [disabled]="disabled" [wrapOptions]="wrapOptions" [ariaLabel]="ariaLabel" [label]="label" [testid]="testid" (choose)="choose($event)" />
      </div>`,
  }),
};
/** Open the popup to see long labels wrap within the viewport. */
export const WrappedOptions: Story = {
  args: {
    wrapOptions: true,
    options: [
      { value: 'short', label: 'Short option' },
      { value: 'long', label: 'A routine with a long name that needs to remain readable in the popup at narrow widths' },
      { value: 'unbroken', label: 'an-extremely-long-unbroken-option-name-that-must-also-remain-readable-within-the-viewport' },
    ],
    selectedValue: 'long',
  },
};
export const WrappedOptionsPhone: Story = {
  ...WrappedOptions,
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const WrappedOptionsPhoneSmall: Story = {
  ...WrappedOptions,
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
/** Open the list by hand: forty options scroll inside the popup. */
export const ManyOptions: Story = {
  args: { options: MANY, selectedValue: 'routine-7' },
};

const formBound = (locked: boolean): Story => ({
  decorators: [moduleMetadata({ imports: [SelectFormHost] })],
  render: () => ({ template: `<fleet-story-select-form [locked]="${locked}" />` }),
});
/** Bound to a field: edit it and the readout follows; leave it and touched flips. */
export const FormBound: Story = formBound(false);
/** A schema `disabled()` rule on the field disables the native control. */
export const FormBoundDisabledByRule: Story = formBound(true);
