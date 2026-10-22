import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { chunkDetail, decisionView, questionView, transitionView, workItemEntry } from '../../testing/builders/chunk';
import { ChunkGeneralTab } from './chunk-general-tab';

const HISTORY = [
  transitionView({ from_node_id: null, to_node_id: 'nd_plan', to_node_name: 'plan', epoch: 1, recorded_at: '2026-10-19T11:40:00.000Z' }),
  transitionView({
    from_node_id: 'nd_plan',
    from_node_name: 'plan',
    to_node_id: 'nd_build',
    to_node_name: 'build',
    choice_name: 'approve',
    epoch: 2,
  }),
];

const meta: Meta<ChunkGeneralTab> = {
  title: 'Chunk/General tab',
  component: ChunkGeneralTab,
  args: {
    detail: chunkDetail({ history: HISTORY }),
    workItems: { status: 'success', items: [workItemEntry()] },
    canAnswer: false,
    canResolve: false,
    resolvePending: false,
    pendingAnswerQuestionIds: [],
    graphLinkBase: null,
    eventsLinkBase: null,
    issuePanePlacement: 'center',
    showNodeHistory: true,
    answerQuestion: fn(),
    resolveDecision: fn(),
    pickStep: fn(),
  },
};
export default meta;

type Story = StoryObj<ChunkGeneralTab>;

export const Loading: Story = { args: { workItems: { status: 'loading', items: [] } } };
export const Forbidden: Story = { args: { workItems: { status: 'error', items: [] } } };
export const Empty: Story = {
  args: { detail: chunkDetail({ work_refs: [], history: [] }), workItems: { status: 'success', items: [] } },
};
export const Full: Story = {};
export const AwaitingQuestion: Story = {
  args: {
    detail: chunkDetail({ status: 'needs_human', history: HISTORY, questions: [questionView()] }),
    canAnswer: true,
  },
};
export const AwaitingDecision: Story = {
  args: {
    detail: chunkDetail({ status: 'needs_human', history: HISTORY, decision: decisionView() }),
    canResolve: true,
  },
};
export const ResolvedWithNote: Story = {
  args: {
    detail: chunkDetail({
      status: 'needs_human',
      history: HISTORY,
      decision: decisionView({
        resolved_choice: 'approve',
        resolved_by: 'alice',
        resolved_at: '2026-10-19T12:00:00.000Z',
        note: 'Fix the flaky test only; leave the rest of the suite alone.',
      }),
    }),
    canResolve: true,
  },
};
