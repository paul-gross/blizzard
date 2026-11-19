import { ChangeDetectionStrategy, Component, input, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { formatWhen } from '../../core/when';
import { awaitingDecision, awaitingHuman, awaitingQuestion, recordingAwaitingHumanActions } from 'fleet/testing';
import type { AwaitingDecision, AwaitingHuman, AwaitingHumanFormActions, AwaitingQuestion } from './awaiting-human.domain';
import { ChunkAwaitingHuman } from './chunk-awaiting-human';

/** Every section of the composition over one state, as its composition sites place them. Saves land unless a spec
 * hands its own port. */
@Component({
  selector: 'fleet-awaiting-human-all-sections',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkAwaitingHuman],
  template: `
    @for (section of sections; track section) {
      <fleet-chunk-detail-awaiting-human [section]="section" [state]="state()" [canAnswer]="canAnswer()"
        [canResolve]="canResolve()" [actions]="actions()" />
    }
  `,
})
class AllSections {
  protected readonly sections = ['asks', 'answered', 'decisions'] as const;
  readonly state = input.required<AwaitingHuman>();
  readonly canAnswer = input(false);
  readonly canResolve = input(false);
  readonly actions = input<AwaitingHumanFormActions | null>(recordingAwaitingHumanActions().actions);
}

/** Types into a text control the way a person does. */
function type(control: HTMLInputElement, value: string): void {
  control.value = value;
  control.dispatchEvent(new Event('input'));
}

/** Lets a submit's awaited save and the re-render after it run. */
async function settled(fixture: { whenStable(): Promise<unknown> }): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await fixture.whenStable();
}

/** What a spec renders the component over: a chunk's questions and gate decision. */
interface Parts {
  readonly chunk_id: string;
  readonly terminal?: boolean;
  readonly questions?: readonly AwaitingQuestion[];
  readonly decision?: AwaitingDecision | null;
}

const REVIEW_FAIL: Parts = { chunk_id: 'ch_01review0000000000000000000' };

const WAITING_QUESTION: Parts = {
  chunk_id: 'ch_01ask00000000000000000000000',
  questions: [
    awaitingQuestion({
      question_id: 'qn_01',
      question: 'Which API style should the endpoint use?',
      options: ['rest', 'graphql'],
    }),
  ],
};

const WAITING_DECISION: Parts = {
  chunk_id: 'ch_01gate0000000000000000000000',
  decision: awaitingDecision({
    decision_id: 'de_01',
    node_name: 'approve-gate',
    choices: [
      { name: 'approve', description: 'Ship it.', would_override_red_checks: false },
      { name: 'reject', description: 'Send it back.', would_override_red_checks: false },
    ],
    check_results: [],
  }),
};

/** One answered question in the chunk's list — `delivered` says whether the runner has
 * carried it back into the resumed session yet. */
function answered(overrides: Partial<AwaitingQuestion> & Pick<AwaitingQuestion, 'question_id'>): AwaitingQuestion {
  return awaitingQuestion({
    question: 'Which API style should the endpoint use?',
    options: [],
    answered: true,
    answer: 'rest',
    answered_by: 'alice',
    answered_at: '2026-07-13T00:01:00Z',
    delivered: false,
    ...overrides,
  });
}

/** A chunk whose question has been answered but not yet carried back to the agent. */
const ANSWERED_UNDELIVERED: Parts = {
  chunk_id: 'ch_01trail000000000000000000000',
  questions: [answered({ question_id: 'qn_01' })],
};

/** An agent's ask as they actually arrive — multi-paragraph prose with a numbered list,
 * alongside an answer that carries its own line break. The shape that read as one
 * run-on wall of text before the dock stopped collapsing its whitespace. */
const MULTILINE: Parts = {
  chunk_id: 'ch_01prose00000000000000000000',
  questions: [
    awaitingQuestion({
      question_id: 'qn_open',
      question: 'Issue #214 does not reproduce under any condition I can test. Details:\n\n1. Code trace: the mutation already invalidates both keys.\n2. Live browser test: the board updated within 500ms.\n\nHow should I proceed?',
      options: [],
    }),
    answered({
      question_id: 'qn_answered',
      question: 'Which API style should the endpoint use?\n\nBoth are wired already.',
      answer: 'line one\nline two',
    }),
  ],
};

