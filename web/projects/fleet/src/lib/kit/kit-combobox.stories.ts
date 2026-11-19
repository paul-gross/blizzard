import { JsonPipe } from '@angular/common';
import { Component, input, signal } from '@angular/core';
import { FormField, disabled, form } from '@angular/forms/signals';
import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { zoneOptions } from '../core/zones';
import { KitCombobox } from './kit-combobox';

const ZONES = zoneOptions(Date.UTC(2026, 0, 15, 12));

/** Form-bound stories render through this host: `form()` needs an injection context; the readout shows the field. */
@Component({
  selector: 'fleet-story-combobox-form',
  imports: [FormField, KitCombobox, JsonPipe],
  templateUrl: './kit-combobox.form-story.html',
})
class ComboboxFormHost {
  readonly options = ZONES;
  readonly locked = input(false);
  readonly f = form(signal('America/Chicago'), (p) => disabled(p, () => this.locked()));
}

const meta: Meta<KitCombobox> = {
  title: 'Kit/Combobox',
  component: KitCombobox,
  args: { options: ZONES, ariaLabel: 'Zone', noneLabel: null, testid: null },
  render: (args) => ({
    props: args,
    template: `<div style="width: 280px"><fleet-kit-combobox [options]="options" [ariaLabel]="ariaLabel" [noneLabel]="noneLabel" [testid]="testid" /></div>`,
  }),
};
export default meta;

type Story = StoryObj<KitCombobox>;

/** Type to filter by substring; Up and Down move the highlight, Enter picks, Escape closes. */
export const Empty: Story = {};
export const WithNoneOption: Story = { args: { noneLabel: 'None' } };
export const FewOptions: Story = {
  args: {
    options: [
      { value: 'UTC', label: 'UTC', meta: 'GMT' },
      { value: 'Europe/Paris', label: 'Europe/Paris', meta: 'GMT+1' },
    ],
  },
};

const formBound = (locked: boolean): Story => ({
  decorators: [moduleMetadata({ imports: [ComboboxFormHost] })],
  render: () => ({ template: `<div style="width: 280px"><fleet-story-combobox-form [locked]="${locked}" /></div>` }),
});
/** Bound to a field: pick a zone and the readout follows; leave the box and touched flips. */
export const FormBound: Story = formBound(false);
/** A schema `disabled()` rule on the field disables the box. */
export const FormBoundDisabledByRule: Story = formBound(true);
