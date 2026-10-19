import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitChip } from './kit-chips';

const meta: Meta<KitChip> = {
  title: 'Kit/Chip',
  component: KitChip,
  args: {
    selected: false,
    action: false,
    disabled: false,
    title: null,
    testid: null,
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-chip [selected]="selected" [action]="action" [disabled]="disabled" [title]="title" [testid]="testid">Blocked</fleet-kit-chip>`,
  }),
};
export default meta;

type Story = StoryObj<KitChip>;

export const Default: Story = {};
export const Selected: Story = { args: { selected: true } };
export const Action: Story = { args: { action: true } };
export const Disabled: Story = { args: { disabled: true } };
export const WithCount: Story = {
  render: (args) => ({
    props: args,
    template: `<fleet-kit-chip [selected]="selected" [action]="action" [disabled]="disabled" [title]="title" [testid]="testid">Blocked <span>12</span></fleet-kit-chip>`,
  }),
};
export const LongLabel: Story = {
  render: (args) => ({
    props: args,
    template: `<fleet-kit-chip [selected]="selected" [action]="action" [disabled]="disabled" [title]="title" [testid]="testid">Chunks awaiting a human answer on a question the plan could not decide</fleet-kit-chip>`,
  }),
};
