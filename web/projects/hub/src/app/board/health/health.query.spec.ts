import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { hubClient, LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { injectHubHealthQuery } from './health.query';

/** A minimal host mounting the health read in an injection context. */
@Component({
  selector: 'app-test-health-host',
  template: '',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class HealthHost {
  readonly health = injectHubHealthQuery();
}

describe('injectHubHealthQuery', () => {
  let stub: RequestClientStub | undefined;

  afterEach(() => {
    stub?.restore();
    vi.useRealTimers();
  });

  it('re-reads /api/health well inside the live-covered backstop, with no SSE event', async () => {
    vi.useFakeTimers();
    stub = stubRequestClient(hubClient, () => ({ status: 'ok' }));
    await TestBed.configureTestingModule({
      imports: [HealthHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(HealthHost);
    fixture.detectChanges();
    await vi.advanceTimersByTimeAsync(0);
    expect(stub.forRoute('/api/health', 'GET')).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(LIVE_COVERED_POLL_BACKSTOP_MS / 3);

    expect(stub.forRoute('/api/health', 'GET').length).toBeGreaterThanOrEqual(3);
  });
});
