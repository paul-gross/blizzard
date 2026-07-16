import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle } from '../testing/settle';
import { type HubClientStub, stubHubClient } from '../testing/stub-hub-client';
import { RunnerStrip } from './runner-strip';

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

describe('RunnerStrip', () => {
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
      imports: [RunnerStrip],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('renders each runner with its liveness and paused state (D-070/D-043)', async () => {
    const fixture = TestBed.createComponent(RunnerStrip);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(4);
    expect(el.querySelector('[data-runner="rn_online"]')?.getAttribute('data-online')).toBe('true');
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-hub-paused"]')).not.toBeNull();
  });

  it('distinguishes the hub brake, the runner\'s own, and both (#43)', async () => {
    const fixture = TestBed.createComponent(RunnerStrip);
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
    const fixture = TestBed.createComponent(RunnerStrip);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    // rn_local stopped itself, but the hub has not paused it: the only thing this button
    // can do is add the hub's brake, so it must not read "Resume".
    const button = el.querySelector<HTMLButtonElement>('[data-runner="rn_local"] [data-testid="runner-toggle"]');
    expect(button?.textContent?.trim()).toBe('Pause');
  });

  it('pauses an online runner via the client call', async () => {
    const fixture = TestBed.createComponent(RunnerStrip);
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
    const fixture = TestBed.createComponent(RunnerStrip);
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
