import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { transitionView } from '../testing/builders/chunk';
import { toolCallSegmentView, transcriptSegmentContentView, transcriptSegmentIndexEntry, turnSegmentView } from '../testing/builders/transcript';
import { ChunkTranscriptsTab } from './chunk-transcripts-tab';

const HISTORY = [
  transitionView({ from_node_id: null, to_node_id: 'nd_plan', to_node_name: 'plan', epoch: 1 }),
  transitionView({
    from_node_id: 'nd_plan',
    from_node_name: 'plan',
    to_node_id: 'nd_build',
    to_node_name: 'build',
    choice_name: 'approve',
    epoch: 2,
  }),
];

const SEGMENTS = [
  transcriptSegmentIndexEntry({ segment_id: 'sg_01plan00000000000000000000', node_id: 'nd_plan', epoch: 1 }),
  transcriptSegmentIndexEntry({ segment_id: 'sg_01build0000000000000000000', node_id: 'nd_build', epoch: 2, final: false }),
];

const meta: Meta<ChunkTranscriptsTab> = {
  title: 'Transcripts/Transcripts tab',
  component: ChunkTranscriptsTab,
  args: {
    history: HISTORY,
    currentNodeId: 'nd_build',
    currentNodeName: 'build',
    latestEpoch: 2,
    currentNodeTerminal: false,
    segments: SEGMENTS,
    indexState: 'ready',
    isForbidden: false,
    segmentId: null,
    sidechainPath: null,
    drilldown: false,
    segmentState: 'empty',
    segmentData: undefined,
    pickSegment: fn(),
    pickSidechain: fn(),
  },
};
export default meta;

type Story = StoryObj<ChunkTranscriptsTab>;

const FULL_ARGS: Story['args'] = {
  segmentId: 'sg_01build0000000000000000000',
  segmentState: 'ready',
  segmentData: transcriptSegmentContentView({
    segment_id: 'sg_01build0000000000000000000',
    turns: [
      turnSegmentView({ index: 0 }),
      turnSegmentView({ index: 1, kind: 'tool', text: 'Checking the working tree.', tool: toolCallSegmentView() }),
    ],
  }),
};

export const Loading: Story = { args: { indexState: 'loading', segments: [] } };
export const Forbidden: Story = { args: { isForbidden: true, indexState: 'empty', segments: [] } };
export const Empty: Story = { args: { indexState: 'empty', segments: [] } };
export const Full: Story = { args: FULL_ARGS };
export const Narrow: Story = {
  args: { ...FULL_ARGS, drilldown: true },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
