import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type MeResponse } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { BehaviorSubject } from 'rxjs';
import { vi } from 'vitest';

import { GardeningScopeDetail } from './gardening-scope-detail';

/** A read-only identity — every permission `OPERATOR_ME_RESPONSE` carries except
 * `graph:edit` — the default for tests unconcerned with the scope panel's gated
 * description-editor/retire/enable controls. */
const VIEWER_ME_RESPONSE: MeResponse = {
  ...OPERATOR_ME_RESPONSE,
  permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'graph:edit'),
};

const SCOPE = { slug: 'blizzard', description: 'the blizzard monorepo', retired: false, created_at: '2026-01-01T00:00:00Z' };

const ROUTINE = {
  routine_id: 'rtn_1',
  name: 'nightly',
  graph_name: 'garden-routine',
  default_scope_slug: 'blizzard',
  default_model: ['claude-sonnet-5'],
  default_effort: 'medium',
  created_at: '2026-01-01T00:00:00Z',
};

/**
 * Exercises the `/gardening/scopes` detail child — the selected scope's
 * description, lifecycle, and defaulting-routines readout. The list beside it and
 * the selection's own route wiring are `gardening-scopes-page.spec.ts`'s;
 * everything routine-shaped is `gardening-routines-page.spec.ts`'s. This pane
 * still reads the routines list (`defaultingRoutineNames` needs it), so every
 * fixture below still stubs `/api/routines`.
 */
describe('GardeningScopeDetail', () => {
  let stub: RequestClientStub;

  afterEach(() => stub?.restore());

  async function render(
    opts: {
      scopes?: readonly unknown[];
      routines?: readonly unknown[];
      me?: MeResponse;
      routeOverride?: (method: string, path: string) => unknown;
      params?: Record<string, string>;
    } = {},
  ) {
    const scopes = opts.scopes ?? [SCOPE];
    const routines = opts.routines ?? [ROUTINE];
    const me = opts.me ?? VIEWER_ME_RESPONSE;
    stub = stubRequestClient(hubClient, (method, path) => {
      const overridden = opts.routeOverride?.(method, path);
      if (overridden !== undefined) return overridden;
      if (method === 'GET' && path === '/api/scopes') return scopes;
      if (method === 'GET' && path === '/api/routines') return routines;
      if (method === 'GET' && path === '/api/me') return me;
      return {};
    });
    const paramMap$ = new BehaviorSubject(convertToParamMap(opts.params ?? {}));
    await TestBed.configureTestingModule({
      imports: [GardeningScopeDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: ActivatedRoute, useValue: { paramMap: paramMap$ } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningScopeDetail);
    await settle(fixture, 12);
    return fixture;
  }

  it('shows its own empty state on the bare child route, selecting nothing', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel"]')).toBeNull();
  });

  it('a scopeSlug param naming an unknown scope resolves to the empty state, not a stale panel', async () => {
    const fixture = await render({ params: { scopeSlug: 'ghost' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel"]')).toBeNull();
  });

  it('shows the selected scope description as plain text, without an editor, for a read-only identity', async () => {
    const fixture = await render({ params: { scopeSlug: 'blizzard' } });
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-scope-panel"]');
    expect(panel?.textContent).toContain('blizzard');
    expect(panel?.textContent).toContain('the blizzard monorepo');
    expect(el.querySelector('[data-testid="gardening-scope-panel-description-input"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-scope-panel-retire"]')).toBeNull();
  });

  it('lists the routines defaulting to the selected scope', async () => {
    const fixture = await render({ params: { scopeSlug: 'blizzard' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-routines"]')?.textContent).toContain('nightly');
  });

  it('shows the description editor and lifecycle control for an identity with graph:edit', async () => {
    const fixture = await render({ me: OPERATOR_ME_RESPONSE, params: { scopeSlug: 'blizzard' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-description-input"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel-retire"]')).toBeTruthy();
  });

  it('submits an edited description through PATCH /api/scopes/{slug}', async () => {
    const fixture = await render({ me: OPERATOR_ME_RESPONSE, params: { scopeSlug: 'blizzard' } });
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="gardening-scope-panel-description-input"]')!;
    input.value = 'updated description';
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-description-submit"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/scopes/blizzard', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ description: 'updated description' });
  });

  it('retires a scope through POST /api/scopes/{slug}/retire once confirmed', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await render({ me: OPERATOR_ME_RESPONSE, params: { scopeSlug: 'blizzard' } });
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/scopes/blizzard/retire', 'POST')).toHaveLength(1);
    confirmSpy.mockRestore();
  });

  it('reports a failed edit through the panel action-error line rather than swallowing it', async () => {
    const fixture = await render({
      me: OPERATOR_ME_RESPONSE,
      params: { scopeSlug: 'blizzard' },
      routeOverride: (method, path) =>
        method === 'PATCH' && path === '/api/scopes/blizzard'
          ? stubError(404, { detail: 'unknown scope blizzard' })
          : undefined,
    });
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="gardening-scope-panel-description-input"]')!;
    input.value = 'updated description';
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-description-submit"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-scope-panel-error"]')?.textContent).toContain(
      'unknown scope blizzard',
    );
  });
});
