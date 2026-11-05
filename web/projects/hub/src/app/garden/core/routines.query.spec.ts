import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import {
  gardenProposalCountsRowView,
  gardenProposalCountsView,
  gardenRoutineView,
  gardenSweepsView,
  gardenTrendView,
  type RequestClientStub,
  stubHubClient,
  settle,
} from 'fleet/testing';
import {
  injectHubRoutineProposalCountsQuery,
  injectHubRoutineSweepsQuery,
  injectHubRoutineTrendQuery,
  injectHubRoutinesQuery,
} from './routines.query';

@Component({
  selector: 'app-test-routines-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRoutinesQueryHost {
  readonly query = injectHubRoutinesQuery();
}

@Component({
  selector: 'app-test-routine-trend-query-host',
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
  selector: 'app-test-routine-sweeps-query-host',
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
  selector: 'app-test-routine-sweeps-disabled-query-host',
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

@Component({
  selector: 'app-test-routine-proposal-counts-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRoutineProposalCountsQueryHost {
  readonly query = injectHubRoutineProposalCountsQuery(
    () => 'nightly',
    () => '2026-01-01T00:00:00Z',
    () => '2026-01-15T00:00:00Z',
  );
}

@Component({
  selector: 'app-test-routine-proposal-counts-disabled-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRoutineProposalCountsDisabledQueryHost {
  readonly query = injectHubRoutineProposalCountsQuery(
    () => null,
    () => '2026-01-01T00:00:00Z',
    () => '2026-01-15T00:00:00Z',
  );
}

describe('injectHubRoutinesQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads the routine list off GET /api/routines', async () => {
    stub = stubHubClient({
      'GET /api/routines': () => [
        gardenRoutineView({ routine_id: 'rtn_1', name: 'nightly', slug: 'nightly', graph_name: 'garden', revision: null }),
      ],
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
    stub = stubHubClient({
      'GET /api/routines/trend': () => gardenTrendView({ until: '2026-01-15T00:00:00Z' }),
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
    stub = stubHubClient({
      'GET /api/routines/{routine_id}/sweeps': () => gardenSweepsView({ until: '2026-01-15T00:00:00Z' }),
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
    stub = stubHubClient({});
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

describe('injectHubRoutineProposalCountsQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads the counts off GET /api/routines/proposal-counts', async () => {
    stub = stubHubClient({
      'GET /api/routines/proposal-counts': () =>
        gardenProposalCountsView({
          until: '2026-01-15T00:00:00Z',
          routine: 'nightly',
          rows: [gardenProposalCountsRowView({ open: 2, passed: 1, accepted_with_item: 3, created: 6 })],
        }),
    });
    TestBed.configureTestingModule({
      imports: [TestRoutineProposalCountsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRoutineProposalCountsQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.rows).toHaveLength(1);
    const calls = stub.forRoute('/api/routines/proposal-counts', 'GET');
    expect(calls).toHaveLength(1);
  });

  it('stays disabled while no routine is selected', async () => {
    stub = stubHubClient({});
    TestBed.configureTestingModule({
      imports: [TestRoutineProposalCountsDisabledQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRoutineProposalCountsDisabledQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.isPending()).toBe(true);
    expect(stub.requests).toHaveLength(0);
  });
});
