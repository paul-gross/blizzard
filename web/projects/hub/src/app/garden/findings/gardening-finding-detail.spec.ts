import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type FindingDetailView } from 'fleet';
import {
  gardenFindingDetailView,
  gardenFindingFactView,
  gardenProposalClosureView,
  gardenProposalView,
  gardenWorkItemView,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';
import { BehaviorSubject } from 'rxjs';

import { GardeningFindingDetail } from './gardening-finding-detail';

type HubRoutes = Parameters<typeof stubHubClient>[0];

function findingFixture(overrides: Pick<FindingDetailView, 'state'> & Partial<FindingDetailView>): FindingDetailView {
  return gardenFindingDetailView({
    routine_name: 'nightly',
    scope_slug: 'blizzard',
    observed_count: 1,
    last_seen_at: '2026-01-05T00:00:00Z',
    introduced: '4ba7ef06d',
    facts: [gardenFindingFactView({ kind: 'add', recorded_at: '2026-01-01T00:00:00Z' })],
    live: overrides.state === 'live',
    ...overrides,
  });
}

const FINDING_LIVE = findingFixture({
  finding_id: 'fnd_10',
  class: 'stale-docstring',
  locus: 'a.py:1',
  summary: 'summary a',
  state: 'live',
});

const PROPOSAL_ACCEPTED_MINTED = gardenProposalView({
  proposal_id: 'gp_1',
  routine_name: 'nightly',
  class: 'stale-docstring',
  title: 'Extract the shared helper',
  body: 'Three call sites duplicate this logic.',
  created_at: '2026-01-01T00:00:00Z',
  findings: ['fnd_10'],
  closure: gardenProposalClosureView({
    closure: 'accepted',
    reason: null,
    closed_by: 'u_1',
    closed_at: '2026-01-02T00:00:00Z',
    item_outcome: 'minted',
    source: 'hub',
    ref: '42',
  }),
});

const WORK_ITEM_42 = gardenWorkItemView({ source: 'hub', ref: '42', label: 'hub#42', web_url: '/board/chunk/ch_9' });

/**
 * Exercises the `/gardening/findings` detail child — the selected finding's own
 * panel and the triage it dispatches. The filtered list beside it, and its
 * agreement with the URL, are `gardening-findings-page.spec.ts`'s.
 * `FleetFindingPanel` owns its own rendering and is covered in its own spec; this
 * spec proves the container wires the right view model to it and resolves
 * `KitAsyncStateValue` correctly.
 */
describe('GardeningFindingDetail', () => {
  let stub: RequestClientStub;
  let paramMap$: BehaviorSubject<ReturnType<typeof convertToParamMap>>;

  afterEach(() => stub?.restore());

  async function mount(findingId: string | null, opts: { routes?: HubRoutes } = {}) {
    paramMap$ = new BehaviorSubject(convertToParamMap(findingId === null ? {} : { findingId }));
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/findings': () => ({ findings: [], next_cursor: null }),
      'GET /api/garden-proposals': () => ({ proposals: [], next_cursor: null }),
      ...opts.routes,
    });
    await TestBed.configureTestingModule({
      imports: [GardeningFindingDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        // A real `Router` (`provideRouter([])`), not a bare stub — the panel's own
        // work-item link resolves its `href` through one.
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: paramMap$ } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningFindingDetail);
    await settle(fixture, 12);
    return { fixture };
  }

  it('shows its own empty state on the bare child route, selecting nothing', async () => {
    const { fixture } = await mount(null);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-finding-panel-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-finding-panel"]')).toBeNull();
  });

  it('mounts the finding panel for the findingId in the route param', async () => {
    const { fixture } = await mount('fnd_10', {
      routes: { 'GET /api/findings/{finding_id}': () => FINDING_LIVE },
    });
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-finding-panel"]');
    expect(panel?.textContent).toContain('stale-docstring');
    expect(panel?.textContent).toContain('summary a');
    // `introduced` is a git revision, never a timestamp (`finding-panel.ts`'s own
    // doc comment) — rendered plain, never through `fleet-when`.
    expect(panel?.querySelector('[data-testid="fp-introduced"]')?.textContent).toContain('4ba7ef06d');
  });

  it('renders the finding panel’s fact timeline in the wire order, including a noteless fact', async () => {
    const findingWithFacts = findingFixture({
      finding_id: 'fnd_11',
      class: 'stale-docstring',
      locus: 'a.py:1',
      summary: 'summary a',
      state: 'resolved',
      facts: [
        gardenFindingFactView({ kind: 'add', recorded_at: '2026-01-01T00:00:00Z' }),
        gardenFindingFactView({ kind: 'observed', recorded_at: '2026-01-02T00:00:00Z' }),
        gardenFindingFactView({ kind: 'resolved', recorded_at: '2026-01-03T00:00:00Z', note: 'fixed', actor: 'u_1' }),
      ],
    });
    const { fixture } = await mount('fnd_11', {
      routes: { 'GET /api/findings/{finding_id}': () => findingWithFacts },
    });
    const el = fixture.nativeElement as HTMLElement;

    const timeline = el.querySelector('[data-testid="fp-timeline"]')!;
    const rows = timeline.querySelectorAll('[data-testid="finding-fact-row"]');
    expect(rows.length).toBe(3);
    expect(Array.from(rows).map((r) => r.querySelector('[data-testid="finding-fact-kind"]')?.textContent)).toEqual([
      'Added',
      'Observed',
      'Resolved',
    ]);
    expect(rows[1].querySelector('[data-testid="finding-fact-note"]')).toBeNull();
    expect(rows[2].querySelector('[data-testid="finding-fact-note"]')?.textContent).toBe('fixed');
  });

  it('surfaces an unknown findingId as the panel’s error state, not as “nothing selected”', async () => {
    const { fixture } = await mount('fnd_missing', {
      routes: { 'GET /api/findings/{finding_id}': () => stubError(404, { detail: 'unknown finding' }) },
    });

    // The URL names a finding, so an empty state here would be a lie: it reads as
    // "select a finding" while one is selected and simply could not be read. The
    // single-finding read exists to keep the two distinguishable.
    const panel = fixture.debugElement.query(By.css('app-finding-panel'));
    expect(panel.componentInstance.state()).toBe('error');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="gardening-finding-panel-empty"]')).toBeNull();
  });

  it('surfaces a failed read as an error state for any status, not only 404', async () => {
    const { fixture } = await mount('fnd_10', {
      routes: { 'GET /api/findings/{finding_id}': () => stubError(500, { detail: 'boom' }) },
    });

    const panel = fixture.debugElement.query(By.css('app-finding-panel'));
    expect(panel.componentInstance.state()).toBe('error');
  });

  it('re-reads the panel when the findingId param changes without remounting the pane', async () => {
    const { fixture } = await mount('fnd_10', {
      routes: { 'GET /api/findings/{finding_id}': () => FINDING_LIVE },
    });
    const paneInstance = fixture.componentInstance;

    paramMap$.next(convertToParamMap({}));
    await settle(fixture);

    expect(fixture.componentInstance).toBe(paneInstance);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-finding-panel-empty"]')).toBeTruthy();
  });

  it('resolves an accepted-and-minted proposal work item onto the finding panel', async () => {
    const { fixture } = await mount('fnd_10', {
      routes: {
        'GET /api/findings/{finding_id}': () => FINDING_LIVE,
        'GET /api/garden-proposals': () => ({ proposals: [PROPOSAL_ACCEPTED_MINTED], next_cursor: null }),
        'GET /api/work-sources/{source}/items/{ref}': () => WORK_ITEM_42,
      },
    });
    await settle(fixture, 8);
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-finding-panel"]');
    const link = panel?.querySelector<HTMLAnchorElement>('[data-testid="fp-work-item-link"]');
    expect(link?.textContent).toBe('hub#42');
    expect(link?.getAttribute('href')).toBe('/board/chunk/ch_9');
  });

  describe('single-finding triage from the panel', () => {
    it('opens the triage dialog when the finding panel emits triage', async () => {
      const { fixture } = await mount('fnd_10', {
        routes: { 'GET /api/findings/{finding_id}': () => FINDING_LIVE },
      });
      const el = fixture.nativeElement as HTMLElement;

      const panel = fixture.debugElement.query(By.css('app-finding-panel'));
      panel.componentInstance.triage.emit('resolve');
      await settle(fixture);

      const dialog = el.querySelector('[data-testid="gardening-finding-triage-dialog"]');
      expect(dialog).toBeTruthy();
      expect(dialog?.textContent).toContain('Resolve finding');
      // The dialog opens on what the verb actually does, above the note field.
      expect(dialog?.querySelector('[data-testid="finding-triage-blurb"]')?.textContent).toContain(
        'has landed',
      );
    });

    it('forwards chunk:control to the finding panel, withholding its triage actions for a read-only identity', async () => {
      const { fixture } = await mount('fnd_10', {
        routes: {
          'GET /api/findings/{finding_id}': () => FINDING_LIVE,
          'GET /api/me': () => ({
            ...OPERATOR_ME_RESPONSE,
            permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'chunk:control'),
          }),
        },
      });
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="gardening-finding-panel-actions"]')).toBeNull();
    });

    it('closes the triage dialog again on (closed)', async () => {
      const { fixture } = await mount('fnd_10', {
        routes: { 'GET /api/findings/{finding_id}': () => FINDING_LIVE },
      });
      const el = fixture.nativeElement as HTMLElement;

      const panel = fixture.debugElement.query(By.css('app-finding-panel'));
      panel.componentInstance.triage.emit('resolve');
      await settle(fixture);
      expect(el.querySelector('[data-testid="gardening-finding-triage-dialog"]')).toBeTruthy();

      const dialogComponent = fixture.debugElement.query(By.css('app-gardening-finding-triage-dialog'));
      dialogComponent.componentInstance.closed.emit();
      await settle(fixture);

      expect(el.querySelector('[data-testid="gardening-finding-triage-dialog"]')).toBeNull();
    });
  });
});
