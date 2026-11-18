import { JsonPipe } from '@angular/common';
import { Component, input, signal } from '@angular/core';
import { FormField, disabled, form } from '@angular/forms/signals';
import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { KitOption } from './kit-option';
import { KitOptionGroup } from './kit-option-group';

/** Form-bound stories render through this host: `form()` needs an injection context; the readout shows the field. */
@Component({
  selector: 'fleet-story-option-group-form',
  imports: [FormField, KitOptionGroup, KitOption, JsonPipe],
  templateUrl: './kit-option-group.form-story.html',
})
class OptionGroupFormHost {
  readonly locked = input(false);
  readonly f = form(signal('project'), (p) => disabled(p, () => this.locked()));
}

const meta: Meta<KitOptionGroup> = {
  title: 'Kit/Option group',
  component: KitOptionGroup,
  args: { ariaLabel: 'Scope', value: 'workspace', disabled: false },
  decorators: [moduleMetadata({ imports: [KitOption] })],
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-option-group [ariaLabel]="ariaLabel" [value]="value" [disabled]="disabled">
        <fleet-kit-option value="project">Project</fleet-kit-option>
        <fleet-kit-option value="workspace">Workspace</fleet-kit-option>
        <fleet-kit-option value="tenant">Tenant</fleet-kit-option>
      </fleet-kit-option-group>`,
  }),
};
export default meta;

type Story = StoryObj<KitOptionGroup>;

export const Default: Story = {};
export const DisabledGroup: Story = { args: { disabled: true } };
/** One option disabled on its own; the rest of the group stays operable. */
export const DisabledOption: Story = {
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-option-group ariaLabel="Scope" value="project">
        <fleet-kit-option value="project">Project</fleet-kit-option>
        <fleet-kit-option value="workspace" [disabled]="true">Workspace</fleet-kit-option>
        <fleet-kit-option value="tenant">Tenant</fleet-kit-option>
      </fleet-kit-option-group>`,
  }),
};

const formBound = (locked: boolean): Story => ({
  decorators: [moduleMetadata({ imports: [OptionGroupFormHost] })],
  render: () => ({ template: `<fleet-story-option-group-form [locked]="${locked}" />` }),
});
/** Bound to a field: arrow keys move the check and the readout; leaving the group flips touched. */
export const FormBound: Story = formBound(false);
/** A schema `disabled()` rule on the field disables every radio. */
export const FormBoundDisabledByRule: Story = formBound(true);
