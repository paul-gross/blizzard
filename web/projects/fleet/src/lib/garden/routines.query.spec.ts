import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { settle } from '../testing/settle';
import { injectHubRoutineSweepsQuery, injectHubRoutineTrendQuery, injectHubRoutinesQuery } from './routines.query';

@Component({
  selector: 'fleet-test-routines-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRoutinesQueryHost {
  readonly query = injectHubRoutinesQuery();
}

@Component({
  selector: 'fleet-test-routine-trend-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRoutineTrendQueryHost {
  readonly query = injectHubRoutineTrendQuery(
    () => 'nightly',
    () => '2026-01-01T00:00:00Z',
    () => '2026-01-15T00:00:00Z',
    () => '2026-01-01T00:00:00Z',
    () => 7,
  );
}

@Component({
  selector: 'fleet-test-routine-sweeps-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRoutineSweepsQueryHost {
  readonly query = injectHubRoutineSweepsQuery(
    () => 'rtn_1',
    () => '2026-01-01T00:00:00Z',
    () => '2026-01-15T00:00:00Z',
  );
}

@Component({
  selector: 'fleet-test-routine-sweeps-disabled-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRoutineSweepsDisabledQueryHost {
  readonly query = injectHubRoutineSweepsQuery(
    () => null,
    () => '2026-01-01T00:00:00Z',
    () => '2026-01-15T00:00:00Z',
  );
}

describe('injectHubRoutinesQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads the routine list off GET /api/routines', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/routines') {
        return [
          {
            routine_id: 'rtn_1',
            name: 'nightly',
            graph_name: 'garden',
            default_scope_slug: 'blizzard',
            default_model: [],
            default_effort: null,
            created_at: '2026-01-01T00:00:00Z',
          },
        ];
      }
      return {};
    });
    TestBed.configureTestingModule({
      imports: [TestRoutinesQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRoutinesQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toHaveLength(1);
    expect(stub.forRoute('/api/routines', 'GET')).toHaveLength(1);
  });
});

describe('injectHubRoutineTrendQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads the trend off GET /api/routines/trend', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/routines/trend') {
        return {
          routine_name: 'nightly',
          since: '2026-01-01T00:00:00Z',
          until: '2026-01-15T00:00:00Z',
          period_days: 7,
          periods: [],
          age: { boundary: '2026-01-01T00:00:00Z', recent: 0, older: 0, unattributed: 0 },
        };
      }
      return {};
    });
    TestBed.configureTestingModule({
      imports: [TestRoutineTrendQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRoutineTrendQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.routine_name).toBe('nightly');
    expect(stub.forRoute('/api/routines/trend', 'GET')).toHaveLength(1);
  });
});

describe('injectHubRoutineSweepsQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads the sweeps off GET /api/routines/{routine_id}/sweeps', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/routines/rtn_1/sweeps') {
        return {
          routine_name: 'nightly',
          since: '2026-01-01T00:00:00Z',
          until: '2026-01-15T00:00:00Z',
          last_swept: [],
          measurements: [],
        };
      }
      return {};
    });
    TestBed.configureTestingModule({
      imports: [TestRoutineSweepsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRoutineSweepsQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.routine_name).toBe('nightly');
    expect(stub.forRoute('/api/routines/rtn_1/sweeps', 'GET')).toHaveLength(1);
  });

  it('stays disabled while no routine id is selected', async () => {
    stub = stubRequestClient(hubClient);
    TestBed.configureTestingModule({
      imports: [TestRoutineSweepsDisabledQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRoutineSweepsDisabledQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.isPending()).toBe(true);
    expect(stub.requests).toHaveLength(0);
  });
});
