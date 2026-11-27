import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { domainChunkDetail as chunkDetail, domainDecision as decisionView, domainQuestion as questionView, domainTransition as transitionView, workItem } from '../../testing/builders/chunk';
import type { AwaitingHumanFormActions } from '../awaiting-human/awaiting-human.domain';
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

/** Saves that always land, so the asks and the gate render their forms. */
const ACTIONS: AwaitingHumanFormActions = {
  answer: fn(async () => ({ ok: true as const })),
  resolve: fn(async () => ({ ok: true as const })),
};

const meta: Meta<ChunkGeneralTab> = {
  title: 'Chunk/General tab',
  component: ChunkGeneralTab,
  args: {
    detail: chunkDetail({ history: HISTORY }),
    workItems: { status: 'success', items: [workItem()] },
    canAnswer: false,
    canResolve: false,
    awaitingHumanActions: null,
    graphLinkBase: null,
    eventsLinkBase: null,
    issuePanePlacement: 'center',
    showNodeHistory: true,
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
    awaitingHumanActions: ACTIONS,
  },
};
export const AwaitingDecision: Story = {
  args: {
    detail: chunkDetail({ status: 'needs_human', history: HISTORY, decision: decisionView() }),
    canResolve: true,
    awaitingHumanActions: ACTIONS,
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
    awaitingHumanActions: ACTIONS,
  },
};
