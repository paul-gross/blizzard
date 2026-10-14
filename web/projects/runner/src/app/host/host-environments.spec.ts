import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { HostEnvironments } from './host-environments';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

/** The host's runner list the holder names resolve against. */
const RUNNERS = { items: [{ runner_id: 'rn_1', name: 'r-claude', state: 'serving' }] };

/** Renders the container over one declared workspace, `ws-a`, holding `envs`. */
async function render(
  envs: readonly runnerApi.HostEnvironmentView[],
  workspaces: readonly runnerApi.HostWorkspaceView[] = [{ workspace_id: 'ws-a', max_environments: envs.length }],
): Promise<{ el: HTMLElement; fixture: ComponentFixture<HostEnvironments> }> {
  stub = stubRequestClient(runnerClient, (method, path) => {
    if (method !== 'GET') return {};
    if (path === '/api/host/environments') {
      return { workspaces: workspaces.map((ws) => ({ workspace_id: ws.workspace_id, items: [...envs] })) };
    }
    if (path === '/api/host/workspaces') return { items: [...workspaces] };
    if (path === '/api/host/runners') return RUNNERS;
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [HostEnvironments],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(HostEnvironments);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

describe('HostEnvironments', () => {
  it('heads each workspace with its environments in use of its capacity', async () => {
    const { el } = await render([
      { environment_id: 'r1', held: true, chunk_id: 'ch_01ABC', runner_id: 'rn_1', project: 'blizzard' },
      { environment_id: 'r2', held: false },
    ]);

    const heading = el.querySelector('[data-testid="workspace-row"]');
    expect(heading?.getAttribute('data-workspace-id')).toBe('ws-a');
    expect(heading?.querySelector('[data-testid="workspace-in-use"]')?.textContent?.trim()).toBe('1 / 2 in use');
  });

  it('names a held environment’s runner and project', async () => {
    const { el } = await render([
      { environment_id: 'r1', held: true, chunk_id: 'ch_01ABC', runner_id: 'rn_1', project: 'blizzard' },
    ]);

    expect(el.querySelector('[data-testid="env-holder"]')?.textContent?.trim()).toBe('r-claude · blizzard');
  });

  it('renders a held row with its chunk ref and how long it has been held', async () => {
    const { el } = await render([{ environment_id: 'r2', held: true, chunk_id: 'ch_01ABC', held_since: '2026-07-16T11:59:30.000Z' }]);

    const row = el.querySelector('[data-testid="env-row"]');
    expect(row?.getAttribute('data-held')).toBe('true');
    expect(row?.querySelector('.env')?.textContent).toBe('r2');
    expect(row?.querySelector('.chunk')?.textContent?.trim().length).toBeGreaterThan(0);
    expect(row?.querySelector('[data-testid="env-held-for"]')?.textContent?.trim().length).toBeGreaterThan(0);
  });

  it('renders an unused row with no chunk ref and no held-for text', async () => {
    const { el } = await render([{ environment_id: 'alpha', chunk_id: null, held_since: null }]);

    const row = el.querySelector('[data-testid="env-row"]');
    expect(row?.getAttribute('data-held')).toBe('false');
    expect(row?.querySelector('.chunk')?.textContent).toBe('');
    expect(row?.querySelector('[data-testid="env-held-for"]')?.textContent).toBe('');
  });

  it('renders the empty state only when the host declares no workspace and reports no environment', async () => {
    const { el } = await render([], []);

    expect(el.querySelector('[data-testid="env-empty"]')).not.toBeNull();
  });

  describe('held-for ticking', () => {
    afterEach(() => vi.restoreAllMocks());

    // `shouldAdvanceTime` keeps settle()'s macrotask resolvable while the ticking
    // interval is driven by advanceTimersByTimeAsync — the wait is a jump, not a
    // real second spent in the gating job.
    it('re-renders held-for at least once a second with no new data', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        const dateNow = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-07-16T12:00:00.000Z'));
        const { fixture } = await render([
          { environment_id: 'r2', held: true, chunk_id: 'ch_01ABC', held_since: '2026-07-16T11:59:30.000Z' },
        ]);
        const el = fixture.nativeElement as HTMLElement;
        expect(el.querySelector('[data-testid="env-held-for"]')?.textContent).toContain('30s');

        dateNow.mockReturnValue(Date.parse('2026-07-16T12:01:00.000Z'));
        await vi.advanceTimersByTimeAsync(1_100);
        fixture.detectChanges();

        expect(el.querySelector('[data-testid="env-held-for"]')?.textContent).toContain('1m');
      } finally {
        vi.useRealTimers();
      }
    });
  });
});
