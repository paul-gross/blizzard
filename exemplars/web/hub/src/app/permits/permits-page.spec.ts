// exemplar: bzh:frontend-pending-override, bzh:frontend-open-vocabulary — the page mounted on its route, the hub answered by a request stub.

import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type MeResponse, type PermitState, type PermitVerb, type PermitView, hubClient } from 'fleet';
import { HUB_PROJECT_FILTER } from 'fleet/shell';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { PermitsPage } from './permits-page';

const REQUESTED: PermitView = {
  permit_id: 'pmt_1',
  project_id: 'prj_core',
  project_slug: 'core',
  project_name: 'Core',
  holder: 'ada',
  capability: 'deploy',
  state: 'requested',
  verbs: ['grant', 'revoke'],
  requested_at: '2026-10-01T09:00:00Z',
  requested_by: 'usr_grace',
  changed_at: null,
  changed_by: null,
  revocation_note: null,
};

const SUSPENDED: PermitView = { ...REQUESTED, permit_id: 'pmt_2', capability: 'release', state: 'suspended', verbs: ['resume', 'revoke'] };

const EDITOR: MeResponse = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };

type Answer = (method: string, path: string) => unknown;

const listing: Answer = (_method, path) =>
  path === '/api/permits' ? { permits: [REQUESTED, SUSPENDED], next_cursor: null } : {};

describe('PermitsPage', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function mount(url: string, answer: Answer = listing, lens: string | null = null, me: MeResponse = EDITOR) {
    stub = stubRequestClient(hubClient, (method, path) => (path === '/api/me' ? me : answer(method, path)));
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'permits', component: PermitsPage }]),
        { provide: HUB_PROJECT_FILTER, useValue: signal(lens).asReadonly() },
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { harness, el: harness.routeNativeElement as HTMLElement };
  }

  function row(el: HTMLElement, permitId: string): HTMLElement {
    return el.querySelector<HTMLElement>(`[data-permit-id="${permitId}"]`)!;
  }

  it("lists the lensed project's permits, each labelled with its project", async () => {
    const { el } = await mount('/permits', listing, 'core');

    expect(el.querySelectorAll('[data-testid="permits-row"]')).toHaveLength(2);
    expect(row(el, 'pmt_1').querySelector('[data-testid="permits-project"]')?.textContent).toContain('Core');
    expect(stub.forRoute('/api/permits', 'GET')[0].search).toBe('?project=core&limit=1000');
  });

  it("narrows the listing to the URL's holder", async () => {
    await mount('/permits?holder=ada');

    expect(stub.forRoute('/api/permits', 'GET')[0].search).toBe('?holder=ada&limit=1000');
  });

  it('follows next_cursor until the listing is whole', async () => {
    const pages = [
      { permits: [REQUESTED], next_cursor: 'c1' },
      { permits: [SUSPENDED], next_cursor: null },
    ];
    const { el } = await mount('/permits', (_method, path) => (path === '/api/permits' ? pages.shift() : {}));

    expect(el.querySelectorAll('[data-testid="permits-row"]')).toHaveLength(2);
    expect(stub.forRoute('/api/permits', 'GET')[1].search).toBe('?cursor=c1&limit=1000');
  });

  it('shows the empty state for a listing with no permits', async () => {
    const { el } = await mount('/permits', () => ({ permits: [], next_cursor: null }));

    expect(el.querySelector('[data-testid="permits-empty"]')?.textContent).toContain('No permits.');
  });

  it('renders the error state when the listing is refused', async () => {
    const { el } = await mount('/permits', () => stubError(403, { detail: "missing permission 'fleet:view'" }));

    expect(el.querySelector('[data-testid="permits-error"]')).toBeTruthy();
  });

  it('offers a viewer no verb controls', async () => {
    const { el } = await mount('/permits', listing, null, OPERATOR_ME_RESPONSE);

    expect(el.querySelectorAll('[data-testid="permits-row"]')).toHaveLength(2);
    expect(el.querySelector('[data-testid="permits-grant"]')).toBeNull();
    expect(el.querySelector('[data-testid="permits-revoke"]')).toBeNull();
  });

  it('shows a state a newer hub sends raw, and offers no control for a verb this build does not know', async () => {
    const lapsed: PermitView = { ...REQUESTED, state: 'lapsed' as PermitState, verbs: ['renew' as PermitVerb, 'grant', 'revoke'] };
    const { el } = await mount('/permits', (_method, path) => (path === '/api/permits' ? { permits: [lapsed], next_cursor: null } : {}));

    expect(row(el, 'pmt_1').querySelector('[data-testid="permits-state"]')?.textContent).toContain('lapsed');
    expect(row(el, 'pmt_1').querySelector('[data-testid="permits-grant"]')).toBeTruthy();
    expect(row(el, 'pmt_1').querySelector('[data-testid="permits-renew"]')).toBeNull();
  });

  it("disables only the acted-on row's controls while its verb is in flight", async () => {
    let answerGrant: () => void = () => undefined;
    const { harness, el } = await mount('/permits', (method, path) =>
      path === '/api/permits/pmt_1/grant'
        ? new Promise((resolve) => (answerGrant = () => resolve({ ...REQUESTED, state: 'granted' })))
        : listing(method, path),
    );

    row(el, 'pmt_1').querySelector<HTMLElement>('[data-testid="permits-grant"]')!.click();
    await vi.waitFor(() => {
      harness.fixture.detectChanges();
      expect(row(el, 'pmt_1').querySelector('[data-testid="permits-state"]')?.textContent).toContain('granted');
    });

    expect(row(el, 'pmt_1').querySelector('[data-testid="permits-revoke"]')?.hasAttribute('disabled')).toBe(true);
    expect(row(el, 'pmt_2').querySelector('[data-testid="permits-resume"]')?.hasAttribute('disabled')).toBe(false);
    answerGrant();
    await settle(harness.fixture);
  });

  it('surfaces a refused verb instead of appearing to succeed', async () => {
    const { harness, el } = await mount('/permits', (method, path) =>
      path === '/api/permits/pmt_2/resume'
        ? stubError(409, { detail: 'permit pmt_2 is granted — resume refused' })
        : listing(method, path),
    );

    row(el, 'pmt_2').querySelector<HTMLElement>('[data-testid="permits-resume"]')!.click();
    await settle(harness.fixture);

    expect(el.querySelector('[data-testid="permits-action-error"]')?.textContent).toContain('resume refused');
  });

  it('revokes with the note the dialog was given', async () => {
    const { harness, el } = await mount('/permits');

    row(el, 'pmt_1').querySelector<HTMLElement>('[data-testid="permits-revoke"]')!.click();
    await settle(harness.fixture);
    const note = document.querySelector<HTMLInputElement>('[data-testid="permits-revoke-note"]')!;
    note.value = 'left the team';
    note.dispatchEvent(new Event('input'));
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="permits-revoke-confirm"]')!.click();
    await settle(harness.fixture);

    expect(stub.forRoute('/api/permits/pmt_1/revoke', 'POST')[0]?.body).toEqual({ note: 'left the team' });
  });
});
