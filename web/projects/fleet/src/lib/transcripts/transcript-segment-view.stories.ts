import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { toolCallSegmentView, transcriptSegmentIndexEntry, turnSegmentView } from './transcript-fixtures';
import { TranscriptSegmentView } from './transcript-segment-view';

const meta: Meta<TranscriptSegmentView> = {
  title: 'Transcripts/Segment view',
  component: TranscriptSegmentView,
  args: {
    turns: [
      turnSegmentView({ index: 0 }),
      turnSegmentView({ index: 1, kind: 'tool', text: 'Checking the working tree.', tool: toolCallSegmentView() }),
    ],
    truncated: false,
    continuedFrom: null,
    continuesIn: null,
    harnessId: 'claude-code',
    pickSegment: fn(),
    openStandalone: fn(),
  },
};
export default meta;

type Story = StoryObj<TranscriptSegmentView>;

export const Truncated: Story = { args: { truncated: true } };
export const ContinuedFrom: Story = {
  args: { continuedFrom: transcriptSegmentIndexEntry({ segment_id: 'sg_01previous0000000000000000', spawn_generation: 1 }) },
};
export const ContinuesIn: Story = {
  args: {
    continuesIn: transcriptSegmentIndexEntry({ segment_id: 'sg_01following000000000000000', spawn_generation: 3 }),
  },
};

export const InteractiveContinuations: Story = {
  render: (args) => ({
    props: {
      ...args,
      segment: 0,
      entries: [
        transcriptSegmentIndexEntry({ segment_id: 'sg_first', spawn_generation: 1 }),
        transcriptSegmentIndexEntry({ segment_id: 'sg_second', spawn_generation: 2 }),
      ],
      segmentTurns: [
        [turnSegmentView({ index: 0, text: 'First segment.' })],
        [turnSegmentView({ index: 0, text: 'Second segment.' })],
      ],
    },
    template: `<fleet-transcript-segment-view [turns]="segmentTurns[segment]" [truncated]="truncated" [harnessId]="harnessId" [continuedFrom]="segment === 1 ? entries[0] : null" [continuesIn]="segment === 0 ? entries[1] : null" (pickSegment)="segment = $event === entries[0].segment_id ? 0 : 1; pickSegment($event)" (openStandalone)="openStandalone($event)" />`,
  }),
};
