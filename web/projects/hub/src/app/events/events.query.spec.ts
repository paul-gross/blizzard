import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { provideProjectLens } from '../core/project-lens';
import { injectHubEventsQuery } from './events.query';

@Component({ selector: 'app-test-events-lens-view', template: '' })
class TestView {}

@Component({
  selector: 'app-test-events-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestEventsQueryHost {
  readonly query = injectHubEventsQuery(() => ({ severity: null, runnerId: 'rn_1', chunkId: null }));
}

describe('injectHubEventsQuery under the project lens', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (_method, path) => (path === '/api/events' ? { events: [] } : {}));
    await TestBed.configureTestingModule({
      imports: [TestEventsQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'p/:projectSlug', children: [{ path: '**', component: TestView }] },
          { path: '**', component: TestView },
        ]),
        provideProjectLens(),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  async function searchAt(url: string): Promise<URLSearchParams> {
    await TestBed.inject(Router).navigateByUrl(url);
    await settle(TestBed.createComponent(TestEventsQueryHost));
    const [request] = stub.forRoute('/api/events', 'GET');
    return new URLSearchParams(request.search);
  }

  it('sends the lensed project beside the view own filters', async () => {
    const search = await searchAt('/p/alpha/events');

    expect(search.get('project')).toBe('alpha');
    expect(search.get('runner_id')).toBe('rn_1');
  });

  it('names no project on All', async () => {
    const search = await searchAt('/events');

    expect(search.has('project')).toBe(false);
    expect(search.get('runner_id')).toBe('rn_1');
  });
});
