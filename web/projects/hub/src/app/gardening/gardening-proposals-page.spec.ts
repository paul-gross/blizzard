import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type MeResponse } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { GardeningProposalsPage } from './gardening-proposals-page';

/** A read-only identity — every permission `OPERATOR_ME_RESPONSE` carries except
 * `chunk:control` — the default for tests unconcerned with the Pass/Accept gate. */
const VIEWER_ME_RESPONSE: MeResponse = {
  ...OPERATOR_ME_RESPONSE,
  permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'chunk:control'),
};

const WAITING_A = {
  proposal_id: 'gp_1',
  routine_name: 'comments',
  class: 'fix-the-source',
  title: 'Author a docstring standard',
  body: 'Seventeen modules narrate their own change history.',
  created_at: '2026-01-01T00:00:00Z',
  findings: ['fin_1', 'fin_2'],
  closure: null,
};

const WAITING_B = {
  proposal_id: 'gp_2',
  routine_name: 'comments',
  class: 'remediate',
  title: 'Delete the dead helper',
  body: 'Nothing calls it.',
  created_at: '2026-01-02T00:00:00Z',
  findings: ['fin_3'],
  closure: null,
};

const PASSED = {
  proposal_id: 'gp_3',
  routine_name: 'comments',
  class: 'fix-the-source',
  title: 'Rewrite the whole module',
  body: 'Too large for this pass.',
  created_at: '2026-01-03T00:00:00Z',
  findings: ['fin_4'],
  closure: {
    closure: 'passed',
    reason: 'not worth it yet',
    closed_by: 'u_1',
    closed_at: '2026-01-04T00:00:00Z',
    item_outcome: null,
    source: null,
    ref: null,
  },
};

const ACCEPTED_MINTED = {
  proposal_id: 'gp_4',
  routine_name: 'comments',
  class: 'fix-the-source',
  title: 'Extract the shared helper',
  body: 'Three call sites duplicate this logic.',
  created_at: '2026-01-05T00:00:00Z',
  findings: ['fin_5'],
  closure: {
    closure: 'accepted',
    reason: null,
    closed_by: 'u_1',
    closed_at: '2026-01-06T00:00:00Z',
    item_outcome: 'minted',
    source: 'hub',
    ref: '42',
  },
};

function findingFixture(findingId: string) {
  return {
    finding_id: findingId,
    routine_name: 'comments',
    scope_slug: 'blizzard',
    class: 'stale-docstring',
    locus: `src/${findingId}.py:1`,
    summary: `summary for ${findingId}`,
    state: 'live',
    live: true,
    observed_count: 1,
    last_seen_at: '2026-01-01T00:00:00Z',
  };
}

