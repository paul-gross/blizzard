import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { nodeStepKey } from '../../core/node-step';
import {
  chunkDetail,
  transitionView,
} from '../../testing/builders/chunk';
import { assetArtifactView } from '../../testing/builders/artifact';
import { toolCallSegmentView, transcriptSegmentContentView, turnSegmentView } from '../../testing/builders/transcript';
import { ChunkNodeHistoryTab } from './chunk-node-history-tab';

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

const SELECTED = nodeStepKey('nd_plan', 2);

const meta: Meta<ChunkNodeHistoryTab> = {
  title: 'Chunk/Node history tab',
  component: ChunkNodeHistoryTab,
  args: {
    detail: chunkDetail({ history: HISTORY }),
    selectedKey: SELECTED,
    drilldown: false,
    stepArtifacts: [],
    indexState: 'ready',
    isForbidden: false,
    segmentState: 'ready',
    segmentData: undefined,
    continuedFrom: null,
    continuesIn: null,
    graphLinkBase: null,
    pickStep: fn(),
    pickSegment: fn(),
  },
};
export default meta;

type Story = StoryObj<ChunkNodeHistoryTab>;

const FULL_ARGS: Story['args'] = {
  stepArtifacts: [assetArtifactView()],
  segmentData: transcriptSegmentContentView({
    turns: [
      turnSegmentView({ index: 0 }),
      turnSegmentView({
        index: 1,
        kind: 'tool',
        text: 'Checking the working tree.',
        tool: toolCallSegmentView(),
      }),
    ],
  }),
};

export const Loading: Story = { args: { indexState: 'loading', segmentState: 'loading' } };
export const Forbidden: Story = { args: { isForbidden: true, indexState: 'empty', segmentState: 'empty' } };
export const Error: Story = { args: { indexState: 'error', segmentState: 'error' } };
export const Empty: Story = { args: { indexState: 'empty', segmentState: 'empty', stepArtifacts: [] } };
export const Full: Story = { args: FULL_ARGS };
export const NoSelection: Story = { args: { selectedKey: null } };
export const Narrow: Story = {
  args: { ...FULL_ARGS, drilldown: true },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
