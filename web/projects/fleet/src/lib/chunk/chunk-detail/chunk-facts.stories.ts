import type { Meta, StoryObj } from '@storybook/angular-vite';

import {
  chunkDetail,
  chunkNeighborhoodView,
  chunkNeighborView,
  chunkUsageTotalView,
  fixtureInstant,
  routeView,
  workRefView,
} from 'fleet/testing';
import { ChunkFacts } from './chunk-facts';

const meta: Meta<ChunkFacts> = {
  title: 'Chunk/Facts',
  component: ChunkFacts,
  args: {
    detail: chunkDetail({
      project_name: 'Blizzard',
      project_slug: 'blizzard',
      graph_name: 'build',
      graph_created_at: fixtureInstant(-86_400),
      route: routeView(),
      cost: chunkUsageTotalView(),
      work_refs: [workRefView(), workRefView({ ref: '1275', label: 'blizzard#1275' })],
    }),
    graphLinkBase: null,
    chunkLinkBase: ['/board', 'chunk'],
  },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-detail-facts [detail]="detail" [graphLinkBase]="graphLinkBase" [chunkLinkBase]="chunkLinkBase" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkFacts>;

export const EveryFact: Story = {
  args: {
    detail: chunkDetail({
      project_name: 'Blizzard',
      project_slug: 'blizzard',
      graph_name: 'build',
      graph_created_at: fixtureInstant(-86_400),
      route: routeView(),
      cost: chunkUsageTotalView(),
      work_refs: [workRefView(), workRefView({ ref: '1275', label: 'blizzard#1275' })],
      neighborhood: chunkNeighborhoodView({
        prerequisites: [chunkNeighborView()],
        dependents: [chunkNeighborView({ chunk_id: 'ch_01dependent0000000000000000', satisfied: false, status: 'not_ready' })],
      }),
    }),
  },
};
export const AlmostNone: Story = {
  args: { detail: chunkDetail({ work_refs: [], route: null, graph_name: null, history: [], latest_epoch: null }) },
};
export const NeighborsBlockingAndBlocked: Story = {
  args: {
    detail: chunkDetail({
      status: 'not_ready',
      neighborhood: chunkNeighborhoodView({
        prerequisites: [
          chunkNeighborView({ chunk_id: 'ch_01prereqdone000000000000000', satisfied: true, status: 'done' }),
          chunkNeighborView({ chunk_id: 'ch_01prereqrunning00000000000', satisfied: false, status: 'running' }),
        ],
        dependents: [chunkNeighborView({ chunk_id: 'ch_01dependent0000000000000000', satisfied: false, status: 'not_ready' })],
      }),
    }),
  },
};
/** The graph read-only, linked to its graph page. */
export const GraphLinked: Story = { args: { graphLinkBase: ['/graphs'] } };
