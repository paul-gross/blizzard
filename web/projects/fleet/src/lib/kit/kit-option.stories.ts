import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitOption } from './kit-option';

const meta: Meta<KitOption> = {
  title: 'Kit/Option',
  component: KitOption,
  args: {
    name: 'scope',
    checked: true,
    disabled: false,
    alignTop: false,
    testid: null,
    changed: fn(),
  },
  render: (args) => ({
    props: args,
    template: `
      <div style="display: flex; flex-direction: column; gap: 6px">
        <fleet-kit-option [name]="name" [checked]="checked" [disabled]="disabled" [alignTop]="alignTop" [testid]="testid" (changed)="changed()">Project</fleet-kit-option>
        <fleet-kit-option [name]="name" [checked]="false" (changed)="changed()">Workspace</fleet-kit-option>
        <fleet-kit-option [name]="name" [checked]="false" (changed)="changed()">Tenant</fleet-kit-option>
      </div>`,
  }),
};
export default meta;

type Story = StoryObj<KitOption>;

export const Checked: Story = {};
export const Unchecked: Story = { args: { checked: false } };
export const Disabled: Story = { args: { disabled: true } };
export const AlignTopWithDescription: Story = {
  args: { alignTop: true },
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-option [name]="name" [checked]="checked" [disabled]="disabled" [alignTop]="alignTop" [testid]="testid" (changed)="changed()">
        <span>
          <strong>workspace</strong><br />
          Every project in the workspace shares this setting, so a change here reaches runners that are
          mid-flight on chunks in other projects as well.
        </span>
      </fleet-kit-option>`,
  }),
};
export const LongLabel: Story = {
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-option [name]="name" [checked]="checked" [disabled]="disabled" [alignTop]="alignTop" [testid]="testid" (changed)="changed()">
        Every project in the workspace, including those whose runners have not reported a heartbeat since the last redeploy
      </fleet-kit-option>`,
  }),
};
