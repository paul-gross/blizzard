import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { configChangesPage, configChangeView, settle, type RequestClientStub, stubHubClient } from 'fleet/testing';

import { injectConfigChangesQuery } from './changes.query';
import { changeEntries } from './changes.model';
import { changesOfPages } from '../config-history.model';
import { HUB_PROJECT_FILTER } from '../../core/project-filter';
import { hubConfigChangesKey, lensedKey } from '../../core/query-keys';

@Component({ selector: 'app-test-changes-query-host', changeDetection: ChangeDetectionStrategy.OnPush, template: '' })
class ChangesQueryHost {
  readonly changes = injectConfigChangesQuery();
}

describe('change log query', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads domain pages and asks Older for the previous page cursor', async () => {
    const first = configChangesPage({
      changes: [
        configChangeView({ id: 9, revision: 4, apply_id: 'apl_1', record_label: 'Winter', actor_name: 'Alice' }),
        configChangeView({ id: 8, revision: 3, actor: 'system' }),
      ],
      next_before: 9,
      total: 3,
    });
    const second = configChangesPage({ changes: [configChangeView({ id: 4, revision: 2, apply_id: 'apl_1' })], next_before: null, total: 3 });
    stub = stubHubClient({ 'GET /api/config/changes': (request) => (request.search.includes('before=9') ? second : first) });
    TestBed.configureTestingModule({
      imports: [ChangesQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))],
    });
    const fixture = TestBed.createComponent(ChangesQueryHost);
    await settle(fixture);
    const query = fixture.componentInstance.changes;

    expect(query.data()?.pages[0]).toEqual({
      changes: [
        expect.objectContaining({ id: 9, apply_id: 'apl_1', record_kind: 'project', record_label: 'Winter' }),
        expect.objectContaining({ id: 8 }),
      ],
      next_before: 9,
      total: 3,
    });
    expect(query.hasNextPage()).toBe(true);

    await query.fetchNextPage();
    await settle(fixture);

    expect(stub.forRoute('/api/config/changes', 'GET').map((request) => Object.fromEntries(new URLSearchParams(request.search)))).toEqual([
      { limit: '50' }, { limit: '50', before: '9' },
    ]);
    expect(query.data()?.pages.map((page) => [page.total, page.next_before])).toEqual([[3, 9], [3, null]]);
    const changes = changesOfPages(query.data()?.pages);
    expect(changes.map((change) => [change.id, change.revision])).toEqual([[9, 4], [8, 3], [4, 2]]);
    expect(changeEntries(changes).map((entry) => [entry.key, entry.actor, entry.changes.map((change) => change.id)])).toEqual([
      ['apply-apl_1', 'Alice', [9, 4]], ['8', 'system', [8]],
    ]);
    expect(query.data()?.pageParams).toEqual([undefined, 9]);
    expect(query.hasNextPage()).toBe(false);
  });

  it('keeps All and project caches separate and resets the cursor when switching lenses', async () => {
    const project = signal<string | null>(null);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    stub = stubHubClient({
      'GET /api/config/changes': (request) => {
        const params = new URLSearchParams(request.search);
        const lensed = params.get('project') === 'alpha';
        const before = params.has('before');
        return configChangesPage({
          changes: [configChangeView({ id: lensed ? (before ? 20 : 30) : (before ? 4 : 9) })],
          next_before: before ? null : (lensed ? 30 : 9),
          total: 2,
        });
      },
    });
    TestBed.configureTestingModule({
      imports: [ChangesQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(client), { provide: HUB_PROJECT_FILTER, useValue: project }],
    });
    const fixture = TestBed.createComponent(ChangesQueryHost);
    await settle(fixture);
    const query = fixture.componentInstance.changes;
    await query.fetchNextPage();
    await settle(fixture);

    project.set('alpha');
    await settle(fixture);
    expect(query.data()?.pageParams).toEqual([undefined]);
    expect(query.data()?.pages[0].changes[0].id).toBe(30);
    await query.fetchNextPage();
    await settle(fixture);
    expect(query.data()?.pageParams).toEqual([undefined, 30]);
    expect(stub.forRoute('/api/config/changes', 'GET').map((request) => Object.fromEntries(new URLSearchParams(request.search)))).toEqual([
      { limit: '50' }, { limit: '50', before: '9' },
      { limit: '50', project: 'alpha' }, { limit: '50', before: '30', project: 'alpha' },
    ]);
    expect(client.getQueryData(lensedKey(hubConfigChangesKey, null))).toEqual(expect.objectContaining({ pageParams: [undefined, 9] }));
    expect(client.getQueryData(lensedKey(hubConfigChangesKey, 'alpha'))).toEqual(expect.objectContaining({ pageParams: [undefined, 30] }));
    project.set(null);
    await settle(fixture);
    expect(query.data()?.pageParams).toEqual([undefined, 9]);
    expect(query.data()?.pages.map((page) => page.changes[0].id)).toEqual([9, 4]);
  });
});