describe('ChunkAwaitingHuman', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AllSections, ChunkAwaitingHuman],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders each requested section without duplicating controls or history', async () => {
    const fixture = TestBed.createComponent(ChunkAwaitingHuman);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...MULTILINE,
      decision: WAITING_DECISION.decision,
    }));
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('canResolve', true);
    for (const [section, expected] of [
      ['asks', ['open-question']],
      ['answered', ['answered-question']],
      ['decisions', ['open-decision']],
    ] as const) {
      fixture.componentRef.setInput('section', section);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      for (const id of ['open-question', 'answered-question', 'open-decision']) {
        expect(!!el.querySelector(`[data-testid="${id}"]`)).toBe((expected as readonly string[]).includes(id));
      }
    }
  });

  it('surfaces a waiting_on_human chunk’s open question and its options (MVP criterion 7)', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_QUESTION));
    fixture.componentRef.setInput('canAnswer', true);
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
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_DECISION));
    fixture.componentRef.setInput('canResolve', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="awaiting-human"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="decision-node"]')?.textContent).toContain('approve-gate');
    const choices = [...el.querySelectorAll('[data-testid="decision-choice"]')].map((c) => c.textContent?.trim());
    expect(choices).toEqual(['approve', 'reject']);
    expect(el.querySelector('[data-testid="decision-choice"]')?.querySelector('[title]')?.getAttribute('title')).toBe('Ship it.');
    expect(el.querySelector('[data-testid="decision-choice"]')?.hasAttribute('aria-pressed')).toBe(false);
    expect(el.querySelector('[data-testid="open-question"]')).toBeNull();
  });

  it('reads a graph-declared gate as declared by the graph', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_DECISION));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="decision-origin"]')?.textContent?.trim()).toBe('declared by the graph');
    expect(el.querySelector('[data-testid="decision-origin-runner"]')).toBeNull();
  });

  it('names the runner whose configuration imposed the gate by its display name, titled with its full id', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...WAITING_DECISION,
      decision: {
        ...WAITING_DECISION.decision!,
        imposed_by_runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3',
        imposed_by_runner_name: 'r-claude',
      },
    }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="decision-origin"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
      'gated by runner R-ABF3.r-claude (runner config)',
    );
    const runner = el.querySelector('[data-testid="decision-origin-runner"]');
    expect(runner?.textContent?.trim()).toBe('R-ABF3.r-claude');
    expect(runner?.getAttribute('title')).toBe('rn_01KXKVVF1J3D6H6VYZ3XYNABF3');
  });

  it('names a gating runner with no name by its compact id', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...WAITING_DECISION,
      decision: { ...WAITING_DECISION.decision!, imposed_by_runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M' },
    }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="decision-origin-runner"]')?.textContent?.trim()).toBe('R-7Q2M');
  });

  it('withholds the answer input and option chips without question:answer', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_QUESTION));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="awaiting-human"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="question-text"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="answer-input"]')).toBeNull();
    expect(el.querySelector('[data-testid="question-option"]')).toBeNull();
  });

  describe('the step\'s checks on a gate decision', () => {
    // The hub's judgment rides each choice, so a red check marks the gated choice and a green one marks none.
    function withChecks(passed: boolean, overrides: Partial<AwaitingDecision> = {}): Parts {
      return {
        ...WAITING_DECISION,
        decision: {
          ...WAITING_DECISION.decision!,
          choices: [
            { name: 'approve', description: 'Ship it.', would_override_red_checks: !passed },
            { name: 'reject', description: 'Send it back.', would_override_red_checks: false },
          ],
          check_results: [{ command: 'make test', passed }],
          ...overrides,
        },
      };
    }

    function checkLines(el: HTMLElement): string[][] {
      return [...el.querySelectorAll('[data-testid="decision-check"]')].map((li) =>
        [...li.querySelectorAll('span')].map((span) => span.textContent?.trim() ?? ''),
      );
    }

    async function render(parts: Parts): Promise<HTMLElement> {
      const fixture = TestBed.createComponent(AllSections);
      fixture.componentRef.setInput('state', awaitingHuman(parts));
      fixture.componentRef.setInput('canResolve', true);
      await fixture.whenStable();
      return fixture.nativeElement as HTMLElement;
    }

    it('lists each check with its command and verdict, and marks the gated choice as an override while one is red', async () => {
      const el = await render(withChecks(false));

      expect(checkLines(el)).toEqual([['make test', 'red']]);
      const choices = [...el.querySelectorAll('[data-testid="decision-choice"]')].map((c) => c.textContent?.trim());
      expect(choices).toEqual(['approve · overrides red checks', 'reject']);
    });

    it('marks no override while every check is green', async () => {
      const el = await render(withChecks(true));

      expect(checkLines(el)).toEqual([['make test', 'pass']]);
      const choices = [...el.querySelectorAll('[data-testid="decision-choice"]')].map((c) => c.textContent?.trim());
      expect(choices).toEqual(['approve', 'reject']);
    });

    it('notes an override on the resolved line, and none on a clean resolution', async () => {
      const resolved = { resolved_choice: 'approve', resolved_by: 'alice', resolved_at: '2026-07-13T00:02:00Z' };

      const overridden = await render(withChecks(false, { ...resolved, overrode_red_checks: true }));
      expect(overridden.querySelector('[data-testid="decision-override"]')?.textContent?.trim()).toBe('overrode red checks');

      const clean = await render(withChecks(true, resolved));
      expect(clean.querySelector('[data-testid="decision-override"]')).toBeNull();
    });
  });

  it('renders a resolved-but-untransitioned gate as "<choice> by <who>, <when>" in place of its choices', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...WAITING_DECISION,
      decision: {
        ...WAITING_DECISION.decision!,
        resolved_choice: 'approve',
        resolved_by: 'alice',
        resolved_at: '2026-07-13T00:02:00Z',
      },
    }));
    fixture.componentRef.setInput('canResolve', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const line = el.querySelector('[data-testid="decision-resolved-line"]')?.textContent?.replace(/\s+/g, ' ').trim();
    expect(line).toBe(`approve by alice, ${formatWhen('2026-07-13T00:02:00Z', new Date())}`);
    expect(el.querySelector('[data-testid="decision-node"]')?.textContent).toContain('approve-gate');
    expect(el.querySelector('[data-testid="decision-choice"]')).toBeNull();
    expect(el.querySelector('[data-testid="open-decision"]')).toBeNull();
  });

  it('renders an open gate’s choices and no resolved line', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_DECISION));
    fixture.componentRef.setInput('canResolve', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="decision-choice"]')).toHaveLength(2);
    expect(el.querySelector('[data-testid="decision-resolved-line"]')).toBeNull();
  });

  it('withholds the gate choice chips without gate:resolve', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_DECISION));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="decision-node"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="decision-choice"]')).toBeNull();
  });

  it('shows no awaiting-human section when the chunk is not parked', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(REVIEW_FAIL));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="awaiting-human"]')).toBeNull();
  });

  it('saves the typed answer for its question and chunk when Answer is activated (MVP criterion 7)', async () => {
    const port = recordingAwaitingHumanActions();
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_QUESTION));
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('actions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    type(el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!, 'rest');
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();
    await settled(fixture);

    expect(port.answers).toEqual([
      { chunkId: 'ch_01ask00000000000000000000000', questionId: 'qn_01', draft: { answer: 'rest' } },
    ]);
  });

  it('saves a picked option in place of the typed text, even while the typed field is blank', async () => {
    const port = recordingAwaitingHumanActions();
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_QUESTION));
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('actions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelectorAll<HTMLButtonElement>('[data-testid="question-option"]')[1].click();
    await settled(fixture);

    expect(port.answers.map((call) => call.draft)).toEqual([{ answer: 'graphql' }]);
    expect(el.querySelector('fleet-kit-field fleet-kit-error-line')).toBeNull();
  });

  it('sends nothing for a blank answer, showing the required error on its field and focusing it', async () => {
    const port = recordingAwaitingHumanActions();
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_QUESTION));
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('actions', port.actions);
    document.body.appendChild(fixture.nativeElement);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    try {
      type(el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!, '   ');
      el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();
      await settled(fixture);

      expect(port.answers).toEqual([]);
      expect(el.querySelector('fleet-kit-field')?.textContent).toContain('Required');
      expect(document.activeElement).toBe(el.querySelector('[data-testid="answer-input"]'));
    } finally {
      el.remove();
    }
  });

  it('keeps a refused answer’s typed text, showing a field refusal on the field and any other on the form line', async () => {
    const outcomes = [
      { ok: false as const, fields: { answer: 'too long' }, form: null },
      { ok: false as const, fields: {}, form: 'question qn_01 is closed' },
    ];
    const port = recordingAwaitingHumanActions({ answer: async () => outcomes.shift()! });
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_QUESTION));
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('actions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;

    type(input, 'rest');
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();
    await settled(fixture);
    expect(input.value).toBe('rest');
    expect(el.querySelector('fleet-kit-field')?.textContent).toContain('too long');
    expect(el.querySelector('[data-testid="answer-form-error"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();
    await settled(fixture);
    expect(input.value).toBe('rest');
    expect(el.querySelector('fleet-kit-field')?.textContent).not.toContain('too long');
    expect(el.querySelector('[data-testid="answer-form-error"]')?.textContent).toContain('is closed');
  });

  it('lands a picked option’s refusal on the form line, never on the typed field', async () => {
    const port = recordingAwaitingHumanActions({
      answer: async () => ({ ok: false, fields: { answer: 'not an offered option' }, form: null }),
    });
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_QUESTION));
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('actions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="question-option"]')?.click();
    await settled(fixture);

    expect(el.querySelector('[data-testid="answer-form-error"]')?.textContent).toContain('not an offered option');
    expect(el.querySelector('fleet-kit-field fleet-kit-error-line')).toBeNull();
  });

  it('resolves the gate with the chosen choice and the trimmed-as-typed note', async () => {
    const port = recordingAwaitingHumanActions();
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_DECISION));
    fixture.componentRef.setInput('canResolve', true);
    fixture.componentRef.setInput('actions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    type(el.querySelector<HTMLInputElement>('[data-testid="decision-note-input"]')!, 'fix the flaky test only');
    await fixture.whenStable();
    el.querySelectorAll<HTMLButtonElement>('[data-testid="decision-choice"]')[1].click();
    await settled(fixture);

    expect(port.resolves).toEqual([
      {
        chunkId: 'ch_01gate0000000000000000000000',
        decisionId: 'de_01',
        choice: 'reject',
        draft: { note: 'fix the flaky test only' },
      },
    ]);
  });

  it('keeps a refused resolution’s note, showing the refusal on the gate’s form line', async () => {
    const port = recordingAwaitingHumanActions({
      resolve: async () => ({ ok: false, fields: {}, form: "'ship' is not a choice" }),
    });
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_DECISION));
    fixture.componentRef.setInput('canResolve', true);
    fixture.componentRef.setInput('actions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const note = el.querySelector<HTMLInputElement>('[data-testid="decision-note-input"]')!;

    type(note, 'careful');
    el.querySelectorAll<HTMLButtonElement>('[data-testid="decision-choice"]')[0].click();
    await settled(fixture);

    expect(note.value).toBe('careful');
    expect(el.querySelector('[data-testid="decision-form-error"]')?.textContent).toContain('is not a choice');
  });

  it('renders the question, the trail, and the gate with no control when there is no saving port', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({ ...WAITING_QUESTION, decision: WAITING_DECISION.decision }));
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('canResolve', true);
    fixture.componentRef.setInput('actions', null);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="question-text"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="decision-node"]')).not.toBeNull();
    for (const id of ['answer-input', 'answer-submit', 'question-option', 'decision-note-input', 'decision-choice']) {
      expect(el.querySelector(`[data-testid="${id}"]`), id).toBeNull();
    }
  });

  it('shows a resolution’s note under the resolved line, and none without one', async () => {
    const resolved = { resolved_choice: 'approve', resolved_by: 'alice', resolved_at: '2026-07-13T00:02:00Z' };
    const render = async (note: string | null) => {
      const fixture = TestBed.createComponent(AllSections);
      fixture.componentRef.setInput('state', awaitingHuman({
        ...WAITING_DECISION,
        decision: { ...WAITING_DECISION.decision!, ...resolved, note },
      }));
      await fixture.whenStable();
      return fixture.nativeElement as HTMLElement;
    };

    const noted = await render('fix the flaky test only');
    expect(noted.querySelector('[data-testid="decision-resolved-note"]')?.textContent?.trim()).toBe('fix the flaky test only');
    expect((await render(null)).querySelector('[data-testid="decision-resolved-note"]')).toBeNull();
  });

  it('disables a gate’s choices while its resolution is in flight, re-enabling once it settles', async () => {
    let release!: () => void;
    const port = recordingAwaitingHumanActions({
      resolve: () => new Promise((resolve) => (release = () => resolve({ ok: false, fields: {}, form: 'Resolve failed.' }))),
    });
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(WAITING_DECISION));
    fixture.componentRef.setInput('canResolve', true);
    fixture.componentRef.setInput('actions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const choices = () => [...el.querySelectorAll<HTMLButtonElement>('[data-testid="decision-choice"]')];

    choices()[0].click();
    await settled(fixture);
    for (const button of choices()) expect(button.getAttribute('aria-disabled')).toBe('true');
    choices()[1].click();
    expect(port.resolves).toHaveLength(1);

    release();
    await settled(fixture);
    for (const button of choices()) expect(button.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('keeps each open question’s draft, errors, and pending state its own', async () => {
    const [first] = WAITING_QUESTION.questions!;
    let release!: () => void;
    const port = recordingAwaitingHumanActions({
      answer: (call) =>
        call.questionId === 'qn_02'
          ? new Promise((resolve) => (release = () => resolve({ ok: true })))
          : Promise.resolve({ ok: true }),
    });
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...WAITING_QUESTION,
      questions: [first, { ...first, question_id: 'qn_02' }],
    }));
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('actions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const [one, two] = [...el.querySelectorAll<HTMLElement>('[data-testid="open-question"]')];
    const inputOf = (row: HTMLElement) => row.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    const submitOf = (row: HTMLElement) => row.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')!;

    // A blank question 1 refuses locally; question 2's typed text and errors are untouched by it.
    type(inputOf(two), 'graphql');
    submitOf(one).click();
    await settled(fixture);
    expect(one.querySelector('fleet-kit-field')?.textContent).toContain('Required');
    expect(two.querySelector('fleet-kit-field')?.textContent).not.toContain('Required');
    expect(inputOf(two).value).toBe('graphql');

    // Question 2's save in flight disables only its own controls.
    submitOf(two).click();
    await settled(fixture);
    expect(submitOf(two).disabled).toBe(true);
    expect(submitOf(one).disabled).toBe(false);
    for (const chip of one.querySelectorAll<HTMLButtonElement>('[data-testid="question-option"]')) {
      expect(chip.getAttribute('aria-disabled')).not.toBe('true');
    }

    type(inputOf(one), 'rest');
    submitOf(one).click();
    await settled(fixture);
    expect(port.answers.map((call) => [call.questionId, call.draft.answer])).toEqual([
      ['qn_02', 'graphql'],
      ['qn_01', 'rest'],
    ]);

    release();
    await settled(fixture);
    expect(submitOf(two).disabled).toBe(false);
  });

  it('keeps an answered question visible with its trail instead of dropping it', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(ANSWERED_UNDELIVERED));
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

  it('reads “agent resumed” once the delivered fact has landed', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(ANSWERED_UNDELIVERED));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The same row updating in place is the whole point — the SSE re-read swaps the
    // detail, it does not remount a different component.
    fixture.componentRef.setInput('state', awaitingHuman({
      ...ANSWERED_UNDELIVERED,
      questions: [answered({ question_id: 'qn_01', delivered: true })],
    }));
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="answer-delivery"]')?.textContent).toContain('agent resumed');
  });

  it('renders an open question and an already-answered one side by side', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...ANSWERED_UNDELIVERED,
      questions: [
        answered({ question_id: 'qn_01', delivered: true }),
        { ...answered({ question_id: 'qn_02' }), question: 'Rename the field?', answered: false, answer: null },
      ],
    }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The live ask keeps its Answer control; the settled one keeps only its trail.
    expect(el.querySelectorAll('[data-testid="open-question"]').length).toBe(1);
    expect(el.querySelector('[data-testid="question-text"]')?.textContent).toContain('Rename the field?');
    expect(el.querySelectorAll('[data-testid="answered-question"]').length).toBe(1);
  });

  it('caps the answered trail at the three most recently answered, newest first', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...ANSWERED_UNDELIVERED,
      // Oldest first, the order the hub sends them in (by asked_at), answered in the
      // same order — so ask order and answer order agree.
      questions: ['q1', 'q2', 'q3', 'q4'].map((id, i) =>
        answered({ question_id: id, answer: id, answered_at: `2026-07-13T00:0${i + 1}:00Z` }),
      ),
    }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const answers = [...el.querySelectorAll('[data-testid="answered-answer"]')].map((n) => n.textContent?.trim());
    expect(answers).toEqual(['q4', 'q3', 'q2']);
  });

  it('says an undelivered answer will never arrive once the chunk is terminal', async () => {
    // A question answered after its runner went down — or on a chunk since reaped or
    // taken over — has no delivery row and never will. "Delivering to the agent…" there
    // is the one place this trail asserts something false rather than merely stale: it
    // promises a return trip nothing is left to complete.
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({ ...ANSWERED_UNDELIVERED, terminal: true }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const delivery = el.querySelector('[data-testid="answer-delivery"]');
    expect(delivery?.textContent).toContain('Not delivered');
    expect(delivery?.textContent).not.toContain('Delivering');
  });

  it('says a restart-superseded answer was never delivered, on a live chunk', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...ANSWERED_UNDELIVERED,
      questions: [answered({ question_id: 'qn_01', superseded: true })],
    }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const delivery = el.querySelector('[data-testid="answer-delivery"]');
    expect(delivery?.textContent).toContain('Not delivered — the session was restarted');
    expect(delivery?.textContent).not.toContain('Delivering');
  });

  it('still reads as in flight while the chunk can resume', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({ ...ANSWERED_UNDELIVERED }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="answer-delivery"]')?.textContent).toContain('Delivering');
  });

  it('reads as delivered on a terminal chunk whose answer did arrive', async () => {
    // The terminal check must not override a real delivery — a chunk reaching `done`
    // after its answer landed is the ordinary happy path, not a failed return trip.
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...ANSWERED_UNDELIVERED,
      terminal: true,
      questions: [answered({ question_id: 'qn_01', delivered: true })],
    }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="answer-delivery"]')?.textContent).toContain('agent resumed');
  });

  it('orders the trail by when each was answered, not by when it was asked', async () => {
    // The hub sends questions ordered by `asked_at` (chunk_store.load_questions), which
    // is a different order from `answered_at` whenever asks and answers interleave. The
    // panel answers "did *my* answer just land", so sorting on the ask would push the row
    // the operator is looking for out of the cap — q1 here, answered last of all.
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...ANSWERED_UNDELIVERED,
      questions: [
        answered({ question_id: 'q1', answer: 'q1', answered_at: '2026-07-13T00:09:00Z' }),
        answered({ question_id: 'q2', answer: 'q2', answered_at: '2026-07-13T00:02:00Z' }),
        answered({ question_id: 'q3', answer: 'q3', answered_at: '2026-07-13T00:03:00Z' }),
        answered({ question_id: 'q4', answer: 'q4', answered_at: '2026-07-13T00:01:00Z' }),
      ],
    }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const answers = [...el.querySelectorAll('[data-testid="answered-answer"]')].map((n) => n.textContent?.trim());
    expect(answers).toEqual(['q1', 'q3', 'q2']);
  });

  it('renders an ask and its answer as the prose they were written as, keeping the newlines', async () => {
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman(MULTILINE));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Two halves, because collapsing can happen in either. The *text* must survive
    // Angular interpolation with its newlines intact, and the *CSS* must be told not to
    // collapse them — jsdom has no layout engine, so the rendered line count is
    // unassertable here (proven in the browser instead) and the assertable shape is
    // `white-space: pre-wrap`. Without it the browser folds every run of whitespace to
    // a single space and the agent's paragraphs render as one wall of text.
    const ask = el.querySelector('[data-testid="question-text"]') as HTMLElement;
    expect(ask.textContent).toContain('Details:\n\n1. Code trace');
    expect(getComputedStyle(ask).whiteSpace).toBe('pre-wrap');

    const answeredAsk = el.querySelector('[data-testid="answered-question-text"]') as HTMLElement;
    expect(getComputedStyle(answeredAsk).whiteSpace).toBe('pre-wrap');

    const answer = el.querySelector('[data-testid="answered-answer"]') as HTMLElement;
    expect(answer.textContent).toContain('line one\nline two');
    expect(getComputedStyle(answer).whiteSpace).toBe('pre-wrap');

  });

  it('preserves indentation and literal markup in open questions, answered questions and answers', async () => {
    const question = 'First paragraph\n\n  1. indented detail\n    <img src=x onerror=alert(1)>\n[unsafe](javascript:alert)';
    const answer = 'Keep the indentation:\n  **literal**\n    <script>alert(1)</script>';
    const fixture = TestBed.createComponent(AllSections);
    fixture.componentRef.setInput('state', awaitingHuman({
      ...MULTILINE,
      questions: MULTILINE.questions!.map((q) => ({ ...q, question, answer: q.answered ? answer : null })),
    }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    for (const [testid, text] of [
      ['question-text', question], ['answered-question-text', question], ['answered-answer', answer],
    ]) {
      const block = el.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!;
      expect(block.textContent).toBe(text);
      expect(block.querySelector('img, script, a, strong')).toBeNull();
      expect(getComputedStyle(block).whiteSpace).toBe('pre-wrap');
      expect(getComputedStyle(block).overflowWrap).toBe('anywhere');
    }
  });

});
