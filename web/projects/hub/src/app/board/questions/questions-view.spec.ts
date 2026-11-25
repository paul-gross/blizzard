import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { QuestionsPanelView } from './questions-view';
import type { BoardQuestionPresentation } from './questions.domain';

const QUESTIONS: BoardQuestionPresentation[] = [
  {
    question_id: 'qn_01',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    question: 'Which API style should the endpoint use?',
    options: ['rest', 'graphql'],
    runner_id: 'rn_01',
    runner_name: undefined,
  },
  {
    question_id: 'qn_02',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB',
    question: 'Should the migration backfill in one pass?',
    runner_id: 'rn_02',
    runner_name: undefined,
    options: [],
  },
];

describe('QuestionsPanelView', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [QuestionsPanelView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('lists every question it is handed — off plain inputs alone', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('questions', QUESTIONS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="rail-question"]')).toHaveLength(2);
    expect(el.querySelector('[data-testid="questions-count"]')?.textContent).toContain('2');
    const first = el.querySelectorAll('[data-testid="rail-question"]')[0];
    expect(first.querySelector('[data-testid="rail-question-chunk"]')?.textContent).toContain('C-3YJ9');
    expect(first.querySelector('[data-testid="rail-question-options"]')?.textContent).toContain('rest · graphql');
  });

  it('names each row after its chunk and the question it answers', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('questions', QUESTIONS);
    await fixture.whenStable();
    const row = (fixture.nativeElement as HTMLElement).querySelector(
      '[data-testid="rail-question"]',
    );

    expect(row?.getAttribute('aria-label')).toBe(
      'Open chunk C-3YJ9 to answer its question',
    );
  });

  it("names each ask's runner by its name, its host apart where known, and a runner with no name by its compact id", async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('runnerHosts', new Map([['rn_01KXKVVF1J3D6H6VYZ3XYNH0ST', 'atlas']]));
    fixture.componentRef.setInput('questions', [
      { ...QUESTIONS[0], runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3', runner_name: 'r-claude' },
      { ...QUESTIONS[1], runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M' },
      { ...QUESTIONS[0], question_id: 'qn_hosted', runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNH0ST', runner_name: 'r-chatgpt' },
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const [named, nameless, hosted] = Array.from(el.querySelectorAll('[data-testid="rail-question-runner"]'));
    expect(named.textContent?.trim()).toBe('r-claude');
    expect(named.querySelector('.r-host')).toBeNull();
    expect(named.getAttribute('title')).toBe('R-ABF3.r-claude · rn_01KXKVVF1J3D6H6VYZ3XYNABF3');
    expect(nameless.textContent?.trim()).toBe('R-7Q2M');
    expect(hosted.querySelector('.r-host')?.textContent).toBe('atlas');
    expect(hosted.querySelector('.r-name')?.textContent).toBe('r-chatgpt');
    expect(hosted.textContent?.replace(/\s+/g, '')).toBe('atlas/r-chatgpt');
  });

  it('omits the options line for an ask that offers none', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('questions', QUESTIONS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const second = el.querySelectorAll('[data-testid="rail-question"]')[1];
    expect(second.querySelector('[data-testid="rail-question-options"]')).toBeNull();
  });

  it('opens the focused ask on Enter, in a rail with no selected row', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('questions', QUESTIONS);
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const row = el.querySelector<HTMLElement>('[data-testid="rail-question"]')!;

    expect(
      el.querySelector('[role="listbox"]')?.getAttribute('aria-label'),
    ).toBe('Open questions');
    expect(
      el.querySelector('[role="option"][aria-selected="true"]'),
    ).toBeNull();
    row.focus();
    row.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
    );
    await fixture.whenStable();
    expect(selected).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });

  it('keeps one value per row when a chunk holds several asks, and opens that chunk', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('questions', [QUESTIONS[0], { ...QUESTIONS[1], chunk_id: QUESTIONS[0].chunk_id }]);
    const opened: string[] = [];
    fixture.componentInstance.selectChunk.subscribe((id: string) => opened.push(id));
    const warn = vi.spyOn(console, 'warn');
    await fixture.whenStable();
    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('[data-testid="rail-question"]');
    expect(rows).toHaveLength(2);
    rows[1].click();
    await fixture.whenStable();
    expect(opened).toEqual([QUESTIONS[0].chunk_id]);
    expect(warn.mock.calls.flat().join(' ')).not.toContain('Duplicate option value');
    warn.mockRestore();
  });

  it('emits selectChunk when an ask is activated', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('questions', QUESTIONS);
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="rail-question"]')?.click();
    await fixture.whenStable();
    expect(selected).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });

  it('rests on an empty state with no questions once loaded', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'empty');
    fixture.componentRef.setInput('questions', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="questions-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="questions-count"]')).toBeNull();
  });

  it('withholds the empty copy while the questions read is pending (AC 3)', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'loading');
    fixture.componentRef.setInput('questions', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="questions-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="questions-empty"]')).toBeNull();
  });

  it('keeps a multi-paragraph ask readable as paragraphs rather than collapsing it', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('questions', [
      { ...QUESTIONS[0], question: 'It does not reproduce. Details:\n\n1. Code trace.\n2. Browser test.' },
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The rail shows the same authored prose the detail dock does, so it collapses it no
    // more than the dock does — see chunk-awaiting-human.spec.ts for why the CSS shape,
    // not the rendered line count, is what a jsdom test can assert.
    const text = el.querySelector('[data-testid="rail-question-text"]') as HTMLElement;
    expect(text.textContent).toContain('Details:\n\n1. Code trace.');
    expect(getComputedStyle(text).whiteSpace).toBe('pre-wrap');
  });

  it('shows an error state when the questions read fails', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'error');
    fixture.componentRef.setInput('questions', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="questions-error"]')).not.toBeNull();
  });
});
