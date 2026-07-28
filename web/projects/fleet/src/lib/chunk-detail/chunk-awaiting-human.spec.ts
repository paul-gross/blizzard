import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkDetail, QuestionView } from '../api/hub';
import { ChunkAwaitingHuman } from './chunk-awaiting-human';

const REVIEW_FAIL_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01review0000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 2,
  work_refs: [],
  history: [],
  artifacts: [],
};

const WAITING_QUESTION_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01ask00000000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'waiting_on_human',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  questions: [
    {
      question_id: 'qn_01',
      chunk_id: 'ch_01ask00000000000000000000000',
      question: 'Which API style should the endpoint use?',
      options: ['rest', 'graphql'],
      epoch: 1,
      runner_id: 'rn_01',
      session_id: 'se_01',
      asked_at: '2026-07-13T00:00:01Z',
      answered: false,
    },
  ],
};

const WAITING_DECISION_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01gate0000000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'waiting_on_human',
  current_node_id: 'nd_gate',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  decision: {
    decision_id: 'de_01',
    chunk_id: 'ch_01gate0000000000000000000000',
    node_id: 'nd_gate',
    node_name: 'approve-gate',
    epoch: 1,
    submitted_at: '2026-07-13T00:00:01Z',
    choices: [
      { name: 'approve', description: 'Ship it.' },
      { name: 'reject', description: 'Send it back.' },
    ],
    transitioned: false,
  },
};

/** One answered question in the chunk's list — `delivered` says whether the runner has
 * carried it back into the resumed session yet (issue #165). */
function answered(overrides: Partial<QuestionView> & Pick<QuestionView, 'question_id'>): QuestionView {
  return {
    chunk_id: 'ch_01trail000000000000000000000',
    question: 'Which API style should the endpoint use?',
    options: [],
    epoch: 1,
    runner_id: 'rn_01',
    asked_at: '2026-07-13T00:00:01Z',
    answered: true,
    answer: 'rest',
    answered_by: 'alice',
    answered_at: '2026-07-13T00:01:00Z',
    delivered: false,
    ...overrides,
  };
}

/** A chunk whose question has been answered but not yet carried back to the agent. */
const ANSWERED_UNDELIVERED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01trail000000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  questions: [answered({ question_id: 'qn_01' })],
};

const ESCALATED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01esc00000000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'needs_human',
  current_node_id: 'nd_build',
  latest_epoch: 3,
  work_refs: [],
  history: [],
  artifacts: [],
  escalation: {
    epoch: 3,
    takeover_command: 'blizzard runner takeover ch_01esc00000000000000000000000',
  },
};

