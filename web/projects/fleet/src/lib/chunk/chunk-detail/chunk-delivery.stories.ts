import type { Meta, StoryObj } from '@storybook/angular-vite';

import { chunkDetail, landedChunkDetail, landedRepoView, prView } from 'fleet/testing';
import { ChunkDelivery } from './chunk-delivery';

const meta: Meta<ChunkDelivery> = {
  title: 'Chunk/Delivery',
  component: ChunkDelivery,
  args: { detail: chunkDetail({ status: 'delivering' }) },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-delivery [detail]="detail" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkDelivery>;

export const BeforeAnyPullRequest: Story = {};
export const OnePullRequestOpen: Story = {
  args: { detail: chunkDetail({ status: 'delivering', open_prs: [prView()] }) },
};
export const SeveralRepositoriesLanded: Story = {
  args: {
    detail: landedChunkDetail({
      landed_repos: [
        landedRepoView(),
        landedRepoView({
          repo: 'paul-gross/blizzard-context',
          commit_hash: '1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d',
          url: 'https://github.com/paul-gross/blizzard-context/commit/1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d',
        }),
      ],
    }),
  },
};
export const LandingFailed: Story = {
  args: {
    detail: chunkDetail({
      status: 'needs_human',
      closed_prs: [prView({ number: 1299, url: 'https://github.com/paul-gross/blizzard/pull/1299' })],
      landed_repos: [],
    }),
  },
};
export const ParkedAwaitingMerge: Story = {
  args: {
    detail: chunkDetail({
      status: 'delivering',
      open_prs: [prView()],
      pending: { node_name: 'park', next_poll_at: '2026-10-30T12:05:00Z', kind: 'awaiting-merge', since: '2026-10-30T12:00:00Z' },
    }),
  },
};
export const ParkedAwaitingMergeWithoutStart: Story = {
  args: {
    detail: chunkDetail({
      status: 'delivering',
      open_prs: [prView()],
      pending: { node_name: 'park', next_poll_at: '2026-10-30T12:05:00Z', kind: 'awaiting-merge', since: null },
    }),
  },
};
