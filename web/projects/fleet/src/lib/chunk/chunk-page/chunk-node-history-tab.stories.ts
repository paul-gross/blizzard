import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { nodeStepKey } from '../../core/node-step';
import {
  domainChunkDetail as chunkDetail,
  domainTransition as transitionView,
} from '../../testing/builders/chunk';
import { assetChunkArtifact } from '../../testing/builders/artifact';
import type { TranscriptTurn } from '../../transcripts/transcript-turn';
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
  stepArtifacts: [assetChunkArtifact()],
  segmentData: {
    segment_id: 'sg_01fixture0000000000000000000', final: true, truncated: false,
    turns: [
      { index: 0, kind: 'asst', text: 'I will start by reading the work item.', timestamp: null,
        thinking_redacted: false, truncated: false, tool: null, sidechain: null },
      {
        index: 1,
        kind: 'tool',
        text: 'Checking the working tree.',
        timestamp: null, thinking_redacted: false, truncated: false, sidechain: null,
        tool: { name: 'Bash', tool_use_id: 'toolu_01fixture', input: { command: 'git status --short' }, input_shape: 'object',
          input_unparsed: null, output: ' M web/projects/fleet/src/lib/testing/public-api.ts', output_truncated: false },
      },
    ] satisfies TranscriptTurn[],
  },
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
