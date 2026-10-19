import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitFactList } from './kit-fact-list';

const meta: Meta<KitFactList> = {
  title: 'Kit/Fact list',
  component: KitFactList,
  args: {
    rows: [
      { label: 'Status', value: 'running' },
      { label: 'Node', value: 'implement' },
      { label: 'Runner', value: 'r-claude' },
    ],
    testid: null,
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-fact-list [rows]="rows" [testid]="testid" />`,
  }),
};
export default meta;

type Story = StoryObj<KitFactList>;

export const Default: Story = {};
export const Empty: Story = { args: { rows: [] } };
export const Templated: Story = {
  render: (args) => ({
    props: args,
    template: `<ng-template #graph><a href="#graph">build-feature</a> <em>(edited)</em></ng-template>
<fleet-kit-fact-list [rows]="[{ label: 'Status', value: 'running' }, { label: 'Graph', template: graph }]" [testid]="testid" />`,
  }),
};
export const LongValue: Story = {
  args: {
    rows: [
      { label: 'Status', value: 'awaiting a human answer' },
      {
        label: 'Escalation reason',
        value:
          'The plan could not decide between two migration orders, and both would leave the board readable while the other is half-applied',
      },
      {
        label: 'Branch',
        value:
          'feat/a-very-long-branch-name-that-has-no-natural-break-point-anywhere-in-it',
      },
    ],
  },
};
