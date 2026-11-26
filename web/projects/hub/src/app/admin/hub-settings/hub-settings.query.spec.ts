import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { settle, stubHubClient, type RequestClientStub } from 'fleet/testing';
import { injectHubSettingsHistoryQuery, injectHubSettingsQuery } from './hub-settings.query';

@Component({ selector: 'app-settings-query-test', changeDetection: ChangeDetectionStrategy.OnPush, template: '' })
class SettingsQueryHost {
  readonly settings = injectHubSettingsQuery(() => true);
  readonly history = injectHubSettingsHistoryQuery(() => true);
}

describe('hub settings queries', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('projects current values and pages history with the original cursors', async () => {
    const change = { id: 9, revision: 4, op: 'edit' as const, door: 'api' as const, actor: 'operator', recorded_at: '2026-01-01', apply_id: null,
      diff: [{ field: 'sweep', old: 60, new: 75, old_label: null, new_label: null }] };
    stub = stubHubClient({
      'GET /api/admin/settings': () => ({ revision: 4, settings: [{ key: 'sweep', value: 75, lifetime: 'pass', source: 'entry', revision: 4, written_at: null, written_by: null }] }),
      'GET /api/admin/settings/changes': (request) => request.search.includes('before=9')
        ? { changes: [{ ...change, id: 3 }], next_before: null } : { changes: [change], next_before: 9 },
    });
    TestBed.configureTestingModule({ imports: [SettingsQueryHost], providers: [provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))] });
    const fixture = TestBed.createComponent(SettingsQueryHost);
    await settle(fixture);
    const { settings, history } = fixture.componentInstance;
    expect(settings.data()?.revision).toBe(4);
    expect(settings.data()?.settings[0].value).toBe(75);
    expect(history.data()?.pages[0]).toEqual({ changes: [change], next_before: 9 });
    await history.fetchNextPage();
    await settle(fixture);
    expect(history.data()?.pages.map((page) => page.changes[0].id)).toEqual([9, 3]);
    expect(history.data()?.pageParams).toEqual([undefined, 9]);
    expect(history.hasNextPage()).toBe(false);
  });
});
