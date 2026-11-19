import {
  type AwaitingDecision,
  type AwaitingHuman,
  type AwaitingQuestion,
  awaitingHuman as judgeAwaitingHuman,
} from '../../chunk/awaiting-human/awaiting-human.domain';
import { fixtureInstant } from './clock';

/** An open question the worker asked, with options to pick from. */
export function awaitingQuestion(overrides: Partial<AwaitingQuestion> = {}): AwaitingQuestion {
  return {
    question_id: 'qn_01fixture0000000000000000000',
    question: 'Should the stories reach the shared builders, or keep their own fixtures?',
    options: ['Shared builders', 'Story-local fixtures'],
    answered: false,
    answer: null,
    answered_at: null,
    answered_by: null,
    delivered: false,
    superseded: false,
    ...overrides,
  };
}

/** A question answered a minute before {@link FIXTURE_NOW} and not yet delivered. */
export function answeredQuestion(overrides: Partial<AwaitingQuestion> = {}): AwaitingQuestion {
  return awaitingQuestion({
    options: [],
    answered: true,
    answer: 'Shared builders',
    answered_by: 'alice',
    answered_at: fixtureInstant(-60),
    ...overrides,
  });
}

/** An unresolved gate decision with two choices and one red check. */
export function awaitingDecision(overrides: Partial<AwaitingDecision> = {}): AwaitingDecision {
  return {
    decision_id: 'dc_01fixture0000000000000000000',
    node_name: 'review',
    imposed_by_runner_id: null,
    imposed_by_runner_name: null,
    check_results: [
      { command: 'npm run lint', passed: true },
      { command: 'npm run test', passed: false },
    ],
    choices: [
      { name: 'approve', description: 'Accept the change and move on.', would_override_red_checks: true },
      { name: 'rework', description: 'Send the change back to build.', would_override_red_checks: false },
    ],
    transitioned: false,
    resolved_choice: null,
    resolved_by: null,
    resolved_at: null,
    overrode_red_checks: false,
    note: null,
    ...overrides,
  };
}

/** What a live, non-terminal chunk waits on a human for — nothing unless questions or a decision are given. */
export function awaitingHuman(
  parts: {
    chunk_id?: string;
    terminal?: boolean;
    questions?: readonly AwaitingQuestion[];
    decision?: AwaitingDecision | null;
  } = {},
): AwaitingHuman {
  return judgeAwaitingHuman({
    chunk_id: parts.chunk_id ?? 'ch_01fixture0000000000000000000',
    terminal: parts.terminal ?? false,
    questions: parts.questions ?? [],
    decision: parts.decision ?? null,
  });
}
