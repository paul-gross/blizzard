import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { HUB_PROJECT_FILTER } from 'fleet/shell';
import { gardenCadenceErrandView, gardenCadenceView, type RequestClientStub, settle, stubHubClient } from 'fleet/testing';
import { injectHubCadenceErrandsQuery, injectHubCadencesQuery } from './cadences.query';

@Component({
  selector: 'app-test-cadences-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestCadencesQueryHost {
  readonly cadenceId = signal<string | null>(null);
  readonly cadences = injectHubCadencesQuery();
  readonly errands = injectHubCadenceErrandsQuery(() => this.cadenceId());
}

describe('cadences queries', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  function mount(lens: string | null) {
    stub = stubHubClient({
      'GET /api/cadences': () => [gardenCadenceView({ slug: 'tenant' })],
      'GET /api/projects/{project}/cadences': () => [gardenCadenceView({ slug: 'evening' })],
      'GET /api/cadences/{cadence_id}/errands': () => [gardenCadenceErrandView()],
    });
    TestBed.configureTestingModule({
      imports: [TestCadencesQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient()),
        { provide: HUB_PROJECT_FILTER, useValue: signal(lens).asReadonly() },
      ],
    });
    return TestBed.createComponent(TestCadencesQueryHost);
  }

  it("reads the lensed project's cadences, retired ones included", async () => {
    const fixture = mount('acme');
    await settle(fixture);

    expect(fixture.componentInstance.cadences.data()?.map((c) => c.slug)).toEqual(['evening']);
    const calls = stub.forRoute('/api/projects/acme/cadences', 'GET');
    expect(calls).toHaveLength(1);
    expect(calls[0].search).toBe('?include_retired=true');
  });

  it("reads the tenant's cadences on All", async () => {
    const fixture = mount(null);
    await settle(fixture);

    expect(fixture.componentInstance.cadences.data()?.map((c) => c.slug)).toEqual(['tenant']);
  });

  it('reads no errands until a cadence is named, then that cadence’s', async () => {
    const fixture = mount('acme');
    await settle(fixture);
    expect(stub.forRoute('/api/cadences/cad_evening/errands', 'GET')).toHaveLength(0);

    fixture.componentInstance.cadenceId.set('cad_evening');
    await settle(fixture);

    expect(fixture.componentInstance.errands.data()).toHaveLength(1);
    expect(stub.forRoute('/api/cadences/cad_evening/errands', 'GET')).toHaveLength(1);
  });
});
