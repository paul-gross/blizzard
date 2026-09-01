import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { settle } from '../testing/settle';
import { injectHubRoutineBaselinesQuery } from './routine-baselines.query';

@Component({
  selector: 'fleet-test-routine-baselines-query-host',
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
    stub = stubRequestClient(hubClient, () => ({}));
    TestBed.configureTestingModule({
      imports: [TestRoutineBaselinesQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestRoutineBaselinesQueryHost);
    await settle(fixture);

    expect(stub.forRoute('/api/routines/rtn_1/baselines', 'GET')).toHaveLength(0);
  });

  it('reads the swept scopes off GET /api/routines/{routine_id}/baselines once a routine is selected', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/routines/rtn_1/baselines') {
        return [
          {
            scope_slug: 'blizzard',
            finding_set_id: 'fins_1',
            recorded_at: '2026-01-01T00:00:00Z',
            repos: [{ repo: 'blizzard', revision: 'a1b2c3d', landed_since: 3 }],
          },
        ];
      }
      return {};
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
