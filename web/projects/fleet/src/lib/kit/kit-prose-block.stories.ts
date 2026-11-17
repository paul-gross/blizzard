import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitProseBlock } from './kit-prose-block';

const meta: Meta<KitProseBlock> = {
  title: 'Kit/Prose block',
  component: KitProseBlock,
  args: {
    kind: 'output',
    label: 'Worker',
    text: 'Implemented the slot gauge and added unit coverage for the overflow case.',
    testid: null,
    format: 'plain',
  },
  argTypes: {
    format: { control: 'inline-radio', options: ['plain', 'markdown'] },
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-prose-block [kind]="kind" [label]="label" [text]="text" [testid]="testid" [format]="format" />`,
  }),
};
export default meta;

type Story = StoryObj<KitProseBlock>;

export const Output: Story = {};
export const Context: Story = {
  args: {
    kind: 'context',
    label: 'Operator',
    text: 'Keep the gauge compact on the runner registry.',
  },
};
export const Unlabelled: Story = { args: { label: null } };
export const EmptyText: Story = { args: { text: '' } };
export const LongText: Story = {
  args: {
    text: 'The runner registry now reads its rate-limit windows from the harness account, paces each window against its own reset clock, and surfaces a stacked gauge per window so an operator can see at a glance whether a runner is about to exhaust its subscription before the window turns over. A runner whose utilization runs ahead of its elapsed share is flagged; one that lags is left alone. Supercalifragilisticexpialidociousunbrokentokenthatnevercontainsaspace.',
  },
};
export const MultilineText: Story = {
  args: {
    text: 'First line of output.\nSecond line, after a newline.\n\nA paragraph after a blank line.',
  },
};
export const Markdown: Story = {
  args: {
    format: 'markdown',
    label: 'Summary',
    text: '## Slot gauge\n\nThe runner registry now paces each rate-limit window against its own **reset clock**.\n\nA second paragraph names `src/runner/gauge.ts` and the follow-ups:\n\n- flag a runner running ahead of its elapsed share\n- leave a lagging runner alone',
  },
};
