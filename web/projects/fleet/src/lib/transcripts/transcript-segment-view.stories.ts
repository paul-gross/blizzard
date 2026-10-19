import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { toolCallSegmentView, transcriptSegmentIndexEntry, turnSegmentView } from '../testing/builders/transcript';
import { TranscriptSegmentView } from './transcript-segment-view';

const meta: Meta<TranscriptSegmentView> = {
  title: 'Transcripts/Segment view',
  component: TranscriptSegmentView,
  args: {
    turns: [
      turnSegmentView({ index: 0 }),
      turnSegmentView({ index: 1, text: 'Checking the working tree.', tool: toolCallSegmentView() }),
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
