import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import type { RoutineRunResponse } from 'fleet';
import {
  gardenScopeView,
  type RequestClientStub,
  routineBaselineView,
  routineRunResponse,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { GardeningRunDialog } from './gardening-run-dialog';

const SCOPES = [
  gardenScopeView({ slug: 'blizzard', description: 'the hub itself', created_at: '2026-01-01T00:00:00Z' }),
  gardenScopeView({ slug: 'web', description: 'the frontend', created_at: '2026-01-01T00:00:00Z' }),
  gardenScopeView({ slug: 'retired-scope', description: 'old ground', created_at: '2026-01-01T00:00:00Z', retired: true }),
  gardenScopeView({ slug: 'unrelated', description: 'not this routine\'s own', created_at: '2026-01-01T00:00:00Z' }),
];

const ROUTINE_SCOPES = ['blizzard', 'retired-scope', 'web'];

const BASELINES = [
  routineBaselineView({
    scope_slug: 'web',
    finding_set_id: 'fins_02XYZ',
    recorded_at: '2026-02-01T00:00:00Z',
    repos: [{ repo: 'blizzard', revision: 'abc123d', landed_since: 5 }],
  }),
];

/** What a run against `web` answers with, minting `chunkId`. */
function runResponse(chunkId: string): RoutineRunResponse {
  return routineRunResponse({ chunk_id: chunkId, scope_slug: 'web', created_at: '2026-03-01T00:00:00Z' });
}

/** Like `settle`, but never calls `whenStable()` — a pending mutation registers itself
 * as an Angular `PendingTasks` entry (`@tanstack/angular-query-experimental`'s own
 * integration), so `whenStable()` would block until it resolves. Only the submit-
 * dismissal-guard test below needs a mutation to stay genuinely pending mid-test; every
 * other test can use `settle`. */
async function pump(fixture: { detectChanges(): void }, ticks = 5): Promise<void> {
  for (let i = 0; i < ticks; i += 1) {
    fixture.detectChanges();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  fixture.detectChanges();
}

async function mount(routes: Parameters<typeof stubHubClient>[0] = {}) {
  const stub = stubHubClient({ ...defaultRoutes(), ...routes });
  await TestBed.configureTestingModule({
    imports: [GardeningRunDialog],
    providers: [
      provideZonelessChangeDetection(),
      provideRouter([]),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningRunDialog);
  fixture.componentRef.setInput('routineId', 'rtn_1');
  fixture.componentRef.setInput('routineName', 'gardening');
  await settle(fixture);
  return { fixture, stub, el: fixture.nativeElement as HTMLElement };
}

function defaultRoutes(): Parameters<typeof stubHubClient>[0] {
  return {
    'GET /api/scopes': () => SCOPES,
    'GET /api/routines/{routine_id}/scopes': () => ROUTINE_SCOPES,
    'GET /api/routines/{routine_id}/baselines': () => BASELINES,
    'POST /api/routines/{routine_id}/run': () => runResponse('ch_new'),
  };
}

describe('GardeningRunDialog', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('offers exactly three fields — scope, mode, and a charge note', async () => {
    const mounted = await mount();
    stub = mounted.stub;

    expect(mounted.el.querySelector('[data-testid="run-scope-field"]')).not.toBeNull();
    expect(mounted.el.querySelector('[data-testid="run-mode-field"]')).not.toBeNull();
    expect(mounted.el.querySelector('[data-testid="run-note-field"]')).not.toBeNull();
  });

  it('lists only the routine\'s related, non-retired scopes with their descriptions, previously-swept first', async () => {
    const mounted = await mount();
    stub = mounted.stub;

    const rows = Array.from(mounted.el.querySelectorAll('[data-testid^="run-scope-option-"]'));
    const slugs = rows.map((r) => r.getAttribute('data-testid')?.replace('run-scope-option-', ''));
    expect(slugs).toEqual(['web', 'blizzard']);
    expect(mounted.el.textContent).not.toContain('retired-scope');
    expect(mounted.el.textContent).not.toContain('unrelated');

    const webRow = mounted.el.querySelector('[data-testid="run-scope-option-web"]')!.closest('label')!;
    expect(webRow.querySelector('[data-testid="run-scope-swept-badge"]')).not.toBeNull();
    expect(webRow.textContent).toContain('the frontend');
  });

  it('renders the empty state, and no scope field, for a routine with no runnable related scope', async () => {
    const mounted = await mount({ 'GET /api/routines/{routine_id}/scopes': () => [] });
    stub = mounted.stub;
    const { el } = mounted;

    expect(el.querySelector('[data-testid="run-dialog-empty"]')?.textContent).toContain(
      'no related scope to run against yet',
    );
    expect(el.querySelector('[data-testid="run-scope-field"]')).toBeNull();
  });

  it('renders the empty state when the routine\'s only related scope is retired', async () => {
    const mounted = await mount({ 'GET /api/routines/{routine_id}/scopes': () => ['retired-scope'] });
    stub = mounted.stub;
    const { el } = mounted;

    expect(el.querySelector('[data-testid="run-dialog-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="run-scope-field"]')).toBeNull();
  });

  it('displays the recorded baseline revision, its instant, and how much has landed since, once delta is selected', async () => {
    const mounted = await mount();
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-web"]')!.click();
    await settle(fixture);
    el.querySelector<HTMLInputElement>('[data-testid="run-mode-delta"]')!.click();
    await settle(fixture);

    const baseline = el.querySelector('[data-testid="run-mode-baseline"]')!;
    expect(baseline.textContent).toContain('fins_02XYZ');
    // The shared relative-date component, not a raw ISO string.
    expect(baseline.textContent).not.toContain('2026-02-01T00:00:00Z');
    expect(baseline.querySelector('fleet-when')).toBeTruthy();
    expect(baseline.textContent).toContain('blizzard@abc123d');
    expect(baseline.textContent).toContain('5 landed since');
  });

  it('says a never-swept pair has no baseline and steers to full — no delta submission is possible', async () => {
    const mounted = await mount();
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-blizzard"]')!.click();
    await settle(fixture);

    expect(el.querySelector<HTMLInputElement>('[data-testid="run-mode-delta"]')!.disabled).toBe(true);
    expect(el.querySelector('[data-testid="run-mode-never-swept"]')).not.toBeNull();
    expect(el.querySelector<HTMLInputElement>('[data-testid="run-mode-full"]')!.checked).toBe(true);
  });

  it('switching off a swept scope steers a selected delta mode back to full', async () => {
    const mounted = await mount();
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-web"]')!.click();
    await settle(fixture);
    el.querySelector<HTMLInputElement>('[data-testid="run-mode-delta"]')!.click();
    await settle(fixture);
    expect(el.querySelector<HTMLInputElement>('[data-testid="run-mode-delta"]')!.checked).toBe(true);

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-blizzard"]')!.click();
    await settle(fixture);

    expect(el.querySelector<HTMLInputElement>('[data-testid="run-mode-full"]')!.checked).toBe(true);
  });

  it('runs against the selected related scope and confirms the chunk id and a board link — no board of its own', async () => {
    const mounted = await mount();
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-blizzard"]')!.click();
    await settle(fixture);

    const btn = el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!;
    btn.click();
    await settle(fixture);
    await settle(fixture);

    const runCalls = stub.forRoute('/api/routines/rtn_1/run', 'POST');
    expect(runCalls).toHaveLength(1);
    expect(runCalls[0].body).toMatchObject({ scope_slug: 'blizzard', mode: 'full' });

    expect(el.querySelector('[data-testid="run-confirmation-chunk-id"]')?.textContent).toBe('ch_new');
    expect(el.querySelector('[data-testid="run-confirmation"]')?.textContent).toContain('not_ready');
    expect(el.querySelector('[data-testid="run-dialog-title"]')?.textContent).not.toContain('Run started');
    const link = el.querySelector('[data-testid="run-confirmation-board-link"]');
    expect(link).not.toBeNull();
    expect(el.querySelector('fleet-board, [data-testid="board"]')).toBeNull();
  });

  it('surfaces a refused run and never renders a confirmation, then lets a resubmit reach the run', async () => {
    let runAttempts = 0;
    const mounted = await mount({
      'POST /api/routines/{routine_id}/run': () => {
        runAttempts += 1;
        return runAttempts === 1 ? stubError(503, { detail: "scope 'web' is retired" }) : runResponse('ch_retry');
      },
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;

    const btn = el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!;
    btn.click();
    await settle(fixture);
    await settle(fixture);

    expect(el.querySelector('[data-testid="run-submit-error"]')?.textContent).toContain('retired');
    expect(el.querySelector('[data-testid="run-confirmation"]')).toBeNull();

    btn.click();
    await settle(fixture);
    await settle(fixture);

    expect(stub.forRoute('/api/routines/rtn_1/run', 'POST')).toHaveLength(2);
    expect(el.querySelector('[data-testid="run-confirmation-chunk-id"]')?.textContent).toBe('ch_retry');
    expect(el.querySelector('[data-testid="run-submit-error"]')).toBeNull();
  });

  it('keeps a submitting run mounted through Escape, a backdrop click, and a disabled Cancel', async () => {
    // The run POST hangs until resolved below, so the mutation stays genuinely pending.
    let resolveRun!: (response: RoutineRunResponse) => void;
    const pendingRun = new Promise<RoutineRunResponse>((resolve) => {
      resolveRun = resolve;
    });
    const mounted = await mount({ 'POST /api/routines/{routine_id}/run': () => pendingRun });
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!.click();
    await pump(fixture);

    expect(el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-cancel"]')!.disabled).toBe(true);

    el.querySelector<HTMLElement>('.scrim')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    el.querySelector<HTMLElement>('.scrim')!.click();
    await pump(fixture);

    expect(el.querySelector('[data-testid="gardening-run-dialog"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="run-confirmation"]')).toBeNull();

    resolveRun(runResponse('ch_new'));
    await settle(fixture);

    expect(el.querySelector('[data-testid="run-confirmation"]')).not.toBeNull();
  });
});
