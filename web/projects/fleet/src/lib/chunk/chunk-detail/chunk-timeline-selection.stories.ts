import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import {
  bouncedChunkDetail,
  escalatedChunkDetail,
  landedChunkDetail,
  midFlightChunkDetail,
  stoppedChunkDetail,
} from 'fleet/testing';
import { ChunkTimelineSelection } from './chunk-timeline-selection';

const meta: Meta<ChunkTimelineSelection> = {
  title: 'Chunk/Timeline selection',
  component: ChunkTimelineSelection,
  args: {
    detail: midFlightChunkDetail(),
    selectedKey: 'nd_plan:2',
    graphLinkBase: null,
    pickStep: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-detail-timeline-selection [detail]="detail" [selectedKey]="selectedKey" [graphLinkBase]="graphLinkBase" (pickStep)="pickStep($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkTimelineSelection>;

export const SelectedStep: Story = {};
export const NoSelection: Story = { args: { selectedKey: null } };
export const SelectedAfterBounce: Story = { args: { detail: bouncedChunkDetail(), selectedKey: 'nd_review:2' } };
export const Escalated: Story = { args: { detail: escalatedChunkDetail() } };
export const Landed: Story = { args: { detail: landedChunkDetail() } };
export const Stopped: Story = { args: { detail: stoppedChunkDetail() } };
