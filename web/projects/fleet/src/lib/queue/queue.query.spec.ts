import { Component, input, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { settle } from '../testing/settle';
import { injectHubBacklogQuery } from './queue.query';

/** A minimal host so {@link injectHubBacklogQuery}'s reactive `canReorder`
 * accessor is driven by a real input, the same way `BoardPage` drives it off
 * `canReorder` — an `injectQuery`'s `enabled` gate only re-evaluates inside a
 * live reactive graph, not a bare `TestBed.runInInjectionContext` call. */
@Component({ selector: 'fleet-test-backlog-query-host', template: '' })
class BacklogQueryHost {
  readonly canReorder = input(false);
  readonly query = injectHubBacklogQuery(this.canReorder);
}

describe('injectHubBacklogQuery (bzh:ranking-is-per-list)', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/backlog') {
        return { entries: [{ chunk_id: 'ch_backlog_1', graph_id: 'gr_1', position: 0, work_refs: [] }] };
      }
      return {};
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    });
  });

  afterEach(() => stub.restore());

  it('never issues GET /api/backlog without queue:reorder — the enabled gate itself, not a discarded 403', async () => {
    const fixture = TestBed.createComponent(BacklogQueryHost);
    fixture.componentRef.setInput('canReorder', false);
    await settle(fixture);

    expect(stub.forRoute('/api/backlog', 'GET')).toHaveLength(0);
    // A disabled query never enters flight and reports isPending() forever
    // (query-state.ts's documented trap) — never isError(), so a withheld
    // backlog read cannot surface as a board error.
    expect(fixture.componentInstance.query.fetchStatus()).toBe('idle');
    expect(fixture.componentInstance.query.isPending()).toBe(true);
    expect(fixture.componentInstance.query.isError()).toBe(false);
  });

  it('issues GET /api/backlog and resolves its entries once queue:reorder holds', async () => {
    const fixture = TestBed.createComponent(BacklogQueryHost);
    fixture.componentRef.setInput('canReorder', true);
    await settle(fixture);

    expect(stub.forRoute('/api/backlog', 'GET')).toHaveLength(1);
    expect(fixture.componentInstance.query.data()).toEqual([
      { chunk_id: 'ch_backlog_1', graph_id: 'gr_1', position: 0, work_refs: [] },
    ]);
  });

  it('starts firing once the identity resolves queue:reorder mid-life — the gate re-evaluates, not a one-shot check', async () => {
    const fixture = TestBed.createComponent(BacklogQueryHost);
    fixture.componentRef.setInput('canReorder', false);
    await settle(fixture);
    expect(stub.forRoute('/api/backlog', 'GET')).toHaveLength(0);

    fixture.componentRef.setInput('canReorder', true);
    await settle(fixture);

    expect(stub.forRoute('/api/backlog', 'GET')).toHaveLength(1);
  });
});
