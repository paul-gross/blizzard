import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { credentialDocument, credentialSummary, settle, type RequestClientStub, stubHubClient } from 'fleet/testing';

import { hubConfigListKey, hubConfigRecordKey } from '../../core/query-keys';
import { injectCredentialChoicesQuery, injectCredentialQuery, injectCredentialsQuery } from './credentials.query';

@Component({ selector: 'app-test-credential-query-host', changeDetection: ChangeDetectionStrategy.OnPush, template: '' })
class CredentialQueryHost {
  readonly list = injectCredentialsQuery(() => false);
  readonly choices = injectCredentialChoicesQuery(() => false);
  readonly detail = injectCredentialQuery(() => 'cred_g');
}

describe('credential query projections', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('shares the wire cache between the list and repository credential choices', async () => {
    const wire = {
      credentials: [credentialSummary()],
      bindings: [{ name: 'github.token', forge_kind: 'github', settings: [{ key: 'variant', kind: 'choice' as const, choices: ['classic'] }] }],
      forge_bindings: [{ forge_id: 'frg_g', bindings: ['github.token'] }],
    };
    const detailWire = credentialDocument();
    stub = stubHubClient({ 'GET /api/credentials': () => wire, 'GET /api/credentials/{credential}': () => detailWire });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      imports: [CredentialQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)],
    });
    const fixture = TestBed.createComponent(CredentialQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.list.data()?.credentials).toHaveLength(1);
    expect(fixture.componentInstance.list.data()).toMatchObject({
      credentials: [{ id: 'cred_g', forgeId: 'frg_g' }],
      bindings: [{ name: 'github.token', forgeKind: 'github', settings: [{ key: 'variant', type: { kind: 'choice', choices: ['classic'] } }] }],
      forgeBindings: [{ forgeId: 'frg_g', bindings: ['github.token'] }],
    });
    expect(fixture.componentInstance.detail.data()).toMatchObject({ id: 'cred_g', forgeId: 'frg_g' });
    expect(fixture.componentInstance.choices.data()).toEqual([
      { id: 'cred_g', slug: 'gh-main', binding: 'github.token', forgeId: 'frg_g', retired: false },
    ]);
    expect(client.getQueryData(hubConfigListKey('credentials', false))).toEqual(wire);
    expect(client.getQueryData(hubConfigRecordKey('credentials', 'cred_g'))).toEqual(detailWire);
    expect(stub.forRoute('/api/credentials', 'GET')).toHaveLength(1);
  });
});
