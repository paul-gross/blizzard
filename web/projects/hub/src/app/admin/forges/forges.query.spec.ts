import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { forgeDetail, settle, type RequestClientStub, stubHubClient } from 'fleet/testing';

import { hubConfigListKey, hubConfigRecordKey } from '../../core/query-keys';
import { injectForgeChoicesQuery, injectForgeHonourQuery, injectForgeListQuery, injectForgeRecordQuery } from './forges.query';

@Component({ selector: 'app-test-forge-query-host', changeDetection: ChangeDetectionStrategy.OnPush, template: '' })
class ForgeQueryHost {
  readonly list = injectForgeListQuery(() => false);
  readonly choices = injectForgeChoicesQuery(() => false);
  readonly record = injectForgeRecordQuery(() => 'frg_g');
  readonly honour = injectForgeHonourQuery(() => 'frg_g');
}

describe('forge query projections', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('shares list and record wire caches across domain projections', async () => {
    const wire = forgeDetail({ landing_policies: [{ policy: 'future_policy' as ReturnType<typeof forgeDetail>['landing_policies'][number]['policy'], honoured: false, missing: ['checks'] }] });
    stub = stubHubClient({ 'GET /api/forges': () => ({ forges: [wire] }), 'GET /api/forges/{forge}': () => wire });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      imports: [ForgeQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)],
    });
    const fixture = TestBed.createComponent(ForgeQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.list.data()).toMatchObject([{ id: 'frg_g', apiBase: 'https://api.github.com' }]);
    expect(fixture.componentInstance.choices.data()).toEqual([{ id: 'frg_g', slug: 'github', name: 'GitHub', retired: false }]);
    expect(fixture.componentInstance.record.data()).toMatchObject({ id: 'frg_g', webOrigin: 'https://github.com' });
    expect(fixture.componentInstance.honour.data()).toEqual([{ policy: { unlisted: 'future_policy' }, honoured: false, missing: ['checks'] }]);
    expect(client.getQueryData(hubConfigListKey('forges', false))).toEqual([wire]);
    expect(client.getQueryData(hubConfigRecordKey('forges', 'frg_g'))).toEqual(wire);
    expect(stub.forRoute('/api/forges', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/forges/frg_g', 'GET')).toHaveLength(1);
  });
});
