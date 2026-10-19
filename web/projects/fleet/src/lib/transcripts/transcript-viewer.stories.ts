import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { sidechainSegmentView, toolCallSegmentView, turnSegmentView } from '../testing/builders/transcript';
import { TranscriptViewer } from './transcript-viewer';

const meta: Meta<TranscriptViewer> = {
  title: 'Transcripts/Viewer',
  component: TranscriptViewer,
  args: {
    turns: [
      turnSegmentView({ index: 0, kind: 'env', text: 'Read the work item and plan the change.' }),
      turnSegmentView({ index: 1 }),
    ],
    openStandalone: fn(),
  },
};
export default meta;

type Story = StoryObj<TranscriptViewer>;

export const Short: Story = {};
export const Long: Story = {
  args: {
    turns: Array.from({ length: 60 }, (_, index) =>
      turnSegmentView({
        index,
        kind: index % 2 === 0 ? 'thinking' : 'asst',
        text: `Turn ${index}: a line of conversation long enough to wrap when the viewer is narrow.`,
      }),
    ),
  },
};
export const ToolCalls: Story = {
  args: {
    turns: [
      turnSegmentView({ index: 0, text: 'Checking the working tree.', tool: toolCallSegmentView() }),
      turnSegmentView({
        index: 1,
        text: 'Reading the header.',
        tool: toolCallSegmentView({
          name: 'Read',
          tool_use_id: 'toolu_02fixture',
          input: { file_path: 'chunk-page-header.ts' },
          output: 'export class ChunkPageHeader {}',
        }),
      }),
      turnSegmentView({
        index: 2,
        text: 'Running the suite.',
        tool: toolCallSegmentView({
          tool_use_id: 'toolu_03fixture',
          input: { command: 'npm run test' },
          output: 'Tests: 1 failed',
          output_truncated: true,
        }),
      }),
    ],
  },
};
export const Sidechain: Story = {
  args: {
    turns: [
      turnSegmentView({
        index: 0,
        text: 'Handing the search to a subagent.',
        tool: toolCallSegmentView({ name: 'Task', input: { description: 'Find the builders' } }),
        sidechain: sidechainSegmentView({
          turns: [
            turnSegmentView({ index: 0, kind: 'env', text: 'Find the builders.' }),
            turnSegmentView({ index: 1, text: 'They live under fleet/testing.', tool: toolCallSegmentView() }),
          ],
        }),
      }),
    ],
  },
};
