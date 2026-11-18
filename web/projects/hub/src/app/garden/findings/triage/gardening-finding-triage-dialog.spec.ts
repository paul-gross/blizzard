import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type FindingTriageVerb } from '../../core/finding-triage-verb.domain';
import { gardenFindingView, type RequestClientStub, settle, stubError, stubHubClient } from 'fleet/testing';

import { GardeningFindingTriageDialog } from './gardening-finding-triage-dialog';

const FINDING = gardenFindingView({
  finding_id: 'fnd_1',
  routine_name: 'nightly',
  scope_slug: 'blizzard',
  class: 'stale-docstring',
  locus: 'a.py:1',
  summary: 'a',
  state: 'resolved',
  live: false,
  observed_count: 1,
  last_seen_at: '2026-01-01T00:00:00Z',
});

type HubRoutes = Parameters<typeof stubHubClient>[0];

/** Every triage verb's route, each answering with the triaged finding. */
const TRIAGE_ROUTES: HubRoutes = {
  'POST /api/findings/resolve': () => [FINDING],
  'POST /api/findings/confirm-gone': () => [FINDING],
  'POST /api/findings/wont-fix': () => [FINDING],
  'POST /api/findings/not-a-finding': () => [FINDING],
  'POST /api/findings/supersede': () => [FINDING],
  'POST /api/findings/reopen': () => [FINDING],
};

