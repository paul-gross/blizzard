import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { settle } from '../testing/settle';
import { type HubClientStub, stubHubClient } from '../testing/stub-hub-client';
import { RunnerPanel } from './runner-panel';

const NOW = new Date().toISOString();
// One runner per pause state: none, the fleet's brake, the runner's own, and both
// (blizzard#43 — they are separate concepts and the strip must say which).
const runner = (id: string, over: Partial<Record<string, unknown>> = {}) => ({
  runner_id: id,
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  hub_paused: false,
  locally_paused: false,
  ...over,
});
const RUNNERS = {
  runners: [
    runner('rn_online'),
    runner('rn_paused', { hub_paused: true }),
    runner('rn_local', { locally_paused: true }),
    runner('rn_both', { hub_paused: true, locally_paused: true }),
  ],
};

describe('RunnerPanel', () => {
  let stub: HubClientStub;

  beforeEach(async () => {
    stub = stubHubClient((method, path) => {
      if (method === 'GET' && path === '/api/runners') return RUNNERS;
      if (path === '/api/runners/rn_online/pause') return RUNNERS.runners[0];
      if (path === '/api/runners/rn_paused/resume') return RUNNERS.runners[1];
      if (path === '/api/runners/rn_local/pause') return RUNNERS.runners[2];
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [RunnerPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('renders each runner with its liveness and paused state (D-070/D-043)', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(4);
    expect(el.querySelector('[data-runner="rn_online"]')?.getAttribute('data-online')).toBe('true');
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-hub-paused"]')).not.toBeNull();
  });

  it('distinguishes the hub brake, the runner\'s own, and both (#43)', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const badges = (id: string) => ({
      hub: el.querySelector(`[data-runner="${id}"] [data-testid="runner-hub-paused"]`) !== null,
      local: el.querySelector(`[data-runner="${id}"] [data-testid="runner-locally-paused"]`) !== null,
    });

    expect(badges('rn_online')).toEqual({ hub: false, local: false });
    expect(badges('rn_paused')).toEqual({ hub: true, local: false });
    expect(badges('rn_local')).toEqual({ hub: false, local: true });
    expect(badges('rn_both')).toEqual({ hub: true, local: true }); // both, not one collapsed badge
  });

  it('offers to pause a locally-paused runner at the hub — the board cannot clear its own brake', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    // rn_local stopped itself, but the hub has not paused it: the only thing this button
    // can do is add the hub's brake, so it must not read "Resume".
    const button = el.querySelector<HTMLButtonElement>('[data-runner="rn_local"] [data-testid="runner-toggle"]');
    expect(button?.textContent?.trim()).toBe('Pause');
  });

  it('pauses an online runner via the client call', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-runner="rn_online"] [data-testid="runner-toggle"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/runners/rn_online/pause', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ by: 'operator' });
    expect(stub.forRoute('/api/runners/rn_online/resume', 'POST')).toHaveLength(0);
  });

  it('resumes a paused runner via the client call', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-runner="rn_paused"] [data-testid="runner-toggle"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/runners/rn_paused/resume', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ by: 'operator' });
    expect(stub.forRoute('/api/runners/rn_paused/pause', 'POST')).toHaveLength(0);
  });
});

describe('RunnerPanel seenLabel (bzh:utc-instants)', () => {
  // Liveness is decided on the hub's clock (`online`); this label is decoration
  // computed against the browser's clock, so `Date.now()` is pinned rather than the
  // wall clock, and `last_seen_at` is placed relative to it.
  const REF = Date.parse('2026-07-16T12:00:00.000Z');
  let stub: HubClientStub;

  function render(lastSeenAt: string, online: boolean): Promise<HTMLElement> {
    const runners = {
      runners: [
        { runner_id: 'r1', workspace_id: 'ws_a', registered_at: lastSeenAt, last_seen_at: lastSeenAt, online, paused: false },
      ],
    };
    stub = stubHubClient((method, path) => (method === 'GET' && path === '/api/runners' ? runners : {}));
    return TestBed.configureTestingModule({
      imports: [RunnerPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    })
      .compileComponents()
      .then(async () => {
        const fixture = TestBed.createComponent(RunnerPanel);
        await settle(fixture);
        return fixture.nativeElement as HTMLElement;
      });
  }

  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(REF);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    stub.restore();
  });

  it('reads a fresh heartbeat as "seen Ns ago"', async () => {
    const el = await render('2026-07-16T11:59:55.000Z', true); // 5s before REF
    expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('seen 5s ago');
  });

  it('reads a small browser-vs-hub skew (<=60s in the future) as "seen 0s ago"', async () => {
    const el = await render('2026-07-16T12:00:30.000Z', true); // 30s after REF
    expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('seen 0s ago');
  });

  it('still reads "seen 0s ago" at exactly the 60s tolerance boundary', async () => {
    const el = await render('2026-07-16T12:01:00.000Z', true); // 60s after REF
    expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('seen 0s ago');
  });

  it('does not render a confident 0s for a stamp hours in the future — falls through to online', async () => {
    // The naive-timestamp bug this guards against: a naive wire stamp on a UTC-5 box
    // reads five hours ahead of the true instant (bzh:utc-instants).
    const el = await render('2026-07-16T17:00:00.000Z', true); // 5h after REF
    expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('online');
  });

  it('falls through to offline (not "0s ago") for a stale runner behind a stamp hours in the future', async () => {
    const el = await render('2026-07-16T17:00:00.000Z', false); // 5h after REF
    expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('offline');
  });
});
