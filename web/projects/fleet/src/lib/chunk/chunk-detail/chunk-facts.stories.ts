import type { Meta, StoryObj } from '@storybook/angular-vite';

import { ChunkFacts } from './chunk-facts';
import { factsDetail, factsNeighbor } from './chunk-facts.fixture';

const meta: Meta<ChunkFacts> = {
  title: 'Chunk/Facts',
  component: ChunkFacts,
  args: {
    detail: factsDetail(),
    chunkLinkBase: ['/board', 'chunk'],
  },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-detail-facts [detail]="detail" [chunkLinkBase]="chunkLinkBase" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkFacts>;

export const EveryFact: Story = {
  args: {
    detail: factsDetail({
      neighborhood: {
        prerequisites: [factsNeighbor()],
        dependents: [factsNeighbor({ chunk_id: 'ch_01dependent0000000000000000', laneStatus: { kind: 'not_ready' } })],
      },
    }),
  },
};
export const AlmostNone: Story = {
  args: { detail: factsDetail() },
};
export const NeighborsBlockingAndBlocked: Story = {
  args: {
    detail: factsDetail({
      neighborhood: {
        prerequisites: [
          factsNeighbor({ chunk_id: 'ch_01prereqdone000000000000000', laneStatus: { kind: 'done' } }),
          factsNeighbor({ chunk_id: 'ch_01prereqrunning00000000000', laneStatus: { kind: 'running' } }),
        ],
        dependents: [factsNeighbor({ chunk_id: 'ch_01dependent0000000000000000', laneStatus: { kind: 'not_ready' } })],
      },
    }),
  },
};
/** A step-free chunk — the efficiency figure reads its cost alone. */
export const NoUsage: Story = {
  args: { detail: factsDetail({ cost: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0, cost_usd: 0, cost_partial: false, estimated_cost_usd: null } }) },
};
