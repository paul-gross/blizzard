import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type FindingTriageVerb } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { GardeningFindingTriageDialog } from './gardening-finding-triage-dialog';

const FINDING = {
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
};

async function mount(
  verb: FindingTriageVerb,
  findingIds: readonly string[],
  route: (method: string, path: string) => unknown,
) {
  const stub = stubRequestClient(hubClient, route);
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
      const mounted = await mount(testCase.verb, ['fnd_1', 'fnd_2', 'fnd_3'], () => [FINDING]);
      stub = mounted.stub;
      const { el, fixture } = mounted;

      setNote(el, 'a note');
      testCase.fill?.(el);
      await settle(fixture);
      el.querySelector<HTMLButtonElement>('[data-testid="finding-triage-dialog-submit"]')!.click();
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

  it('refuses submission while the note is blank or whitespace-only', async () => {
    const mounted = await mount('resolve', ['fnd_1'], () => [FINDING]);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="finding-triage-dialog-submit"]')!.disabled).toBe(true);

    setNote(el, '   ');
    await settle(fixture);
    expect(el.querySelector<HTMLButtonElement>('[data-testid="finding-triage-dialog-submit"]')!.disabled).toBe(true);

    setNote(el, 'a real note');
    await settle(fixture);
    expect(el.querySelector<HTMLButtonElement>('[data-testid="finding-triage-dialog-submit"]')!.disabled).toBe(
      false,
    );
  });

  it("supersede also requires the absorbing finding's id", async () => {
    const mounted = await mount('supersede', ['fnd_1'], () => [FINDING]);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    setNote(el, 'folds into fnd_9');
    await settle(fixture);
    expect(el.querySelector<HTMLButtonElement>('[data-testid="finding-triage-dialog-submit"]')!.disabled).toBe(true);

    setSupersededBy(el, 'fnd_9');
    await settle(fixture);
    expect(el.querySelector<HTMLButtonElement>('[data-testid="finding-triage-dialog-submit"]')!.disabled).toBe(
      false,
    );
  });

  it('names the resolve CLI verb, joining every id with spaces', async () => {
    const mounted = await mount('resolve', ['fnd_1', 'fnd_2'], () => [FINDING]);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    setNote(el, 'landed elsewhere');
    await settle(fixture);

    expect(el.querySelector('[data-testid="finding-triage-cli-verb"]')?.textContent).toContain(
      'blizzard hub finding resolve fnd_1 fnd_2 --note "landed elsewhere"',
    );
  });

  it('escapes a quote in the note so the CLI mirror stays a runnable command (F11)', async () => {
    const mounted = await mount('resolve', ['fnd_1'], () => [FINDING]);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    setNote(el, 'the "real" fix landed');
    await settle(fixture);

    expect(el.querySelector('[data-testid="finding-triage-cli-verb"]')?.textContent).toContain(
      String.raw`blizzard hub finding resolve fnd_1 --note "the \"real\" fix landed"`,
    );
  });

  it('names the supersede CLI verb, carrying --by ahead of --note', async () => {
    const mounted = await mount('supersede', ['fnd_1'], () => [FINDING]);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    setNote(el, 'folds into fnd_9');
    setSupersededBy(el, 'fnd_9');
    await settle(fixture);

    expect(el.querySelector('[data-testid="finding-triage-cli-verb"]')?.textContent).toContain(
      'blizzard hub finding supersede fnd_1 --by fnd_9 --note "folds into fnd_9"',
    );
  });

  it('surfaces a rejected batch as an error, keeping the dialog open and the selection intact', async () => {
    const mounted = await mount('resolve', ['fnd_1', 'fnd_2'], (method, path) =>
      method === 'POST' && path === '/api/findings/resolve'
        ? stubError(404, { detail: 'unknown finding among fnd_1, fnd_2' })
        : {},
    );
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));

    setNote(el, 'landed elsewhere');
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="finding-triage-dialog-submit"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="finding-triage-submit-error"]')?.textContent).toContain(
      'unknown finding among fnd_1, fnd_2',
    );
    expect(closed).toBe(false);
    expect(fixture.componentInstance.findingIds()).toEqual(['fnd_1', 'fnd_2']);
  });

  it('closes on a successful submission', async () => {
    const mounted = await mount('reopen', ['fnd_1'], () => [FINDING]);
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));

    setNote(el, 'reopening');
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="finding-triage-dialog-submit"]')!.click();
    await settle(fixture);

    expect(closed).toBe(true);
  });

  it('emits succeeded on a successful submission, distinct from a plain cancel (F1)', async () => {
    const mounted = await mount('reopen', ['fnd_1'], () => [FINDING]);
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let succeeded = false;
    fixture.componentInstance.succeeded.subscribe(() => (succeeded = true));

    fixture.componentInstance.closed.emit();
    expect(succeeded).toBe(false);

    setNote(el, 'reopening');
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="finding-triage-dialog-submit"]')!.click();
    await settle(fixture);

    expect(succeeded).toBe(true);
  });

  it('does not emit succeeded when the batch is rejected', async () => {
    const mounted = await mount('resolve', ['fnd_1'], (method, path) =>
      method === 'POST' && path === '/api/findings/resolve' ? stubError(404, { detail: 'unknown finding' }) : {},
    );
    stub = mounted.stub;
    const { el, fixture } = mounted;
    let succeeded = false;
    fixture.componentInstance.succeeded.subscribe(() => (succeeded = true));

    setNote(el, 'landed elsewhere');
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="finding-triage-dialog-submit"]')!.click();
    await settle(fixture);

    expect(succeeded).toBe(false);
  });
});
