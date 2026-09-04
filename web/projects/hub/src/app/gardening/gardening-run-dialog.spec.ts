import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { GardeningRunDialog } from './gardening-run-dialog';

const SCOPES = [
  { slug: 'blizzard', description: 'the hub itself', created_at: '2026-01-01T00:00:00Z', retired: false },
  { slug: 'web', description: 'the frontend', created_at: '2026-01-01T00:00:00Z', retired: false },
  { slug: 'retired-scope', description: 'old ground', created_at: '2026-01-01T00:00:00Z', retired: true },
];

const BASELINES = [
  {
    scope_slug: 'web',
    finding_set_id: 'fins_02XYZ',
    recorded_at: '2026-02-01T00:00:00Z',
    repos: [{ repo: 'blizzard', revision: 'abc123d', landed_since: 5 }],
  },
];

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

async function mount(route: (method: string, path: string) => unknown) {
  const stub = stubRequestClient(hubClient, route);
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

function defaultRoute(method: string, path: string): unknown {
  if (method === 'GET' && path === '/api/scopes') return SCOPES;
  if (method === 'GET' && path === '/api/routines/rtn_1/baselines') return BASELINES;
  if (method === 'POST' && path === '/api/scopes') {
    return { slug: 'fresh-scope', description: 'a fresh weed patch', created_at: '2026-03-01T00:00:00Z', retired: false };
  }
  if (method === 'POST' && path === '/api/routines/rtn_1/run') {
    return {
      chunk_id: 'ch_new',
      source: 'hub',
      ref: '1',
      title: 'gardening run (full)',
      body: 'Routine: gardening',
      routine_name: 'gardening',
      scope_slug: 'fresh-scope',
      effective_mode: 'full',
      downgraded: false,
      baseline_finding_set_id: null,
      baseline_revisions: null,
      created_at: '2026-03-01T00:00:00Z',
    };
  }
  return {};
}

describe('GardeningRunDialog', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('offers exactly three fields — scope, mode, and a charge note', async () => {
    const mounted = await mount(defaultRoute);
    stub = mounted.stub;

    expect(mounted.el.querySelector('[data-testid="run-scope-field"]')).not.toBeNull();
    expect(mounted.el.querySelector('[data-testid="run-mode-field"]')).not.toBeNull();
    expect(mounted.el.querySelector('[data-testid="run-note-field"]')).not.toBeNull();
  });

  it('lists every non-retired scope with its description, previously-swept first', async () => {
    const mounted = await mount(defaultRoute);
    stub = mounted.stub;

    const rows = Array.from(mounted.el.querySelectorAll('[data-testid^="run-scope-option-"]:not([data-testid$="-new"])'));
    const slugs = rows.map((r) => r.getAttribute('data-testid')?.replace('run-scope-option-', ''));
    expect(slugs).toEqual(['web', 'blizzard']);
    expect(mounted.el.textContent).not.toContain('retired-scope');

    const webRow = mounted.el.querySelector('[data-testid="run-scope-option-web"]')!.closest('label')!;
    expect(webRow.querySelector('[data-testid="run-scope-swept-badge"]')).not.toBeNull();
    expect(webRow.textContent).toContain('the frontend');
  });

  it('requires a description for a new slug and warns on a near match before committing', async () => {
    const mounted = await mount(defaultRoute);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-new"]')!.click();
    await settle(fixture);
    expect(el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!.disabled).toBe(true);

    const slugInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-slug"]')!;
    slugInput.value = 'webb';
    slugInput.dispatchEvent(new Event('input'));
    await settle(fixture);

    expect(el.querySelector('[data-testid="run-scope-near-match-warning"]')?.textContent).toContain('web');
    expect(el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!.disabled).toBe(true);

    const descInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-description"]')!;
    descInput.value = 'a fresh weed patch';
    descInput.dispatchEvent(new Event('input'));
    await settle(fixture);

    expect(el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!.disabled).toBe(false);
  });

  it('displays the recorded baseline revision, its instant, and how much has landed since, once delta is selected', async () => {
    const mounted = await mount(defaultRoute);
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
    const mounted = await mount(defaultRoute);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-blizzard"]')!.click();
    await settle(fixture);

    expect(el.querySelector<HTMLInputElement>('[data-testid="run-mode-delta"]')!.disabled).toBe(true);
    expect(el.querySelector('[data-testid="run-mode-never-swept"]')).not.toBeNull();
    expect(el.querySelector<HTMLInputElement>('[data-testid="run-mode-full"]')!.checked).toBe(true);
  });

  it('switching off a swept scope steers a selected delta mode back to full', async () => {
    const mounted = await mount(defaultRoute);
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

  it('warns on a retired scope\'s exact slug too, even though it is never a picker row', async () => {
    const mounted = await mount(defaultRoute);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-new"]')!.click();
    await settle(fixture);
    const slugInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-slug"]')!;
    slugInput.value = 'retired-scope';
    slugInput.dispatchEvent(new Event('input'));
    await settle(fixture);

    expect(el.querySelector('[data-testid="run-scope-near-match-warning"]')?.textContent).toContain('retired-scope');
  });

  it('mints a new scope before running (D3), then confirms the chunk id and links to the board — no board of its own', async () => {
    const mounted = await mount(defaultRoute);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-new"]')!.click();
    await settle(fixture);
    const slugInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-slug"]')!;
    slugInput.value = 'fresh-scope';
    slugInput.dispatchEvent(new Event('input'));
    await settle(fixture);
    const descInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-description"]')!;
    descInput.value = 'a fresh weed patch';
    descInput.dispatchEvent(new Event('input'));
    await settle(fixture);

    const btn = el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!;
    btn.click();
    await settle(fixture);
    await settle(fixture);

    const createCalls = stub.forRoute('/api/scopes', 'POST');
    expect(createCalls).toHaveLength(1);
    expect(createCalls[0].body).toEqual({ slug: 'fresh-scope', description: 'a fresh weed patch' });
    const runCalls = stub.forRoute('/api/routines/rtn_1/run', 'POST');
    expect(runCalls).toHaveLength(1);
    expect(runCalls[0].body).toMatchObject({ scope_slug: 'fresh-scope', mode: 'full' });

    const createIndex = stub.requests.findIndex((r) => r.path === '/api/scopes' && r.method === 'POST');
    const runIndex = stub.requests.findIndex((r) => r.path === '/api/routines/rtn_1/run' && r.method === 'POST');
    expect(createIndex).toBeGreaterThanOrEqual(0);
    expect(createIndex).toBeLessThan(runIndex);

    expect(el.querySelector('[data-testid="run-confirmation-chunk-id"]')?.textContent).toBe('ch_new');
    const link = el.querySelector('[data-testid="run-confirmation-board-link"]');
    expect(link).not.toBeNull();
    expect(el.querySelector('fleet-board, [data-testid="board"]')).toBeNull();
  });

  it('surfaces a refused run after its scope create already succeeded, and a resubmit reaches the run', async () => {
    let runAttempts = 0;
    const mounted = await mount((method, path) => {
      if (method === 'POST' && path === '/api/routines/rtn_1/run') {
        runAttempts += 1;
        if (runAttempts === 1) return stubError(503, { detail: "scope 'fresh-scope' is retired" });
        return {
          chunk_id: 'ch_retry',
          source: 'hub',
          ref: '1',
          title: 'gardening run (full)',
          body: 'Routine: gardening',
          routine_name: 'gardening',
          scope_slug: 'fresh-scope',
          effective_mode: 'full',
          downgraded: false,
          baseline_finding_set_id: null,
          baseline_revisions: null,
          created_at: '2026-03-01T00:00:00Z',
        };
      }
      return defaultRoute(method, path);
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-new"]')!.click();
    await settle(fixture);
    const slugInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-slug"]')!;
    slugInput.value = 'fresh-scope';
    slugInput.dispatchEvent(new Event('input'));
    await settle(fixture);
    const descInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-description"]')!;
    descInput.value = 'a fresh weed patch';
    descInput.dispatchEvent(new Event('input'));
    await settle(fixture);

    const btn = el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!;
    btn.click();
    await settle(fixture);
    await settle(fixture);

    // The create already landed even though the run that followed it refused.
    expect(stub.forRoute('/api/scopes', 'POST')).toHaveLength(1);
    expect(el.querySelector('[data-testid="run-submit-error"]')?.textContent).toContain('retired');
    expect(el.querySelector('[data-testid="run-confirmation"]')).toBeNull();

    // A resubmit re-creates the same slug — mint-or-no-op (D4) — and this time the run succeeds.
    btn.click();
    await settle(fixture);
    await settle(fixture);

    expect(stub.forRoute('/api/scopes', 'POST')).toHaveLength(2);
    expect(stub.forRoute('/api/routines/rtn_1/run', 'POST')).toHaveLength(2);
    expect(el.querySelector('[data-testid="run-confirmation-chunk-id"]')?.textContent).toBe('ch_retry');
    expect(el.querySelector('[data-testid="run-submit-error"]')).toBeNull();
  });

  it('surfaces a refused scope create and never reaches the run', async () => {
    const mounted = await mount((method, path) => {
      if (method === 'POST' && path === '/api/scopes') return stubError(422, { detail: 'malformed slug' });
      return defaultRoute(method, path);
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-new"]')!.click();
    await settle(fixture);
    const slugInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-slug"]')!;
    slugInput.value = '!!!';
    slugInput.dispatchEvent(new Event('input'));
    await settle(fixture);
    const descInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-description"]')!;
    descInput.value = 'x';
    descInput.dispatchEvent(new Event('input'));
    await settle(fixture);

    el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!.click();
    await settle(fixture);
    await settle(fixture);

    expect(el.querySelector('[data-testid="run-submit-error"]')?.textContent).toContain('malformed slug');
    expect(stub.forRoute('/api/routines/rtn_1/run', 'POST')).toHaveLength(0);
  });

  it('keeps a submitting run mounted through Escape, a backdrop click, and a disabled Cancel', async () => {
    const mounted = await mount(defaultRoute);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    let resolveRun!: (response: Response) => void;
    const pendingRun = new Promise<Response>((resolve) => {
      resolveRun = resolve;
    });
    // Overrides the mount's own stub so the run POST hangs until resolved below —
    // `stubRequestClient`'s `route` callback is read synchronously (never awaited), so
    // it cannot itself model a request that stays in flight.
    hubClient.setConfig({
      baseUrl: 'http://localhost',
      fetch: (async (input: Request) => {
        const url = new URL(input.url);
        if (input.method.toUpperCase() === 'POST' && url.pathname === '/api/routines/rtn_1/run') {
          return pendingRun;
        }
        return new Response(JSON.stringify(defaultRoute(input.method.toUpperCase(), url.pathname)), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }) as typeof fetch,
    });

    el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!.click();
    await pump(fixture);

    expect(el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-cancel"]')!.disabled).toBe(true);

    el.querySelector<HTMLElement>('.scrim')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    el.querySelector<HTMLElement>('.scrim')!.click();
    await pump(fixture);

    expect(el.querySelector('[data-testid="gardening-run-dialog"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="run-confirmation"]')).toBeNull();

    resolveRun(
      new Response(
        JSON.stringify({
          chunk_id: 'ch_new',
          source: 'hub',
          ref: '1',
          title: 'gardening run (full)',
          body: 'Routine: gardening',
          routine_name: 'gardening',
          scope_slug: 'web',
          effective_mode: 'full',
          downgraded: false,
          baseline_finding_set_id: null,
          baseline_revisions: null,
          created_at: '2026-03-01T00:00:00Z',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    await settle(fixture);

    expect(el.querySelector('[data-testid="run-confirmation"]')).not.toBeNull();
  });

  it('trims a newly minted slug and description before they reach POST /api/scopes', async () => {
    const mounted = await mount(defaultRoute);
    stub = mounted.stub;
    const { el, fixture } = mounted;

    el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-new"]')!.click();
    await settle(fixture);
    const slugInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-slug"]')!;
    slugInput.value = '  fresh-scope  ';
    slugInput.dispatchEvent(new Event('input'));
    await settle(fixture);
    const descInput = el.querySelector<HTMLInputElement>('[data-testid="run-new-scope-description"]')!;
    descInput.value = '  a fresh weed patch  ';
    descInput.dispatchEvent(new Event('input'));
    await settle(fixture);

    el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!.click();
    await settle(fixture);
    await settle(fixture);

    const createCalls = stub.forRoute('/api/scopes', 'POST');
    expect(createCalls).toHaveLength(1);
    expect(createCalls[0].body).toEqual({ slug: 'fresh-scope', description: 'a fresh weed patch' });
  });

  it('still offers the mint escape hatch when GET /api/scopes returns zero rows', async () => {
    const mounted = await mount((method, path) => {
      if (method === 'GET' && path === '/api/scopes') return [];
      if (method === 'GET' && path === '/api/routines/rtn_1/baselines') return [];
      return defaultRoute(method, path);
    });
    stub = mounted.stub;
    const { el, fixture } = mounted;

    expect(el.querySelector('[data-testid="run-dialog-empty"]')?.textContent).toContain('No scopes declared yet.');

    const mintOption = el.querySelector<HTMLInputElement>('[data-testid="run-scope-option-new"]')!;
    expect(mintOption).not.toBeNull();
    mintOption.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="run-new-scope-slug"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="run-new-scope-description"]')).not.toBeNull();
  });
});
