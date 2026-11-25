import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { configChangesPage, configChangeView, settle, type RequestClientStub, stubHubClient } from 'fleet/testing';
import { injectConfigHistoryQuery } from './config-history.query';

@Component({ selector: 'app-test-config-history-host', changeDetection: ChangeDetectionStrategy.OnPush, template: '' })
class ConfigHistoryHost {
  readonly history = injectConfigHistoryQuery('repository', () => 'repo_1');
}

describe('record history query', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('filters by domain kind and key and drains cursors in revision order', async () => {
    const searches: string[] = [];
    stub = stubHubClient({ 'GET /api/config/changes': (request) => {
      searches.push(request.search);
      return request.search.includes('before=9')
        ? configChangesPage({ changes: [configChangeView({ id: 4, revision: 1 })], next_before: null })
        : configChangesPage({ changes: [configChangeView({ id: 9, revision: 2, actor_name: 'Alice' })], next_before: 9 });
    } });
    TestBed.configureTestingModule({
      imports: [ConfigHistoryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))],
    });
    const fixture = TestBed.createComponent(ConfigHistoryHost);
    await settle(fixture);
    expect(searches).toHaveLength(2);
    for (const search of searches) {
      const params = new URLSearchParams(search);
      expect(params.get('record_kind')).toBe('repository');
      expect(params.get('record_key')).toBe('repo_1');
      expect(params.get('limit')).toBe('100');
    }
    expect(new URLSearchParams(searches[1]).get('before')).toBe('9');
    expect(fixture.componentInstance.history.data()).toEqual([
      expect.objectContaining({ id: 9, revision: 2, actor_name: 'Alice' }),
      expect.objectContaining({ id: 4, revision: 1 }),
    ]);
  });
});
