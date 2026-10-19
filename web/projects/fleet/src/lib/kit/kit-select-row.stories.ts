import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitSelectRow } from './kit-select-row';

const meta: Meta<KitSelectRow> = {
  title: 'Kit/Select row',
  component: KitSelectRow,
  args: {
    selected: false,
    bare: false,
    ariaLabel: null,
    testid: null,
    picked: fn(),
  },
  render: (args) => ({
    props: args,
    template: `
      <div style="width: 320px">
        <fleet-kit-select-row [selected]="selected" [bare]="bare" [ariaLabel]="ariaLabel" [testid]="testid" (picked)="picked()">plan.md</fleet-kit-select-row>
        <fleet-kit-select-row (picked)="picked()">review-findings.json</fleet-kit-select-row>
        <fleet-kit-select-row (picked)="picked()">retrospective.md</fleet-kit-select-row>
      </div>`,
  }),
};
export default meta;

type Story = StoryObj<KitSelectRow>;

export const Default: Story = {};
export const Selected: Story = { args: { selected: true } };
export const Bare: Story = { args: { bare: true } };
export const LongUnbrokenId: Story = {
  render: (args) => ({
    props: args,
    template: `
      <div style="width: 320px">
        <fleet-kit-select-row [selected]="selected" [bare]="bare" [ariaLabel]="ariaLabel" [testid]="testid" (picked)="picked()">ch_01M57RP7EGQTX461G740Z5WS6G_lease_01M58V8E0A3XPZMXMTPCKVB77F_artifact_reviewed_plan</fleet-kit-select-row>
      </div>`,
  }),
};
