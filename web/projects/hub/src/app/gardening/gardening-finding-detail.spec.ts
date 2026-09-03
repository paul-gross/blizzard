import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { BehaviorSubject } from 'rxjs';

import { GardeningFindingDetail } from './gardening-finding-detail';

function findingFixture(overrides: { state: string } & Record<string, unknown>) {
  return {
    routine_name: 'nightly',
    scope_slug: 'blizzard',
    observed_count: 1,
    last_seen_at: '2026-01-05T00:00:00Z',
    introduced: '4ba7ef06d',
    note: null,
    live: overrides.state === 'live',
    ...overrides,
  };
}

const FINDING_LIVE = findingFixture({
  finding_id: 'fnd_10',
  class: 'stale-docstring',
  locus: 'a.py:1',
  summary: 'summary a',
  state: 'live',
});

const PROPOSAL_ACCEPTED_MINTED = {
  proposal_id: 'gp_1',
  routine_name: 'nightly',
  class: 'stale-docstring',
  title: 'Extract the shared helper',
  body: 'Three call sites duplicate this logic.',
  created_at: '2026-01-01T00:00:00Z',
  findings: ['fnd_10'],
  closure: {
    closure: 'accepted',
    reason: null,
    closed_by: 'u_1',
    closed_at: '2026-01-02T00:00:00Z',
    item_outcome: 'minted',
    source: 'hub',
    ref: '42',
  },
};

const WORK_ITEM_42 = {
  source: 'hub',
  ref: '42',
  label: 'hub#42',
  web_url: '/board/chunk/ch_9',
  title: 't',
  body: 'b',
  author: { kind: 'user' },
  closure: null,
  closed_at: null,
  created_at: '2026-01-01T00:00:00Z',
  edited_at: '2026-01-01T00:00:00Z',
  stated_priority: null,
};

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

  async function mount(findingId: string | null, opts: { routeOverride?: (method: string, path: string) => unknown } = {}) {
    paramMap$ = new BehaviorSubject(convertToParamMap(findingId === null ? {} : { findingId }));
    stub = stubRequestClient(hubClient, (method, path) => {
      const overridden = opts.routeOverride?.(method, path);
      if (overridden !== undefined) return overridden;
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/findings') return [];
      if (method === 'GET' && path === '/api/garden-proposals') return [];
      return {};
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
      routeOverride: (method, path) => (method === 'GET' && path === '/api/findings/fnd_10' ? FINDING_LIVE : undefined),
    });
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-finding-panel"]');
    expect(panel?.textContent).toContain('stale-docstring');
    expect(panel?.textContent).toContain('summary a');
    // `introduced` is a git revision, never a timestamp (`finding-panel.ts`'s own
    // doc comment) — rendered plain, never through `fleet-when`.
    expect(panel?.querySelector('[data-testid="fp-introduced"]')?.textContent).toContain('4ba7ef06d');
  });

  it('surfaces an unknown findingId as the panel’s error state, not as “nothing selected”', async () => {
    const { fixture } = await mount('fnd_missing', {
      routeOverride: (method, path) =>
        method === 'GET' && path === '/api/findings/fnd_missing' ? stubError(404, { detail: 'unknown finding' }) : undefined,
    });

    // The URL names a finding, so an empty state here would be a lie: it reads as
    // "select a finding" while one is selected and simply could not be read. The
    // single-finding read exists to keep the two distinguishable.
    const panel = fixture.debugElement.query(By.css('fleet-finding-panel'));
    expect(panel.componentInstance.state()).toBe('error');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="gardening-finding-panel-empty"]')).toBeNull();
  });

  it('surfaces a failed read as an error state for any status, not only 404', async () => {
    const { fixture } = await mount('fnd_10', {
      routeOverride: (method, path) =>
        method === 'GET' && path === '/api/findings/fnd_10' ? stubError(500, { detail: 'boom' }) : undefined,
    });

    const panel = fixture.debugElement.query(By.css('fleet-finding-panel'));
    expect(panel.componentInstance.state()).toBe('error');
  });

  it('re-reads the panel when the findingId param changes without remounting the pane', async () => {
    const { fixture } = await mount('fnd_10', {
      routeOverride: (method, path) => (method === 'GET' && path === '/api/findings/fnd_10' ? FINDING_LIVE : undefined),
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
      routeOverride: (method, path) => {
        if (method === 'GET' && path === '/api/findings/fnd_10') return FINDING_LIVE;
        if (method === 'GET' && path === '/api/garden-proposals') return [PROPOSAL_ACCEPTED_MINTED];
        if (method === 'GET' && path === '/api/work-sources/hub/items/42') return WORK_ITEM_42;
        return undefined;
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
        routeOverride: (method, path) => (method === 'GET' && path === '/api/findings/fnd_10' ? FINDING_LIVE : undefined),
      });
      const el = fixture.nativeElement as HTMLElement;

      const panel = fixture.debugElement.query(By.css('fleet-finding-panel'));
      panel.componentInstance.triage.emit('resolve');
      await settle(fixture);

      const dialog = el.querySelector('[data-testid="gardening-finding-triage-dialog"]');
      expect(dialog).toBeTruthy();
      expect(dialog?.textContent).toContain('Resolve 1 finding');
    });

    it('forwards chunk:control to the finding panel, withholding its triage actions for a read-only identity', async () => {
      const { fixture } = await mount('fnd_10', {
        routeOverride: (method, path) => {
          if (method === 'GET' && path === '/api/findings/fnd_10') return FINDING_LIVE;
          if (method === 'GET' && path === '/api/me') {
            return { ...OPERATOR_ME_RESPONSE, permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'chunk:control') };
          }
          return undefined;
        },
      });
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="gardening-finding-panel-actions"]')).toBeNull();
    });

    it('closes the triage dialog again on (closed)', async () => {
      const { fixture } = await mount('fnd_10', {
        routeOverride: (method, path) => (method === 'GET' && path === '/api/findings/fnd_10' ? FINDING_LIVE : undefined),
      });
      const el = fixture.nativeElement as HTMLElement;

      const panel = fixture.debugElement.query(By.css('fleet-finding-panel'));
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
