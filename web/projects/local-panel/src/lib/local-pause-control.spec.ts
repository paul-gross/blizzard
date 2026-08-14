import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { LocalPauseControl } from './local-pause-control';

/** Render `LocalPauseControl` with `GET /api/dashboard`'s `runner.pause` triad
 * answered by `pause`, and `PATCH /api/runner` echoing the flipped local brake
 * back — or, when `patchError` is given, answering the PATCH with that
 * failure instead. */
async function render(pause: { local: boolean; hub: boolean }, patchError?: ReturnType<typeof stubError>) {
  const stub = stubRequestClient(runnerClient, (method, path) => {
    if (method === 'GET' && path === '/api/dashboard') {
      return {
        runner: { pause: { local: pause.local, hub: pause.hub, effective: pause.local || pause.hub } },
        environments: { items: [] },
        asks: { items: [] },
        escalations: { items: [] },
        takeovers: { items: [] },
        facts: { items: [] },
        fleet_summary: null,
      };
    }
    if (method === 'PATCH' && path === '/api/runner') {
      if (patchError) return patchError;
      const flipped = !pause.local;
      return { runner_id: 'runner-1', local_paused: flipped, hub_paused: pause.hub, paused: flipped || pause.hub };
    }
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [LocalPauseControl],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalPauseControl);
  await settle(fixture);
  return { fixture, stub };
}

describe('LocalPauseControl', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  it('renders a Pause button when the local brake is off, and PATCHes paused: true when activated', async () => {
    const { fixture, stub: s } = await render({ local: false, hub: false });
    stub = s;
    const el = fixture.nativeElement as HTMLElement;
    const toggle = el.querySelector<HTMLButtonElement>('[data-testid="pause-toggle"]');

    expect(toggle?.textContent?.trim()).toBe('Pause');

    toggle?.click();
    await settle(fixture);

    const patches = stub.forRoute('/api/runner', 'PATCH');
    expect(patches).toHaveLength(1);
    expect(patches[0].body).toMatchObject({ paused: true });
  });

  it('renders a Resume button when the local brake is on, and PATCHes paused: false when activated', async () => {
    const { fixture, stub: s } = await render({ local: true, hub: false });
    stub = s;
    const el = fixture.nativeElement as HTMLElement;
    const toggle = el.querySelector<HTMLButtonElement>('[data-testid="pause-toggle"]');

    expect(toggle?.textContent?.trim()).toBe('Resume');

    toggle?.click();
    await settle(fixture);

    const patches = stub.forRoute('/api/runner', 'PATCH');
    expect(patches).toHaveLength(1);
    expect(patches[0].body).toMatchObject({ paused: false });
  });

  it('shows no paused-by-hub badge when the hub brake is off', async () => {
    const { fixture, stub: s } = await render({ local: false, hub: false });
    stub = s;
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="hub-paused-badge"]')).toBeNull();
  });

  it('shows an explicit paused-by-hub badge when the hub brake is set, even though the local brake is off', async () => {
    const { fixture, stub: s } = await render({ local: false, hub: true });
    stub = s;
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="hub-paused-badge"]')?.textContent).toContain('Paused by hub');
    // The toggle still reflects the local brake alone — it never implies it can
    // clear the hub's, so it still reads "Pause" (local is off).
    expect(el.querySelector('[data-testid="pause-toggle"]')?.textContent?.trim()).toBe('Pause');
  });

  it('shows the paused-by-hub badge alongside a Resume toggle when both brakes are set', async () => {
    const { fixture, stub: s } = await render({ local: true, hub: true });
    stub = s;
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="hub-paused-badge"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="pause-toggle"]')?.textContent?.trim()).toBe('Resume');
  });

  it('renders the paused-by-hub badge with the shared "waiting" tone, not "needs" — the same tone the board gives every other paused status', async () => {
    const { fixture, stub: s } = await render({ local: false, hub: true });
    stub = s;
    const el = fixture.nativeElement as HTMLElement;

    // `chunk-lanes.ts`'s `STATUS_TONE` maps every `paused` chunk to `waiting`
    // (`var(--amber-hi)`), never `needs` (`var(--red)`) — this badge must not
    // disagree by reading as an alarm for the identical condition.
    const style = el.querySelector('[data-testid="hub-paused-badge"] .badge')?.getAttribute('style') ?? '';
    expect(style).toContain('var(--amber-hi)');
    expect(style).not.toContain('var(--red)');
  });

  it('surfaces a failed PATCH rather than swallowing it, and re-enables the toggle', async () => {
    const { fixture, stub: s } = await render({ local: false, hub: false }, stubError(500, { detail: 'runner store unwired' }));
    stub = s;
    const el = fixture.nativeElement as HTMLElement;
    const toggle = el.querySelector<HTMLButtonElement>('[data-testid="pause-toggle"]');

    toggle?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="pause-error"]')?.textContent).toContain('runner store unwired');
    // The toggle re-enables — a failed flip is not a stuck control — and the label
    // still reads "Pause": the local brake never actually moved.
    expect(el.querySelector<HTMLButtonElement>('[data-testid="pause-toggle"]')?.disabled).toBe(false);
    expect(el.querySelector('[data-testid="pause-toggle"]')?.textContent?.trim()).toBe('Pause');
  });

  it('clears a stale pause error on the next toggle attempt', async () => {
    const { fixture, stub: s } = await render({ local: false, hub: false }, stubError(500, { detail: 'runner store unwired' }));
    stub = s;
    let el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-toggle"]')?.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="pause-error"]')).not.toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="pause-toggle"]')?.click();
    await settle(fixture);
    el = fixture.nativeElement as HTMLElement;

    // The retry itself still fails (same stubbed error), but the notice was cleared
    // and reset by the new attempt rather than lingering stale from the first.
    expect(stub.forRoute('/api/runner', 'PATCH')).toHaveLength(2);
    expect(el.querySelector('[data-testid="pause-error"]')).not.toBeNull();
  });
});
