import type { Meta, StoryObj } from '@storybook/angular-vite';

import { SessionEfficiency } from './session-efficiency-view';

const meta: Meta<SessionEfficiency> = {
  title: 'Core/Session efficiency',
  component: SessionEfficiency,
  args: {
    usage: {
      costUsd: 0.3,
      estimatedCostUsd: null,
      costPartial: false,
      inputTokens: 20_000,
      outputTokens: 32_700,
      cacheReadTokens: 8_340_000,
      cacheCreateTokens: 200_000,
    },
  },
  render: (args) => ({ props: args, template: `<fleet-session-efficiency [usage]="usage" />` }),
};
export default meta;

type Story = StoryObj<SessionEfficiency>;

/** Cost, distinct input, output and cache miss; the spelled-out breakdown is the hover title. */
export const Typical: Story = {};
/** A step with no tokens reads its cost alone. */
export const NoTokens: Story = {
  args: { usage: { costUsd: 0, estimatedCostUsd: null, costPartial: false, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheCreateTokens: 0 } },
};
/** An estimated, partial cost over millions of tokens. */
export const EstimatedPartial: Story = {
  args: { usage: { costUsd: 1.2, estimatedCostUsd: 3.4, costPartial: true, inputTokens: 1_234_567, outputTokens: 456_000, cacheReadTokens: 45_600_000, cacheCreateTokens: 0 } },
};
/** In a narrow box the figure wraps between its parts, never inside one. */
export const Narrow: Story = {
  render: (args) => ({ props: args, template: `<div style="width: 9em"><fleet-session-efficiency [usage]="usage" /></div>` }),
};
