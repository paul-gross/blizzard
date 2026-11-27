import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { KitSelectList } from './kit-select-list';
import { KitSelectRow } from './kit-select-row';

const meta: Meta<KitSelectRow> = {
  title: 'Kit/Select row',
  component: KitSelectRow,
  decorators: [moduleMetadata({ imports: [KitSelectList, KitSelectRow] })],
  args: {
    bare: false,
    ariaLabel: null,
    testid: null,
  },
  render: (args) => ({
    props: args,
    template: `
      <div fleetKitSelectList selectionMode="explicit" [softDisabled]="false" [wrap]="false" ariaLabel="Artifacts" value="review" style="width: 320px">
        <fleet-kit-select-row value="plan" label="plan.md" [bare]="bare" [ariaLabel]="ariaLabel" [testid]="testid">plan.md</fleet-kit-select-row>
        <fleet-kit-select-row value="review" label="review-findings.json">review-findings.json</fleet-kit-select-row>
        <fleet-kit-select-row value="retro" label="retrospective.md">retrospective.md</fleet-kit-select-row>
      </div>`,
  }),
};
export default meta;

type Story = StoryObj<KitSelectRow>;

export const Default: Story = {};
export const Bare: Story = { args: { bare: true } };
export const LongUnbrokenId: Story = {
  render: (args) => ({
    props: args,
    template: `
      <div fleetKitSelectList selectionMode="explicit" [softDisabled]="false" [wrap]="false" ariaLabel="Artifacts" style="width: 320px">
        <fleet-kit-select-row value="long" label="long id" [bare]="bare">ch_01M57RP7EGQTX461G740Z5WS6G_lease_01M58V8E0A3XPZMXMTPCKVB77F_artifact_reviewed_plan</fleet-kit-select-row>
      </div>`,
  }),
};
