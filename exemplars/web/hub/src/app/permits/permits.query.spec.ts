// exemplar: bzh:frontend-empty-state-gated — a disabled read proved to stay off the wire until it is named.

import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type PermitView, hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { injectPermitQuery } from './permits.query';

const GRANTED: PermitView = {
  permit_id: 'pmt_1',
  project_id: 'prj_core',
  project_slug: 'core',
  project_name: 'Core',
  holder: 'ada',
  capability: 'deploy',
  state: 'granted',
  verbs: ['suspend', 'revoke'],
  requested_at: '2026-10-01T09:00:00Z',
  requested_by: 'usr_grace',
};

@Component({
  selector: 'app-test-permit-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestPermitQueryHost {
  readonly permitId = signal<string | null>(null);
  readonly query = injectPermitQuery(() => this.permitId());
}

describe('injectPermitQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  function mount() {
    stub = stubRequestClient(hubClient, () => GRANTED);
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    });
    return TestBed.createComponent(TestPermitQueryHost);
  }

  it('reads nothing while no permit is named', async () => {
    const fixture = mount();
    await settle(fixture);

    expect(stub.requests).toHaveLength(0);
    expect(fixture.componentInstance.query.data()).toBeUndefined();
  });

  it('reads the permit once it is named', async () => {
    const fixture = mount();
    fixture.componentInstance.permitId.set('pmt_1');
    await settle(fixture);

    expect(stub.forRoute('/api/permits/pmt_1', 'GET')).toHaveLength(1);
    expect(fixture.componentInstance.query.data()).toEqual(GRANTED);
  });
});
