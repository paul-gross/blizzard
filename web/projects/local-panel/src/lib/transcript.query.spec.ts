import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { runnerTranscriptKey } from './query-keys';
import { settle } from './testing/settle';
import { RouteError, type RunnerClientStub, stubRunnerClient } from './testing/stub-runner-client';
import { injectTranscriptQuery } from './transcript.query';

const TRANSCRIPT = {
  lease_id: 'L-903',
  session_id: 'sess-77',
  available: true,
  reason: null,
  truncated: false,
  turns: [
    {
      index: 0,
      kind: 'env',
      timestamp: '2026-07-16T11:00:00+00:00',
      text: 'NODE ENVELOPE',
      tool_name: null,
      tool_input: null,
      tool_output: null,
      truncated: false,
    },
  ],
};

/** Mirrors `leases.query.spec.ts`'s host pattern — the query is a `Component`
 * field initializer concern and needs a real injection context. */
@Component({
  selector: 'fleet-test-transcript-query-host',
  template: '',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class TranscriptQueryHost {
  readonly leaseId = signal<string | null>(null);
  readonly query = injectTranscriptQuery(this.leaseId);
}

describe('injectTranscriptQuery', () => {
  let stub: RunnerClientStub | undefined;

  afterEach(() => stub?.restore());

  it('is namespaced under runner, lease id, transcript (D-097 fleet/local split)', () => {
    expect(runnerTranscriptKey('L-903')).toEqual(['runner', 'lease', 'L-903', 'transcript']);
  });

  it('does not fire while leaseId is null — no selection, no request', async () => {
    stub = stubRunnerClient(() => TRANSCRIPT);
    await TestBed.configureTestingModule({
      imports: [TranscriptQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TranscriptQueryHost);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(stub.requests).toHaveLength(0);
  });

  it('reads GET /api/leases/{lease_id}/transcript once a lease id is set', async () => {
    stub = stubRunnerClient((method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript' ? TRANSCRIPT : {},
    );
    await TestBed.configureTestingModule({
      imports: [TranscriptQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TranscriptQueryHost);
    fixture.componentInstance.leaseId.set('L-903');
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toEqual(TRANSCRIPT);
    expect(stub.forRoute('/api/leases/L-903/transcript', 'GET')).toHaveLength(1);
  });

  it('re-fetches under a distinct cache key when the lease id changes — switching rows, not a poll', async () => {
    stub = stubRunnerClient((method, path) => {
      if (method === 'GET' && path === '/api/leases/L-903/transcript') return TRANSCRIPT;
      if (method === 'GET' && path === '/api/leases/L-905/transcript') return { ...TRANSCRIPT, lease_id: 'L-905' };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TranscriptQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TranscriptQueryHost);
    fixture.componentInstance.leaseId.set('L-903');
    await settle(fixture);
    expect(fixture.componentInstance.query.data()?.lease_id).toBe('L-903');

    fixture.componentInstance.leaseId.set('L-905');
    await settle(fixture);
    expect(fixture.componentInstance.query.data()?.lease_id).toBe('L-905');

    expect(stub.forRoute('/api/leases/L-903/transcript', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/leases/L-905/transcript', 'GET')).toHaveLength(1);
  });

  it('never polls — no second request without a lease-id change', async () => {
    // Real-time transcript refresh is out of scope for this issue; unlike the
    // leases query's 5s floor, this must sit still.
    stub = stubRunnerClient((method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript' ? TRANSCRIPT : {},
    );
    await TestBed.configureTestingModule({
      imports: [TranscriptQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TranscriptQueryHost);
    fixture.componentInstance.leaseId.set('L-903');
    await settle(fixture);
    expect(stub.forRoute('/api/leases/L-903/transcript', 'GET')).toHaveLength(1);

    // Give a poll-shaped window plenty of time to fire, then confirm it didn't.
    await new Promise((resolve) => setTimeout(resolve, 20));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(stub.forRoute('/api/leases/L-903/transcript', 'GET')).toHaveLength(1);
  });

  it('surfaces a 503 as an error', async () => {
    stub = stubRunnerClient((method, path) => {
      if (method === 'GET' && path === '/api/leases/L-903/transcript') throw new RouteError(503, 'not wired');
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TranscriptQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TranscriptQueryHost);
    fixture.componentInstance.leaseId.set('L-903');
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
  });
});
