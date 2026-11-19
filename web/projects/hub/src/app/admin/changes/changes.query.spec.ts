import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { configChangesPage, configChangeView, settle, type RequestClientStub, stubHubClient } from 'fleet/testing';

import { injectConfigChangesQuery } from './changes.query';

@Component({ selector: 'app-test-changes-query-host', changeDetection: ChangeDetectionStrategy.OnPush, template: '' })
class ChangesQueryHost {
  readonly changes = injectConfigChangesQuery();
}

describe('change log query', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads domain pages and asks Older for the previous page cursor', async () => {
    const first = configChangesPage({
      changes: [configChangeView({ id: 9, apply_id: 'apl_1', record_label: 'Winter' })],
      next_before: 9,
      total: 2,
    });
    const second = configChangesPage({ changes: [configChangeView({ id: 4 })], next_before: null, total: 2 });
    stub = stubHubClient({ 'GET /api/config/changes': (request) => (request.search.includes('before=9') ? second : first) });
    TestBed.configureTestingModule({
      imports: [ChangesQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))],
    });
    const fixture = TestBed.createComponent(ChangesQueryHost);
    await settle(fixture);
    const query = fixture.componentInstance.changes;

    expect(query.data()?.pages[0]).toEqual({
      changes: [expect.objectContaining({ id: 9, apply_id: 'apl_1', record_kind: 'project', record_label: 'Winter' })],
      next_before: 9,
      total: 2,
    });
    expect(query.hasNextPage()).toBe(true);

    await query.fetchNextPage();
    await settle(fixture);

    expect(query.data()?.pages.map((page) => page.changes.map((change) => change.id))).toEqual([[9], [4]]);
    expect(query.data()?.pageParams).toEqual([undefined, 9]);
    expect(query.hasNextPage()).toBe(false);
  });
});
