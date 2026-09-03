import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { GardeningPage } from './gardening-page';

describe('GardeningPage (blizzard#397)', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function render(proposals: readonly unknown[]) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') return proposals;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [GardeningPage],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningPage);
    await settle(fixture);
    return fixture;
  }

  it('renders the five deep-linkable sub-tabs, in order', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-tab-scopes"]')?.textContent).toContain('Scopes');
    expect(el.querySelector('[data-testid="gardening-tab-routines"]')?.textContent).toContain('Routines');
    expect(el.querySelector('[data-testid="gardening-tab-runs"]')?.textContent).toContain('Runs');
    expect(el.querySelector('[data-testid="gardening-tab-findings"]')?.textContent).toContain('Findings');
    expect(el.querySelector('[data-testid="gardening-tab-proposals"]')?.textContent).toContain('Proposals');
    const tabs = Array.from(el.querySelectorAll('[fleetKitTab]')).map((t) => t.getAttribute('data-testid'));
    expect(tabs).toEqual([
      'gardening-tab-scopes',
      'gardening-tab-routines',
      'gardening-tab-runs',
      'gardening-tab-findings',
      'gardening-tab-proposals',
    ]);
  });

  it('omits the proposals count when nothing is waiting', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposals-count"]')).toBeNull();
  });

  it('carries the waiting count on Proposals only, and no count on the other four tabs', async () => {
    const fixture = await render([
      { proposal_id: 'gp_1', routine_name: 'comments', class: 'x', body: 'b', created_at: '2026-01-01T00:00:00Z', findings: [] },
      {
        proposal_id: 'gp_2',
        routine_name: 'comments',
        class: 'x',
        body: 'b',
        created_at: '2026-01-01T00:00:00Z',
        findings: [],
        closure: { closure: 'passed', reason: 'r', closed_by: 'u', closed_at: '2026-01-02T00:00:00Z', item_outcome: null, source: null, ref: null },
      },
    ]);
    const el = fixture.nativeElement as HTMLElement;

    // Only gp_1 (no closure) is still waiting.
    expect(el.querySelector('[data-testid="gardening-proposals-count"]')?.textContent).toBe('1');
    expect(el.querySelector('[data-testid="gardening-tab-scopes"] .badge')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-tab-routines"] .badge')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-tab-runs"] .badge')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-tab-findings"] .badge')).toBeNull();
  });
});
