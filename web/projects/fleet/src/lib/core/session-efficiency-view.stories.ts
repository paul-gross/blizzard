import type { Meta, StoryObj } from '@storybook/angular-vite';

import { SessionEfficiency } from './session-efficiency-view';

const meta: Meta<SessionEfficiency> = {
  title: 'Core/Session efficiency',
  component: SessionEfficiency,
  args: {
    usage: {
      cost_usd: 0.3,
      estimated_cost_usd: null,
      cost_partial: false,
      input_tokens: 20_000,
      output_tokens: 32_700,
      cache_read_tokens: 8_340_000,
      cache_create_tokens: 200_000,
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
  args: { usage: { cost_usd: 0, estimated_cost_usd: null, cost_partial: false, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0 } },
};
/** An estimated, partial cost over millions of tokens. */
export const EstimatedPartial: Story = {
  args: { usage: { cost_usd: 1.2, estimated_cost_usd: 3.4, cost_partial: true, input_tokens: 1_234_567, output_tokens: 456_000, cache_read_tokens: 45_600_000, cache_create_tokens: 0 } },
};
/** In a narrow box the figure wraps between its parts, never inside one. */
export const Narrow: Story = {
  render: (args) => ({ props: args, template: `<div style="width: 9em"><fleet-session-efficiency [usage]="usage" /></div>` }),
};
