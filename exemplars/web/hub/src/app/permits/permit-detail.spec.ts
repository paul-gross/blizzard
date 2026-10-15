// exemplar: bzh:frontend-empty-state-gated — the resting branch asserted beside the read it gates.

import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type PermitView, hubClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { PermitDetail } from './permit-detail';

const REVOKED: PermitView = {
  permit_id: 'pmt_1',
  project_id: 'prj_core',
  project_slug: 'core',
  project_name: null,
  holder: 'ada',
  capability: 'deploy',
  state: 'revoked',
  verbs: [],
  requested_at: '2026-10-01T09:00:00Z',
  requested_by: 'usr_grace',
  changed_at: '2026-10-03T09:00:00Z',
  changed_by: 'usr_grace',
  revocation_note: 'left the team',
};

type Answer = (method: string, path: string) => unknown;

describe('PermitDetail', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function mount(url: string, answer: Answer = () => REVOKED) {
    stub = stubRequestClient(hubClient, answer);
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'permits', component: PermitDetail },
          { path: 'permits/:permitId', component: PermitDetail },
        ]),
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return harness.routeNativeElement as HTMLElement;
  }

  it('rests on the empty state, reading nothing, while no permit is named', async () => {
    const el = await mount('/permits');

    expect(el.querySelector('[data-testid="permit-detail-empty"]')?.textContent).toContain('Pick a permit.');
    expect(el.querySelector('[data-testid="permit-detail-loading"]')).toBeNull();
    expect(stub.forRoute('/api/permits/pmt_1', 'GET')).toHaveLength(0);
  });

  it("shows the named permit's facts, its project by slug when the hub has no name", async () => {
    const el = await mount('/permits/pmt_1');

    const facts = el.querySelector('[data-testid="permit-detail-facts"]')?.textContent;
    expect(facts).toContain('core');
    expect(facts).toContain('left the team');
    expect(el.querySelector('[data-testid="permit-detail-state"]')?.textContent).toContain('revoked');
  });

  it('renders the error state when the permit is not found', async () => {
    const el = await mount('/permits/pmt_9', () => stubError(404, { detail: 'no permit pmt_9' }));

    expect(el.querySelector('[data-testid="permit-detail-error"]')).toBeTruthy();
  });
});
