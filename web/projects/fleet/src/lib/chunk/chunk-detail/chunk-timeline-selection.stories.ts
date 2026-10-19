import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import {
  bouncedChunkDetail,
  escalatedChunkDetail,
  fixtureInstant,
  landedChunkDetail,
  midFlightChunkDetail,
  stoppedChunkDetail,
  transitionView,
} from 'fleet/testing';
import { ChunkTimelineSelection } from './chunk-timeline-selection';

// One finished step, so `nd_build:1` names a row for the selection to land on.
const walked = [
  transitionView({ recorded_at: fixtureInstant(-600) }),
  transitionView({
    from_node_id: 'nd_build',
    from_node_name: 'build',
    to_node_id: 'nd_review',
    to_node_name: 'review',
    choice_name: 'pass',
    recorded_at: fixtureInstant(-60),
  }),
];

const meta: Meta<ChunkTimelineSelection> = {
  title: 'Chunk/Timeline selection',
  component: ChunkTimelineSelection,
  args: {
    detail: midFlightChunkDetail(),
    selectedKey: 'nd_plan:2',
    graphLinkBase: null,
    stepSessions: new Map(),
    pickStep: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-detail-timeline-selection [detail]="detail" [selectedKey]="selectedKey" [graphLinkBase]="graphLinkBase" [stepSessions]="stepSessions" (pickStep)="pickStep($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkTimelineSelection>;

export const SelectedStep: Story = {};
export const NoSelection: Story = { args: { selectedKey: null } };
export const SelectedAfterBounce: Story = { args: { detail: bouncedChunkDetail(), selectedKey: 'nd_review:2' } };
export const Escalated: Story = { args: { detail: escalatedChunkDetail({ history: walked }), selectedKey: 'nd_build:1' } };
export const Landed: Story = { args: { detail: landedChunkDetail(), selectedKey: 'nd_build:1' } };
export const Stopped: Story = { args: { detail: stoppedChunkDetail({ history: walked }), selectedKey: 'nd_build:1' } };
/** A daemon that records its sessions adds each known step's environment, pid, ending, and duration. */
export const WithStepSessions: Story = {
  args: {
    detail: stoppedChunkDetail({ history: walked }),
    selectedKey: null,
    stepSessions: new Map([
      [
        'nd_build:1',
        { environment: 'env-2', pid: 48211, startedAt: fixtureInstant(-900), endedAt: fixtureInstant(-600), ending: 'finished' },
      ],
    ]),
  },
};