describe('ChunkAwaitingHuman', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkAwaitingHuman],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('surfaces a waiting_on_human chunk’s open question and its options (MVP criterion 7)', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const awaiting = el.querySelector('[data-testid="awaiting-human"]');
    expect(awaiting).not.toBeNull();
    expect(el.querySelector('[data-testid="question-text"]')?.textContent).toContain(
      'Which API style should the endpoint use?',
    );
    const options = [...el.querySelectorAll('[data-testid="question-option"]')].map((o) => o.textContent?.trim());
    expect(options).toEqual(['rest', 'graphql']);
    expect(el.querySelector('[data-testid="open-decision"]')).toBeNull();
  });

  it('surfaces a waiting_on_human chunk’s open gate decision and its choices (MVP criterion 12)', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', WAITING_DECISION_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="awaiting-human"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="decision-node"]')?.textContent).toContain('approve-gate');
    const choices = [...el.querySelectorAll('[data-testid="decision-choice"]')].map((c) => c.textContent?.trim());
    expect(choices).toEqual(['approve', 'reject']);
    expect(el.querySelector('[data-testid="open-question"]')).toBeNull();
  });

  it('shows no awaiting-human section when the chunk is not parked', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="awaiting-human"]')).toBeNull();
  });

  it('emits answerQuestion with the typed answer when Answer is activated (MVP criterion 7)', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    let emitted: { questionId: string; answer: string; chunkId: string } | undefined;
    fixture.componentInstance.answerQuestion.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    input.value = 'rest';
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();

    expect(emitted).toEqual({
      questionId: 'qn_01',
      answer: 'rest',
      chunkId: 'ch_01ask00000000000000000000000',
    });
  });

  it('emits answerQuestion when an option chip is clicked', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    let emitted: { questionId: string; answer: string } | undefined;
    fixture.componentInstance.answerQuestion.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="question-option"]')?.click();
    expect(emitted?.answer).toBe('rest');
  });

  it('does not emit answerQuestion for a blank answer', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    let emitted = false;
    fixture.componentInstance.answerQuestion.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();
    expect(emitted).toBe(false);
  });

  it('emits resolveDecision with the chosen gate choice when a choice button is clicked', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', WAITING_DECISION_DETAIL);
    let emitted: { decisionId: string; choice: string; chunkId: string } | undefined;
    fixture.componentInstance.resolveDecision.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const buttons = el.querySelectorAll<HTMLButtonElement>('[data-testid="decision-choice"]');
    buttons[1].click(); // reject

    expect(emitted).toEqual({
      decisionId: 'de_01',
      choice: 'reject',
      chunkId: 'ch_01gate0000000000000000000000',
    });
  });

  it('keeps an answered question visible with its trail instead of dropping it (issue #165)', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', ANSWERED_UNDELIVERED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Nothing is awaited any more, but the question has not vanished.
    expect(el.querySelector('[data-testid="awaiting-human"]')).toBeNull();
    expect(el.querySelector('[data-testid="answered-question"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="answered-question-text"]')?.textContent).toContain(
      'Which API style should the endpoint use?',
    );
    expect(el.querySelector('[data-testid="answered-by"]')?.textContent).toContain('alice');
    expect(el.querySelector('[data-testid="answered-answer"]')?.textContent).toContain('rest');
    // Answered but not yet delivered: the trail says the return trip is still in flight,
    // never that the agent already resumed.
    expect(el.querySelector('[data-testid="answer-delivery"]')?.textContent).toContain('Delivering');
    expect(el.querySelector('[data-testid="answer-delivery"]')?.textContent).not.toContain('resumed');
  });

  it('reads “agent resumed” once the delivered fact has landed (issue #165)', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', ANSWERED_UNDELIVERED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The same row updating in place is the whole point — the SSE re-read swaps the
    // detail, it does not remount a different component.
    fixture.componentRef.setInput('detail', {
      ...ANSWERED_UNDELIVERED_DETAIL,
      questions: [answered({ question_id: 'qn_01', delivered: true, delivered_at: '2026-07-13T00:01:05Z' })],
    });
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="answer-delivery"]')?.textContent).toContain('agent resumed');
  });

  it('renders an open question and an already-answered one side by side', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', {
      ...ANSWERED_UNDELIVERED_DETAIL,
      status: 'waiting_on_human',
      questions: [
        answered({ question_id: 'qn_01', delivered: true }),
        { ...answered({ question_id: 'qn_02' }), question: 'Rename the field?', answered: false, answer: null },
      ],
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The live ask keeps its Answer control; the settled one keeps only its trail.
    expect(el.querySelectorAll('[data-testid="open-question"]').length).toBe(1);
    expect(el.querySelector('[data-testid="question-text"]')?.textContent).toContain('Rename the field?');
    expect(el.querySelectorAll('[data-testid="answered-question"]').length).toBe(1);
  });

  it('caps the answered trail at the three most recently answered, newest first', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', {
      ...ANSWERED_UNDELIVERED_DETAIL,
      // Oldest first, the order the hub sends them in (by asked_at), answered in the
      // same order — so ask order and answer order agree.
      questions: ['q1', 'q2', 'q3', 'q4'].map((id, i) =>
        answered({ question_id: id, answer: id, answered_at: `2026-07-13T00:0${i + 1}:00Z` }),
      ),
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const answers = [...el.querySelectorAll('[data-testid="answered-answer"]')].map((n) => n.textContent?.trim());
    expect(answers).toEqual(['q4', 'q3', 'q2']);
  });

  it('orders the trail by when each was answered, not by when it was asked', async () => {
    // The hub sends questions ordered by `asked_at` (chunk_store.load_questions), which
    // is a different order from `answered_at` whenever asks and answers interleave. The
    // panel answers "did *my* answer just land", so sorting on the ask would push the row
    // the operator is looking for out of the cap — q1 here, answered last of all.
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', {
      ...ANSWERED_UNDELIVERED_DETAIL,
      questions: [
        answered({ question_id: 'q1', answer: 'q1', answered_at: '2026-07-13T00:09:00Z' }),
        answered({ question_id: 'q2', answer: 'q2', answered_at: '2026-07-13T00:02:00Z' }),
        answered({ question_id: 'q3', answer: 'q3', answered_at: '2026-07-13T00:03:00Z' }),
        answered({ question_id: 'q4', answer: 'q4', answered_at: '2026-07-13T00:01:00Z' }),
      ],
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const answers = [...el.querySelectorAll('[data-testid="answered-answer"]')].map((n) => n.textContent?.trim());
    expect(answers).toEqual(['q1', 'q3', 'q2']);
  });

  it('surfaces an escalation with its copyable takeover command', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('detail', ESCALATED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="escalation"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command"]')?.textContent).toContain(
      'blizzard runner takeover ch_01esc',
    );
    expect(el.querySelector('[data-testid="copy-takeover"]')).not.toBeNull();
  });
});
