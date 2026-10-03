import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { QuestionsPanelView } from './questions-view';

const QUESTIONS = [
  {
    question_id: 'qn_01',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    question: 'Which API style should the endpoint use?',
    options: ['rest', 'graphql'],
    epoch: 1,
    runner_id: 'rn_01',
    asked_at: '2026-07-16T00:00:01Z',
    answered: false,
  },
  {
    question_id: 'qn_02',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB',
    question: 'Should the migration backfill in one pass?',
    epoch: 2,
    runner_id: 'rn_02',
    asked_at: '2026-07-16T00:00:02Z',
    answered: false,
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

  it('omits the options line for an ask that offers none', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('questions', QUESTIONS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const second = el.querySelectorAll('[data-testid="rail-question"]')[1];
    expect(second.querySelector('[data-testid="rail-question-options"]')).toBeNull();
  });

  it('emits selectChunk when an ask is activated', async () => {
    const fixture = TestBed.createComponent(QuestionsPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('questions', QUESTIONS);
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="rail-question"]')?.click();
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
