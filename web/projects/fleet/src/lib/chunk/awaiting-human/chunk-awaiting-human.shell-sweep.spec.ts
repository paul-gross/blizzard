import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page, userEvent } from 'vitest/browser';

import { answeredQuestion, awaitingDecision, awaitingHuman, awaitingQuestion } from 'fleet/testing';
import type { AwaitingDecision } from './awaiting-human.domain';
import { ChunkAwaitingHuman } from './chunk-awaiting-human';

/**
 * The Gate panel's origin line, note input and resolved line with its note — the tooled half of `web:shell-sweep`: a real
 * headless-Chromium proof that a long runner id in "gated by runner … (runner config)"
 * wraps inside the panel at ~390px rather than forcing horizontal scroll
 * (`bzh:narrow-viewport-tier-rule`). Excluded from the default `ng test fleet` run; run it
 * via `npm run shell-sweep`.
 */

type Parts = NonNullable<Parameters<typeof awaitingHuman>[0]>;

function detail(imposedBy: string | null, resolution: Partial<AwaitingDecision> = {}): Parts {
  return {
    chunk_id: 'ch_01gate0000000000000000000000',
    decision: awaitingDecision({
      decision_id: 'de_01',
      node_name: 'approve-gate',
      choices: [
        { name: 'approve', description: 'Ship it.', would_override_red_checks: false },
        { name: 'reject', description: 'Send it back.', would_override_red_checks: false },
      ],
      check_results: [],
      imposed_by_runner_id: imposedBy,
      ...resolution,
    }),
  };
}

