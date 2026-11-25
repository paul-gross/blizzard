import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hostSettingsChangesPage, type RequestClientStub, settle, stubRunnerClient } from 'fleet/testing';
import { SettingsHistory } from './settings-history';

describe('SettingsHistory', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  it('reads the host record`s own rows of the host`s change log, not every record`s', async () => {
    stub = stubRunnerClient({ 'GET /api/host/settings/changes': () => hostSettingsChangesPage() });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))],
    });
    const fixture = TestBed.createComponent(SettingsHistory);
    fixture.componentRef.setInput('ref', { id: 'host', title: 'Host', target: { kind: 'host' } });
    await settle(fixture, 12);

    const search = stub.forRoute('/api/host/settings/changes', 'GET')[0].search;
    expect(search).toContain('record_kind=host');
    expect(search).not.toContain('record_key');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="settings-history-host-change"]')).not.toBeNull();
  });
});