async function render(
  proposals: readonly unknown[] = [WAITING_A, WAITING_B, PASSED],
  me: MeResponse = VIEWER_ME_RESPONSE,
) {
  const stub = stubRequestClient(hubClient, (method, path) => {
    if (method === 'GET' && path === '/api/garden-proposals') return proposals;
    if (method === 'GET' && path === '/api/me') return me;
    if (method === 'GET' && path.startsWith('/api/findings/')) return findingFixture(path.split('/').pop()!);
    if (method === 'GET' && path === '/api/work-sources/hub/items/42') {
      return { source: 'hub', ref: '42', label: 'hub#42', web_url: '/board/chunk/ch_1', title: 't', body: 'b', author: { kind: 'user' }, closure: null, closed_at: null, created_at: '2026-01-01T00:00:00Z', edited_at: '2026-01-01T00:00:00Z', stated_priority: null };
    }
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [GardeningProposalsPage],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningProposalsPage);
  await settle(fixture, 6);
  return { fixture, stub, el: fixture.nativeElement as HTMLElement };
}

describe('GardeningProposalsPage', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('lists every waiting proposal by default, selecting the first', async () => {
    const rendered = await render();
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_3"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')?.classList).toContain(
      'pl-row--selected',
    );
  });

  it('shows a passed proposal once the waiting filter is switched to all, and it stays reachable', async () => {
    const rendered = await render();
    stub = rendered.stub;
    const { fixture, el } = rendered;

    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-filter-all"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_3"]')).toBeTruthy();
  });

  it('derives the class chips from the fetched data, never a hardcoded list', async () => {
    const rendered = await render();
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="gardening-proposal-class-all"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-class-fix-the-source"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-class-remediate"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-class-mechanize"]')).toBeNull();
  });

  it('filters the list down to one class', async () => {
    const rendered = await render();
    stub = rendered.stub;
    const { fixture, el } = rendered;

    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-class-remediate"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')).toBeTruthy();
  });

  it('renders the empty state only once the read resolves', async () => {
    const rendered = await render([]);
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="gardening-proposals-empty"]')).toBeTruthy();
  });

  it("renders the detail area's select-a-proposal rest state", async () => {
    const rendered = await render([]);
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="gardening-proposal-panel-empty"]')?.textContent).toContain(
      'Select a proposal',
    );
  });

  it("renders the selected proposal's case and its live-read evidence", async () => {
    const rendered = await render();
    stub = rendered.stub;
    const { fixture, el } = rendered;
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-proposal-case"]')?.textContent).toContain(
      'Author a docstring standard',
    );
    expect(el.querySelector('[data-testid="gardening-proposal-finding-fin_1"]')?.textContent).toContain(
      'summary for fin_1',
    );
    expect(el.querySelector('[data-testid="gardening-proposal-finding-fin_2"]')?.textContent).toContain(
      'summary for fin_2',
    );
  });

  it('resolves an accepted-and-minted work item through the closure pointer and shows its link on the finding row', async () => {
    const rendered = await render([ACCEPTED_MINTED]);
    stub = rendered.stub;
    const { fixture, el } = rendered;
    await settle(fixture);
    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-filter-all"]')?.click();
    await settle(fixture, 10);

    const closureEl = el.querySelector('[data-testid="gardening-proposal-closure-accepted-minted"]');
    const link = closureEl?.querySelector<HTMLAnchorElement>('[data-testid="gardening-proposal-work-item-link"]');
    expect(link?.textContent).toBe('hub#42');
    expect(link?.getAttribute('href')).toBe('/board/chunk/ch_1');
    expect(stub.forRoute('/api/work-sources/hub/items/42', 'GET').length).toBeGreaterThan(0);

    const findingLink = el.querySelector('[data-testid="gardening-proposal-finding-work-item-link-fin_5"]');
    expect(findingLink?.textContent).toBe('hub#42');
  });

  it('withholds Pass and Accept without chunk:control', async () => {
    const rendered = await render([WAITING_A], VIEWER_ME_RESPONSE);
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="gardening-proposal-actions"]')).toBeNull();
  });

  it('offers Pass and Accept for a waiting proposal with chunk:control', async () => {
    const rendered = await render([WAITING_A], OPERATOR_ME_RESPONSE);
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="gardening-proposal-pass"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-accept"]')).toBeTruthy();
  });

  it('opens the pass dialog off the panel Pass trigger, and closing it tears the dialog down', async () => {
    const rendered = await render([WAITING_A], OPERATOR_ME_RESPONSE);
    stub = rendered.stub;
    const { fixture, el } = rendered;

    expect(el.querySelector('[data-testid="gardening-proposal-pass-dialog"]')).toBeNull();
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-proposal-pass"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="proposal-pass-dialog-title"]')?.textContent).toContain(
      'Author a docstring standard',
    );

    el.querySelector<HTMLButtonElement>('[data-testid="proposal-pass-dialog-cancel"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-proposal-pass-dialog"]')).toBeNull();
  });

  it('opens the accept dialog off the panel Accept trigger, prefilled with the proposal body', async () => {
    const rendered = await render([WAITING_A], OPERATOR_ME_RESPONSE);
    stub = rendered.stub;
    const { fixture, el } = rendered;

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-proposal-accept"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="proposal-accept-dialog-title"]')?.textContent).toContain(
      'Author a docstring standard',
    );
    expect(el.querySelector<HTMLTextAreaElement>('[data-testid="proposal-accept-body-input"]')?.value).toBe(
      WAITING_A.body,
    );
  });
});