async function mount(imposedBy: string | null, resolution: Partial<AwaitingDecision> = {}) {
  await TestBed.configureTestingModule({
    imports: [ChunkAwaitingHuman],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkAwaitingHuman);
  fixture.componentRef.setInput('state', awaitingHuman(detail(imposedBy, resolution)));
  fixture.componentRef.setInput('canResolve', true);
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await fixture.whenStable();
  return root;
}

describe('gate panel origin line shell sweep (web:shell-sweep)', () => {
  it('navigates and invokes question and decision toolbars once at phone width', async () => {
    await page.viewport(390, 800);
    await TestBed.configureTestingModule({
      imports: [ChunkAwaitingHuman], providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...detail(null),
      questions: [awaitingQuestion({ question_id: 'q1', question: 'Choose an answer', options: ['yes', 'no'] })],
    }));
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('canResolve', true);
    const answers: string[] = [];
    const decisions: string[] = [];
    fixture.componentInstance.answerQuestion.subscribe((event) => answers.push(event.answer));
    fixture.componentInstance.resolveDecision.subscribe((event) => decisions.push(event.choice));
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    try {
      const toolbars = root.querySelectorAll<HTMLElement>('[role="toolbar"]');
      expect(toolbars.length).toBe(2);
      for (const toolbar of toolbars) {
        expect(toolbar.querySelector('[aria-selected]')).toBeNull();
        expect(toolbar.scrollWidth).toBeLessThanOrEqual(toolbar.clientWidth);
      }
      const question = root.querySelectorAll<HTMLElement>('[data-testid="question-option"]');
      question[0].focus();
      await userEvent.keyboard('{ArrowRight}');
      expect(document.activeElement).toBe(question[1]);
      expect(answers).toEqual([]);
      await userEvent.keyboard('{Enter}');
      expect(answers).toEqual(['no']);
      const choices = root.querySelectorAll<HTMLElement>('[data-testid="decision-choice"]');
      choices[0].focus();
      await userEvent.keyboard('{ArrowRight}');
      expect(document.activeElement).toBe(choices[1]);
      expect(decisions).toEqual([]);
      await userEvent.keyboard(' ');
      expect(decisions).toEqual(['reject']);
      fixture.componentRef.setInput('resolvePending', true);
      fixture.componentRef.setInput('pendingAnswerQuestionIds', ['q1']);
      await fixture.whenStable();
      question[0].click();
      choices[0].click();
      expect(answers).toEqual(['no']);
      expect(decisions).toEqual(['reject']);
    } finally {
      root.remove();
    }
  });
  it.each([320, 390])('preserves question and answer lines and indentation without overflow at %ipx', async (width) => {
    await page.viewport(width, 800);
    await TestBed.configureTestingModule({
      imports: [ChunkAwaitingHuman],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    const text = `first line\n  second line\n\n${'long-token-'.repeat(35)}`;
    fixture.componentRef.setInput('state', awaitingHuman({
      questions: [
        awaitingQuestion({ question_id: 'q_open', question: text }),
        answeredQuestion({ question_id: 'q_answered', question: text, answer: text }),
      ],
    }));
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await new Promise((resolve) => requestAnimationFrame(resolve));
      for (const testid of ['question-text', 'answered-question-text', 'answered-answer']) {
        const block = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!;
        expect(block.textContent).toBe(text);
        expect(block.scrollWidth).toBeLessThanOrEqual(block.clientWidth);
        const node = block.firstChild!;
        const range = document.createRange();
        range.setStart(node, 0);
        range.setEnd(node, 1);
        const first = range.getBoundingClientRect();
        const secondIndex = text.indexOf('second');
        range.setStart(node, secondIndex);
        range.setEnd(node, secondIndex + 1);
        const second = range.getBoundingClientRect();
        expect(second.top).toBeGreaterThan(first.top);
        expect(second.left).toBeGreaterThan(first.left);
      }
    } finally {
      root.remove();
    }
  });

  it.each([
    ['a runner-imposed gate', 'r-a-runner-id-long-enough-to-need-wrapping-on-a-narrow-phone-0123456789', 'gated by runner'],
    ['a graph-declared gate', null, 'declared by the graph'],
  ])('keeps %s inside the panel with no page errors or horizontal overflow at ~390px', async (_name, imposedBy, text) => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const root = await mount(imposedBy);

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const origin = root.querySelector<HTMLElement>('[data-testid="decision-origin"]')!;
      const noteInput = root.querySelector<HTMLElement>('[data-testid="decision-note-input"]');
      expect(noteInput, 'the gate note input is not mounted').not.toBeNull();
      expect(noteInput!.getBoundingClientRect().right).toBeLessThanOrEqual(document.documentElement.clientWidth);
      expect(origin.textContent).toContain(text);
      expect(origin.getBoundingClientRect().right).toBeLessThanOrEqual(document.documentElement.clientWidth);
      expect(
        root.scrollWidth,
        `gate panel overflows horizontally at 390px (${root.scrollWidth} > ${root.clientWidth})`,
      ).toBeLessThanOrEqual(root.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});

describe('gate panel resolved line shell sweep (web:shell-sweep)', () => {
  it('keeps a resolved gate’s "<choice> by <who>, <when>" line inside the panel at 390px and 320px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const root = await mount(null, {
      resolved_choice: 'approve-with-a-choice-name-long-enough-to-need-wrapping',
      resolved_by: 'an-operator-username-long-enough-to-need-wrapping@example.com',
      resolved_at: '2026-07-13T00:02:00Z',
      note: 'fix-the-flaky-test-only-and-leave-the-rest-of-the-suite-alone-a-note-long-enough-to-wrap',
    });

    try {
      for (const width of [390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const line = root.querySelector<HTMLElement>('[data-testid="decision-resolved-line"]');
        expect(line?.textContent, `${width}px: the resolved line is absent`).toContain(' by ');
        expect(root.querySelector('[data-testid="decision-choice"]'), `${width}px: choices still offered`).toBeNull();
        const note = root.querySelector<HTMLElement>('[data-testid="decision-resolved-note"]');
        expect(note?.textContent, `${width}px: the resolution note is absent`).toContain('flaky');
        expect(note!.getBoundingClientRect().right).toBeLessThanOrEqual(document.documentElement.clientWidth);
        expect(line!.getBoundingClientRect().right).toBeLessThanOrEqual(document.documentElement.clientWidth);
        expect(
          root.scrollWidth,
          `gate panel overflows horizontally at ${width}px (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
      }
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
