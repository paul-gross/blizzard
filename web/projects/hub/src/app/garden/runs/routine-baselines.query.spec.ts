import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type RequestClientStub, routineBaselineView, settle, stubHubClient } from 'fleet/testing';
import { injectHubRoutineBaselinesQuery } from './routine-baselines.query';

@Component({
  selector: 'app-test-routine-baselines-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestRoutineBaselinesQueryHost {
  readonly routineId = signal<string | undefined>(undefined);
  readonly query = injectHubRoutineBaselinesQuery(this.routineId);
}

describe('injectHubRoutineBaselinesQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('fires no request while no routine is selected', async () => {
    stub = stubHubClient({});
    TestBed.configureTestingModule({
      imports: [TestRoutineBaselinesQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRoutineBaselinesQueryHost);
    await settle(fixture);

    expect(stub.forRoute('/api/routines/rtn_1/baselines', 'GET')).toHaveLength(0);
  });

  it('reads the swept scopes off GET /api/routines/{routine_id}/baselines once a routine is selected', async () => {
    stub = stubHubClient({
      'GET /api/routines/{routine_id}/baselines': () => [
        routineBaselineView({
          scope_slug: 'blizzard',
          finding_set_id: 'fins_1',
          recorded_at: '2026-01-01T00:00:00Z',
          repos: [{ repo: 'blizzard', revision: 'a1b2c3d', landed_since: 3 }],
        }),
      ],
    });
    TestBed.configureTestingModule({
      imports: [TestRoutineBaselinesQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRoutineBaselinesQueryHost);
    fixture.componentInstance.routineId.set('rtn_1');
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toHaveLength(1);
    expect(stub.forRoute('/api/routines/rtn_1/baselines', 'GET')).toHaveLength(1);
  });
});
