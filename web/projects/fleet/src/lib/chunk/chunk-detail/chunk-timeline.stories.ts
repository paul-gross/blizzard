import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import {
  bouncedChunkDetail,
  chunkDetail,
  escalatedChunkDetail,
  fixtureInstant,
  landedChunkDetail,
  midFlightChunkDetail,
  queuedChunkDetail,
  stoppedChunkDetail,
  transitionView,
} from 'fleet/testing';
import { ChunkTimeline } from './chunk-timeline';

const longHistory = chunkDetail({
  latest_epoch: 30,
  history: Array.from({ length: 30 }, (_, i) =>
    transitionView({
      from_node_id: i === 0 ? null : i % 2 ? 'nd_build' : 'nd_review',
      from_node_name: i === 0 ? null : i % 2 ? 'build' : 'review',
      to_node_id: i % 2 ? 'nd_review' : 'nd_build',
      to_node_name: i % 2 ? 'review' : 'build',
      choice_name: i === 0 ? null : i % 2 ? 'pass' : 'fail',
      epoch: i + 1,
      recorded_at: fixtureInstant(-3600 + i * 60),
    }),
  ),
});

const meta: Meta<ChunkTimeline> = {
  title: 'Chunk/Timeline',
  component: ChunkTimeline,
  args: {
    detail: midFlightChunkDetail(),
    heading: true,
    activatable: false,
    selectedKey: null,
    pickStep: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-detail-timeline [detail]="detail" [heading]="heading" [activatable]="activatable" [selectedKey]="selectedKey" (pickStep)="pickStep($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkTimeline>;

export const Queued: Story = { args: { detail: queuedChunkDetail() } };
export const MidFlightThirdEpoch: Story = {};
export const BouncedBackByReview: Story = { args: { detail: bouncedChunkDetail() } };
export const Escalated: Story = { args: { detail: escalatedChunkDetail() } };
export const Landed: Story = { args: { detail: landedChunkDetail() } };
export const Stopped: Story = { args: { detail: stoppedChunkDetail() } };
export const WithSelectedStep: Story = { args: { activatable: true, selectedKey: 'nd_plan:2' } };
export const WithoutSelectedStep: Story = { args: { activatable: true, selectedKey: null } };
export const Activatable: Story = { args: { activatable: true } };
export const NotActivatable: Story = { args: { activatable: false } };
export const LongHistory: Story = {
  args: { detail: longHistory },
  render: (args) => ({
    props: args,
    template: `<div style="max-height: 320px; overflow: auto"><fleet-chunk-detail-timeline [detail]="detail" [heading]="heading" [activatable]="activatable" [selectedKey]="selectedKey" (pickStep)="pickStep($event)" /></div>`,
  }),
};
export const Narrow: Story = {
  globals: { viewport: { value: 'phone', isRotated: false } },
  args: { detail: bouncedChunkDetail(), activatable: true },
};
