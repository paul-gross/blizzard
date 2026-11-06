import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type RequestClientStub, runDeltaView, runRowView, settle, stubHubClient } from 'fleet/testing';
import { injectHubRunDeltaQuery, injectHubRunsQuery } from './garden-runs.query';

@Component({
  selector: 'app-test-runs-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRunsQueryHost {
  readonly query = injectHubRunsQuery(() => '2026-01-01T00:00:00Z');
}

@Component({
  selector: 'app-test-run-delta-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRunDeltaQueryHost {
  readonly query = injectHubRunDeltaQuery(() => 'ch_1');
}

@Component({
  selector: 'app-test-run-delta-disabled-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRunDeltaDisabledQueryHost {
  readonly query = injectHubRunDeltaQuery(() => null);
}

describe('injectHubRunsQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads the run list off GET /api/runs', async () => {
    stub = stubHubClient({
      'GET /api/runs': () => [runRowView({ chunk_id: 'ch_1', minted_at: '2026-01-10T00:00:00Z' })],
    });
    TestBed.configureTestingModule({
      imports: [TestRunsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRunsQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toHaveLength(1);
    const calls = stub.forRoute('/api/runs', 'GET');
    expect(calls).toHaveLength(1);
  });
});

describe('injectHubRunDeltaQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads one run’s delta off GET /api/runs/{chunk_id}', async () => {
    stub = stubHubClient({
      'GET /api/runs/{chunk_id}': () => runDeltaView({ chunk_id: 'ch_1' }),
    });
    TestBed.configureTestingModule({
      imports: [TestRunDeltaQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRunDeltaQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.routine_name).toBe('nightly');
    expect(stub.forRoute('/api/runs/ch_1', 'GET')).toHaveLength(1);
  });

  it('stays disabled while no chunk id is selected', async () => {
    stub = stubHubClient({});
    TestBed.configureTestingModule({
      imports: [TestRunDeltaDisabledQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRunDeltaDisabledQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.isPending()).toBe(true);
    expect(stub.requests).toHaveLength(0);
  });
});
