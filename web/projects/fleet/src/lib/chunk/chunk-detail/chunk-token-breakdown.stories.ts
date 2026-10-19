import type { Meta, StoryObj } from '@storybook/angular-vite';

import { chunkDetail, chunkUsageTotalView } from 'fleet/testing';
import { ChunkTokenBreakdown } from './chunk-token-breakdown';

const meta: Meta<ChunkTokenBreakdown> = {
  title: 'Chunk/Token breakdown',
  component: ChunkTokenBreakdown,
  args: { detail: chunkDetail({ cost: chunkUsageTotalView() }) },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-detail-token-breakdown [detail]="detail" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkTokenBreakdown>;

export const Inexpensive: Story = {
  args: {
    detail: chunkDetail({
      cost: chunkUsageTotalView({
        input_tokens: 1_200,
        output_tokens: 600,
        cache_read_tokens: 8_000,
        cache_create_tokens: 2_000,
        cost_usd: 0.04,
      }),
    }),
  },
};
export const Expensive: Story = {
  args: {
    detail: chunkDetail({
      cost: chunkUsageTotalView({
        input_tokens: 4_800_000,
        output_tokens: 1_900_000,
        cache_read_tokens: 310_000_000,
        cache_create_tokens: 22_000_000,
        cost_usd: 482.7,
      }),
    }),
  },
};
export const UsageStillArriving: Story = {
  args: { detail: chunkDetail({ cost: chunkUsageTotalView({ cost_usd: 3.42, cost_partial: true }) }) },
};