async function mount(
  verb: FindingTriageVerb,
  findingIds: readonly string[],
  routes: HubRoutes = {},
) {
  const stub = stubHubClient({ ...TRIAGE_ROUTES, ...routes });
  await TestBed.configureTestingModule({
    imports: [GardeningFindingTriageDialog],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningFindingTriageDialog);
  fixture.componentRef.setInput('verb', verb);
  fixture.componentRef.setInput('findingIds', findingIds);
  await settle(fixture);
  return { fixture, stub, el: fixture.nativeElement as HTMLElement };
}

function setNote(el: HTMLElement, value: string): void {
  const input = el.querySelector<HTMLTextAreaElement>('[data-testid="finding-triage-note-input"]')!;
  input.value = value;
  input.dispatchEvent(new Event('input'));
}

function setSupersededBy(el: HTMLElement, value: string): void {
  const input = el.querySelector<HTMLInputElement>('[data-testid="finding-triage-superseded-by-input"]')!;
  input.value = value;
  input.dispatchEvent(new Event('input'));
}

/** Every verb's own route, wire vars shape, and CLI verb — one `describe` per
 * verb, `finding.mutations.spec.ts`'s own per-verb `CASES` shape. */
const CASES: readonly {
  verb: FindingTriageVerb;
  route: string;
  extraBody?: Record<string, unknown>;
  fill?: (el: HTMLElement) => void;
}[] = [
  { verb: 'resolve', route: '/api/findings/resolve' },
  { verb: 'confirm-gone', route: '/api/findings/confirm-gone' },
  { verb: 'wont-fix', route: '/api/findings/wont-fix' },
  { verb: 'not-a-finding', route: '/api/findings/not-a-finding' },
  {
    verb: 'supersede',
    route: '/api/findings/supersede',
    extraBody: { superseded_by: 'fnd_9' },
    fill: (el) => setSupersededBy(el, 'fnd_9'),
  },
  { verb: 'reopen', route: '/api/findings/reopen' },
];

describe('GardeningFindingTriageDialog', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  for (const testCase of CASES) {
    it(`issues exactly one ${testCase.verb} request carrying every selected id and the note`, async () => {
      const mounted = await mount(testCase.verb, ['fnd_1', 'fnd_2', 'fnd_3']);
      stub = mounted.stub;
      const { el, fixture } = mounted;

      setNote(el, 'a note');
      testCase.fill?.(el);
      await settle(fixture);
      el.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!.click();
      await settle(fixture);

      const calls = stub.forRoute(testCase.route, 'POST');
      expect(calls).toHaveLength(1);
      expect(calls[0].body).toEqual({
        finding_ids: ['fnd_1', 'fnd_2', 'fnd_3'],
        note: 'a note',
        ...testCase.extraBody,
      });
    });
  }

  const submit = (el: HTMLElement) =>
    el.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!;
  const cancel = (el: HTMLElement) =>
    el.querySelector<HTMLButtonElement>('[data-testid="kit-form-cancel"]')!;

  it('sends nothing for a blank or whitespace-only note, names the missing field, and focuses it', async () => {
    const mounted = await mount('resolve', ['fnd_1']);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    expect(submit(el).disabled).toBe(false);
    submit(el).click();
    await settle(fixture);
    expect(el.textContent).toContain('Required');
    expect(document.activeElement).toBe(el.querySelector('[data-testid="finding-triage-note-input"]'));

    setNote(el, '   ');
    await settle(fixture);
    submit(el).click();
    await settle(fixture);
    expect(stub.forRoute('/api/findings/resolve', 'POST')).toHaveLength(0);
  });

  it("supersede also requires the absorbing finding's id", async () => {
    const mounted = await mount('supersede', ['fnd_1']);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    setNote(el, 'folds into fnd_9');
    await settle(fixture);
    submit(el).click();
    await settle(fixture);
    expect(stub.forRoute('/api/findings/supersede', 'POST')).toHaveLength(0);
    expect(document.activeElement).toBe(el.querySelector('[data-testid="finding-triage-superseded-by-input"]'));
  });

  it('shows a hub field refusal under its field, focused, keeping the draft and the dialog open', async () => {
    const mounted = await mount('supersede', ['fnd_1'], {
      'POST /api/findings/supersede': () =>
        stubError(422, { type: 'urn:blizzard:problem:absorber-not-live', detail: 'fnd_9 is not live' }),
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    let succeeded = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));
    fixture.componentInstance.succeeded.subscribe(() => (succeeded = true));

    setNote(el, 'folds');
    setSupersededBy(el, 'fnd_9');
    await settle(fixture);
    submit(el).click();
    await settle(fixture);

    const field = el.querySelector<HTMLElement>('[data-testid="finding-triage-superseded-by-input"]')!;
    expect(field.closest('fleet-kit-field')!.textContent).toContain('fnd_9 is not live');
    expect(document.activeElement).toBe(field);
    expect(el.querySelector<HTMLTextAreaElement>('[data-testid="finding-triage-note-input"]')!.value).toBe('folds');
    expect(closed).toBe(false);
    expect(succeeded).toBe(false);
  });

  it('shows a root refusal on the form line, keeping the draft and the selection intact', async () => {
    const mounted = await mount('resolve', ['fnd_1', 'fnd_2'], {
      'POST /api/findings/resolve': () => stubError(404, { detail: 'unknown finding among fnd_1, fnd_2' }),
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));

    setNote(el, 'landed elsewhere');
    await settle(fixture);
    submit(el).click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="kit-form-line"]')?.textContent).toContain(
      'unknown finding among fnd_1, fnd_2',
    );
    expect(el.querySelector<HTMLTextAreaElement>('[data-testid="finding-triage-note-input"]')!.value).toBe(
      'landed elsewhere',
    );
    expect(closed).toBe(false);
    expect(fixture.componentInstance.findingIds()).toEqual(['fnd_1', 'fnd_2']);
  });

  it('emits succeeded then closed on success, and only closed on cancel', async () => {
    const mounted = await mount('reopen', ['fnd_1']);
    stub = mounted.stub;
    const { el, fixture } = mounted;
    const events: string[] = [];
    fixture.componentInstance.succeeded.subscribe(() => events.push('succeeded'));
    fixture.componentInstance.closed.subscribe(() => events.push('closed'));

    cancel(el).click();
    await settle(fixture);
    expect(events).toEqual(['closed']);

    events.length = 0;
    setNote(el, 'reopening');
    await settle(fixture);
    submit(el).click();
    await settle(fixture);
    expect(events).toEqual(['succeeded', 'closed']);
  });

  it('disables Save and Cancel and ignores Escape and the backdrop while the save is pending', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const mounted = await mount('resolve', ['fnd_1'], {
      'POST /api/findings/resolve': async () => {
        await gate;
        return [FINDING];
      },
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));

    setNote(el, 'n');
    await settle(fixture);
    submit(el).click();
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(submit(el).disabled).toBe(true);
    });
    expect(cancel(el).disabled).toBe(true);
    el.querySelector('dialog')?.dispatchEvent(new Event('cancel', { cancelable: true }));
    cancel(el).click();
    fixture.detectChanges();
    expect(closed).toBe(false);

    release();
    await settle(fixture);
    await vi.waitFor(() => expect(closed).toBe(true));
  });
});
