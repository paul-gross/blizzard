import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type DecisionView, type QuestionView } from 'fleet';
import { decisionView, questionView, settle, type RequestClientStub, stubHubClient } from 'fleet/testing';
import { GatesPanel } from '../gates/gates-panel';
import { QuestionsPanel } from './questions-panel';

const QUESTIONS: QuestionView[] = [
  questionView({
    question_id: 'qn_01',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    question: 'Which API style should the endpoint use?',
    options: ['rest', 'graphql'],
    epoch: 1,
    runner_id: 'rn_01',
    asked_at: '2026-07-16T00:00:01Z',
    answered: false,
  }),
  questionView({
    question_id: 'qn_02',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB',
    question: 'Should the migration backfill in one pass?',
    options: [],
    epoch: 2,
    runner_id: 'rn_02',
    asked_at: '2026-07-16T00:00:02Z',
    answered: false,
  }),
];

describe('QuestionsPanel', () => {
  let stub: RequestClientStub;

  const render = async (
    questions: QuestionView[] | (() => QuestionView[] | Promise<QuestionView[]>) = QUESTIONS,
    gates: () => DecisionView[] = () => [],
  ) => {
    stub = stubHubClient({
      'GET /api/questions': () => typeof questions === 'function' ? questions() : questions,
      'GET /api/decisions': () => ({ decisions: gates() }),
    });
    await TestBed.configureTestingModule({
      imports: [QuestionsPanel, GatesPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(QuestionsPanel);
    await settle(fixture);
    return fixture;
  };

  afterEach(() => stub.restore());

  it('lists every open ask across the fleet, not just the selected chunk (MVP criterion 7)', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    // The rail reads the fleet-wide surface, so an ask on a chunk nobody has opened
    // still shows — that is the whole reason it does not ride on the chunk's detail.
    expect(stub.forRoute('/api/questions', 'GET').length).toBeGreaterThan(0);
    expect(el.querySelectorAll('[data-testid="rail-question"]')).toHaveLength(2);
    expect(el.querySelector('[data-testid="questions-count"]')?.textContent).toContain('2');

    const first = el.querySelectorAll('[data-testid="rail-question"]')[0];
    expect(first.querySelector('[data-testid="rail-question-text"]')?.textContent).toContain('Which API style');
    // The chunk is named the way the board names it, so the two surfaces agree.
    expect(first.querySelector('[data-testid="rail-question-chunk"]')?.textContent).toContain('C-3YJ9');
    expect(first.querySelector('[data-testid="rail-question-options"]')?.textContent).toContain('rest · graphql');
  });

  it('omits the options line for an ask that offers none', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const second = el.querySelectorAll('[data-testid="rail-question"]')[1];
    expect(second.querySelector('[data-testid="rail-question-options"]')).toBeNull();
  });

  it('emits the chunk id when an ask is activated — the answer is given in the dock', async () => {
    const fixture = await render();
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="rail-question"]')?.click();
    expect(selected).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });

  it('hides the panel only once the fleet has no open ask', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.style.display).toBe('none');
    expect(el.querySelector('[data-testid="questions-panel"]')).toBeNull();
    expect(el.querySelector('[data-testid="questions-empty"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="rail-question"]')).toHaveLength(0);
    // The count badge is omitted entirely rather than printing a bare 0 beside "open questions".
    expect(el.querySelector('[data-testid="questions-count"]')).toBeNull();
  });

  it('keeps the loading panel visible until an empty read resolves', async () => {
    let release!: (questions: QuestionView[]) => void;
    const held = new Promise<QuestionView[]>((resolve) => (release = resolve));
    const fixture = await render(() => held);
    const el = fixture.nativeElement as HTMLElement;
    try {
      expect(el.style.display).not.toBe('none');
      expect(el.querySelector('[data-testid="questions-loading"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="questions-empty"]')).toBeNull();
    } finally {
      release([]);
      await settle(fixture);
    }
    expect(el.style.display).toBe('none');
  });

  it('reappears when questions arrive and hides again when they are answered', async () => {
    let questions: QuestionView[] = [];
    const fixture = await render(() => questions);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.style.display).toBe('none');

    questions = QUESTIONS;
    await TestBed.inject(QueryClient).refetchQueries();
    await settle(fixture);
    expect(el.style.display).not.toBe('none');
    expect(el.querySelectorAll('[data-testid="rail-question"]')).toHaveLength(2);

    questions = [];
    await TestBed.inject(QueryClient).refetchQueries();
    await settle(fixture);
    expect(el.style.display).toBe('none');
  });

  it('shows a failed read instead of treating it as known empty, and recovers', async () => {
    let failed = true;
    const fixture = await render(() => {
      if (failed) throw new Error('questions unavailable');
      return QUESTIONS;
    });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.style.display).not.toBe('none');
    expect(el.querySelector('[data-testid="questions-error"]')).not.toBeNull();

    failed = false;
    await TestBed.inject(QueryClient).refetchQueries();
    await settle(fixture);
    expect(el.querySelector('[data-testid="questions-error"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="rail-question"]')).toHaveLength(2);
  });

  it('hides questions and gates independently as their own reads change', async () => {
    let questions: QuestionView[] = [];
    let gates: DecisionView[] = [decisionView()];
    const questionsFixture = await render(() => questions, () => gates);
    const gatesFixture = TestBed.createComponent(GatesPanel);
    await settle(gatesFixture);
    const questionsEl = questionsFixture.nativeElement as HTMLElement;
    const gatesEl = gatesFixture.nativeElement as HTMLElement;
    expect(questionsEl.style.display).toBe('none');
    expect(gatesEl.style.display).not.toBe('none');
    expect(gatesEl.querySelectorAll('[data-testid="rail-gate"]')).toHaveLength(1);

    questions = QUESTIONS;
    gates = [];
    await TestBed.inject(QueryClient).refetchQueries();
    await settle(questionsFixture);
    await settle(gatesFixture);
    expect(questionsEl.style.display).not.toBe('none');
    expect(questionsEl.querySelectorAll('[data-testid="rail-question"]')).toHaveLength(2);
    expect(gatesEl.style.display).toBe('none');
  });
});
