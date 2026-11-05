import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { FLEET_CLOCK, type FleetSpendView } from 'fleet';
import { fleetSpendView, type RequestClientStub, settle, stubError, stubHubClient, type StubDetailBody, type StubHttpError } from 'fleet/testing';
import { FleetSpendPanel } from './fleet-spend-panel';

describe('FleetSpendPanel', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  async function mount(answer: (search: string) => FleetSpendView | StubHttpError<number, StubDetailBody>) {
    stub = stubHubClient({ 'GET /api/spend': (request) => answer(request.search) });
    await TestBed.configureTestingModule({
      imports: [FleetSpendPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: FLEET_CLOCK, useValue: () => new Date(2026, 6, 16, 12, 0, 0).getTime() },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetSpendPanel);
    await settle(fixture);
    return fixture.nativeElement as HTMLElement;
  }

  it('names itself Fleet spend and stacks Today over Yesterday', async () => {
    const el = await mount(() => fleetSpendView());

    expect(el.querySelector('[data-testid="fleet-spend-panel"]')?.getAttribute('aria-label')).toBe('Fleet spend');
    const rows = el.querySelector('[data-testid="fleet-spend-rows"]');
    expect(rows?.classList).toContain('stacked');
    expect([...(rows?.querySelectorAll('.label') ?? [])].map((l) => l.textContent)).toEqual(['Today', 'Yesterday']);
  });

  it('reads today from local midnight, and yesterday from the midnight before up to it', async () => {
    await mount(() => fleetSpendView());

    const today = new Date(2026, 6, 16).toISOString();
    const yesterday = new Date(2026, 6, 15).toISOString();
    const reads = stub.forRoute('/api/spend', 'GET').map((r) => new URLSearchParams(r.search));
    expect(reads.some((q) => q.get('since') === today && !q.has('until'))).toBe(true);
    expect(reads.some((q) => q.get('since') === yesterday && q.get('until') === today)).toBe(true);
  });

  it('says FAILED TO LOAD when the today read fails', async () => {
    const el = await mount(() => stubError(500, { detail: 'boom' }));

    expect(el.querySelector('[data-testid="fleet-spend-error"]')?.textContent).toContain('FAILED TO LOAD');
    expect(el.querySelector('[data-testid="fleet-spend-rows"]')).toBeNull();
  });
});
