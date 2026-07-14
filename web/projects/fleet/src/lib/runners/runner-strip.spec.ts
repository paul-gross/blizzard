import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle } from '../testing/settle';
import { type HubClientStub, stubHubClient } from '../testing/stub-hub-client';
import { RunnerStrip } from './runner-strip';

const NOW = new Date().toISOString();
const RUNNERS = {
  runners: [
    { runner_id: 'rn_online', workspace_id: 'ws_a', registered_at: NOW, last_seen_at: NOW, online: true, paused: false },
    { runner_id: 'rn_paused', workspace_id: 'ws_b', registered_at: NOW, last_seen_at: NOW, online: true, paused: true },
  ],
};

describe('RunnerStrip', () => {
  let stub: HubClientStub;

  beforeEach(async () => {
    stub = stubHubClient((method, path) => {
      if (method === 'GET' && path === '/api/runners') return RUNNERS;
      if (path === '/api/runners/rn_online/pause') return RUNNERS.runners[0];
      if (path === '/api/runners/rn_paused/resume') return RUNNERS.runners[1];
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

    expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(2);
    expect(el.querySelector('[data-runner="rn_online"]')?.getAttribute('data-online')).toBe('true');
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-paused"]')).not.toBeNull();
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
