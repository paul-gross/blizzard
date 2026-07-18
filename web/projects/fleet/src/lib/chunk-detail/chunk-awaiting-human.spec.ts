import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkDetail } from '../api/hub';
import { ChunkAwaitingHuman } from './chunk-awaiting-human';

const REVIEW_FAIL_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01review0000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 2,
  pm_pointers: [],
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
  pm_pointers: [],
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
  pm_pointers: [],
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

const ESCALATED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01esc00000000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'needs_human',
  current_node_id: 'nd_build',
  latest_epoch: 3,
  pm_pointers: [],
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
