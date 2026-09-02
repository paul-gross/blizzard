import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectHubWorkItemQuery } from './work-item.query';

@Component({
  selector: 'fleet-test-work-item-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestWorkItemQueryHost {
  readonly source = signal<string | null>(null);
  readonly ref = signal<string | null>(null);
  readonly query = injectHubWorkItemQuery(
    () => this.source(),
    () => this.ref(),
  );
}

describe('injectHubWorkItemQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('stays disabled while the source/ref pair is incomplete', async () => {
    stub = stubRequestClient(hubClient, () => ({}));
    TestBed.configureTestingModule({
      imports: [TestWorkItemQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestWorkItemQueryHost);
    fixture.componentInstance.source.set('hub');
    await settle(fixture);

    expect(fixture.componentInstance.query.isPending()).toBe(true);
    expect(stub.forRoute('/api/work-sources/hub/items/42', 'GET')).toHaveLength(0);
  });

  it('reads one work item off GET /api/work-sources/{source}/items/{ref}', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/work-sources/hub/items/42') {
        return {
          source: 'hub',
          ref: '42',
          title: 't',
          body: 'b',
          author: { kind: 'user', user_id: 'u_1' },
          closure: null,
          closed_at: null,
          created_at: '2026-01-01T00:00:00Z',
          edited_at: '2026-01-01T00:00:00Z',
          label: 'hub#42',
          stated_priority: null,
          web_url: '/board/chunk/ch_1',
        };
      }
      return {};
    });
    TestBed.configureTestingModule({
      imports: [TestWorkItemQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestWorkItemQueryHost);
    fixture.componentInstance.source.set('hub');
    fixture.componentInstance.ref.set('42');
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.label).toBe('hub#42');
    expect(stub.forRoute('/api/work-sources/hub/items/42', 'GET')).toHaveLength(1);
  });
});
