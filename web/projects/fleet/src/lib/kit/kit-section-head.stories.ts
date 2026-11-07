import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitSectionHead } from './kit-section-head';

const meta: Meta<KitSectionHead> = {
  title: 'Kit/Section heading',
  component: KitSectionHead,
  args: { label: 'Awaiting human', labelId: null, count: null },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-section-head [label]="label" [labelId]="labelId" [count]="count" />`,
  }),
};
export default meta;

type Story = StoryObj<KitSectionHead>;

export const Plain: Story = {};
export const WithCount: Story = { args: { label: 'Messages', count: 4 } };
export const WithTrailingContent: Story = {
  args: { label: 'Node history', labelId: 'node-history-heading' },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-section-head [label]="label" [labelId]="labelId"><span style="color: var(--label-dim)">·</span><span style="color: var(--amber); font-size: var(--fs-xs)">build-and-verify</span></fleet-kit-section-head>`,
  }),
};
export const LongLabel: Story = {
  args: { label: 'Needs human · takeover of a session that stopped answering its runner' },
};
