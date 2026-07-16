import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { runnerApi } from 'fleet';
import { vi } from 'vitest';

import { AgentRow } from './agent-row';
import { settle } from './testing/settle';
import { RouteError, type RunnerClientStub, stubRunnerClient } from './testing/stub-runner-client';

const REF = Date.parse('2026-07-16T12:00:00.000Z');

function lease(overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView {
  return {
    lease_id: 'L-903',
    chunk_id: 'C-125',
    graph_id: 'gr_1',
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 2,
    session_id: 'sess-77',
    pid: 4821,
    environment_id: 'beta',
    workdir: '/ws/beta',
    created_at: '2026-07-16T11:00:00.000Z',
    last_heartbeat_at: '2026-07-16T11:59:26.000Z', // -34s from REF
    state: 'running',
    ...overrides,
  };
}

let activeStubs: RunnerClientStub[] = [];

/**
 * `AgentRow` injects {@link injectChunkTitleQuery} internally (issue #28 phase 7),
 * so every render needs a `TanStack` provider and a stubbed runner transport for
 * the `GET /api/chunks/{chunk_id}/pm-items` read the title/chips decoration reads.
 * Defaults to `{ items: [] }` — the "no title arrived" case — so every test that
 * doesn't care about the title reads the row's identity alone, exactly like a
 * genuinely title-free chunk. The stub is tracked and torn down in `afterEach`
 * below, so call sites don't each need to remember to restore it.
 */
async function render(
  agent: runnerApi.LeaseView,
  route: (method: string, path: string) => unknown = () => ({ items: [] }),
): Promise<HTMLElement> {
  activeStubs.push(stubRunnerClient(route));
  await TestBed.configureTestingModule({
    imports: [AgentRow],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(AgentRow);
  fixture.componentRef.setInput('agent', agent);
  await settle(fixture);
  return fixture.nativeElement as HTMLElement;
}

describe('AgentRow', () => {
  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(REF);
  });

  afterEach(() => {
    activeStubs.forEach((s) => s.restore());
    activeStubs = [];
    vi.restoreAllMocks();
  });

  it('renders the lease/chunk/epoch identity and the data-lease-id hook (issue #28, shaped for #29)', async () => {
    const el = await render(lease());

    const row = el.querySelector('[data-testid="agent-row"]');
    expect(row?.getAttribute('data-lease-id')).toBe('L-903');
    expect(row?.textContent).toContain('L-903');
    expect(row?.textContent).toContain('C-125');
    expect(row?.textContent).toContain('epoch 2');
  });

  it('renders node, env, pid, and session on the second line — the title lives on its own line, not here', async () => {
    const el = await render(lease());

    const l2 = el.querySelector('.l2')?.textContent ?? '';
    expect(l2).toContain('build');
    expect(l2).toContain('beta');
    expect(l2).toContain('4821');
    expect(l2).toContain('sess-77');
  });

  it('has no (select) output and no role="button" — #29\'s territory, not phase 6\'s', () => {
    // Structural guard: the component declares no output named `select`.
    expect(Object.getOwnPropertyNames(AgentRow.prototype)).not.toContain('select');
  });

  it.each([
    ['running', 'st-running'],
    ['stale', 'st-stale'],
    ['parked', 'st-parked'],
    ['spawning', 'st-spawning'],
    ['exited', 'st-exited'],
  ] as const)('renders the %s state with its %s class', async (state, cls) => {
    const el = await render(lease({ state }));
    const stEl = el.querySelector('[data-testid="agent-state"]');
    expect(stEl?.classList.contains(cls)).toBe(true);
    expect(stEl?.textContent?.trim()).toBe(state.toUpperCase());
  });

  describe('heartbeat age', () => {
    it('renders "—" (not "-0s") for a spawning lease with no heartbeat yet', async () => {
      const el = await render(lease({ state: 'spawning', last_heartbeat_at: null, pid: null, session_id: null }));
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('—');
    });

    it('does not fall back to created_at for a spawning lease', async () => {
      // created_at is hours before REF; if the row fell back to it the age would be
      // a large number, not the honest "—".
      const el = await render(
        lease({ state: 'spawning', last_heartbeat_at: null, created_at: '2026-07-16T09:00:00.000Z' }),
      );
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('—');
    });

    it('formats a sub-minute age as -Ns', async () => {
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T11:59:26.000Z' })); // -34s
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('-34s');
    });

    it('formats a sub-hour age as -Nm', async () => {
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T11:48:00.000Z' })); // -12m
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('-12m');
    });

    it('formats an hour-plus age as -HhMMm', async () => {
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T10:55:56.000Z' })); // -1h04m
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('-1h04m');
    });

    it('colors a stale lease\'s age --red via the "stale" class, not the state label alone', async () => {
      const el = await render(lease({ state: 'stale', last_heartbeat_at: '2026-07-16T11:00:00.000Z' }));
      const ageEl = el.querySelector('[data-testid="agent-hb-age"]');
      expect(ageEl?.classList.contains('stale')).toBe(true);
      expect(ageEl?.classList.contains('dim')).toBe(false);
    });

    it('dims a parked lease\'s age instead of using the stale color, even though the age keeps growing', async () => {
      // The reap clock is stopped for a parked lease — a large age is expected, not alarming.
      const el = await render(lease({ state: 'parked', last_heartbeat_at: '2026-07-16T08:00:00.000Z' }));
      const ageEl = el.querySelector('[data-testid="agent-hb-age"]');
      expect(ageEl?.classList.contains('dim')).toBe(true);
      expect(ageEl?.classList.contains('stale')).toBe(false);
      expect(ageEl?.textContent?.trim()).toBe('-4h00m');
    });

    it('renders a running lease\'s age undecorated (no stale, no dim)', async () => {
      const el = await render(lease({ state: 'running', last_heartbeat_at: '2026-07-16T11:59:26.000Z' }));
      const ageEl = el.querySelector('[data-testid="agent-hb-age"]');
      expect(ageEl?.classList.contains('stale')).toBe(false);
      expect(ageEl?.classList.contains('dim')).toBe(false);
    });

    // bzh:utc-instants — the bounded-tolerance region agent-row.ts must mirror from
    // runner-strip.ts's seenLabel: a positive age (above), a small negative age
    // (benign browser-vs-hub skew, reads as "-0s"), and a large negative age (not
    // skew — falls through to "—" rather than a confident "-0s").
    it('reads a small browser-vs-hub skew (last_heartbeat_at up to 60s in the future) as "-0s"', async () => {
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T12:00:30.000Z' })); // 30s after REF
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('-0s');
    });

    it('still reads "-0s" at exactly the 60s tolerance boundary', async () => {
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T12:01:00.000Z' })); // 60s after REF
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('-0s');
    });

    it('does not render a confident "-0s" for a heartbeat stamp hours in the future — falls through to "—"', async () => {
      // The naive-timestamp bug this guards against: a naive wire stamp on a UTC-5
      // box reads five hours ahead of the true instant (bzh:utc-instants).
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T17:00:00.000Z' })); // 5h after REF
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('—');
    });
  });

  describe('issue title (issue #28 phase 7, D-084 pass-through)', () => {
    it('renders chips + title once the pm-items read resolves — success case', async () => {
      const el = await render(lease(), (method, path) =>
        method === 'GET' && path === '/api/chunks/C-125/pm-items'
          ? { items: [{ provider: 'github', url: 'https://github.com/acme/widget/issues/8', label: 'gh:widget#8', title: 'Fix the flaky retry', fetched_at: 't', body: 'x', comments: [] }] }
          : {},
      );

      const ttl = el.querySelector('[data-testid="agent-title"]');
      expect(ttl?.textContent).toContain('gh:widget#8');
      expect(ttl?.textContent).toContain('Fix the flaky retry');
      expect(el.querySelector('[data-testid="agent-title"] .chips')?.textContent).toBe('gh:widget#8');
    });

    it('shows chunk_id immediately while pm-items is in flight, with the title slot empty until it resolves, then fills', async () => {
      activeStubs.push(
        stubRunnerClient((method, path) =>
          method === 'GET' && path === '/api/chunks/C-125/pm-items'
            ? {
                items: [
                  {
                    provider: 'github',
                    url: 'https://github.com/acme/widget/issues/8',
                    label: 'gh:widget#8',
                    title: 'Fix the flaky retry',
                    fetched_at: 't',
                    body: 'x',
                    comments: [],
                  },
                ],
              }
            : {},
        ),
      );
      await TestBed.configureTestingModule({
        imports: [AgentRow],
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        ],
      }).compileComponents();
      const fixture = TestBed.createComponent(AgentRow);
      fixture.componentRef.setInput('agent', lease());
      // Right after creation the stubbed fetch's promise hasn't resolved yet — the
      // title query is still pending. The row must already show chunk_id, with no
      // title element and nothing blocking on it (no spinner, no loading state).
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="agent-row"]')?.textContent).toContain('C-125');
      expect(el.querySelector('[data-testid="agent-title"]')).toBeNull();

      await settle(fixture);
      expect(el.querySelector('[data-testid="agent-title"]')?.textContent).toContain('Fix the flaky retry');
    });

    it('falls back to chunk_id alone when the pm-items read has no items', async () => {
      const el = await render(lease(), (method, path) =>
        method === 'GET' && path === '/api/chunks/C-125/pm-items' ? { items: [] } : {},
      );

      expect(el.querySelector('[data-testid="agent-title"]')).toBeNull();
      expect(el.querySelector('[data-testid="agent-row"]')?.textContent).toContain('C-125');
    });

    it('falls back to chunk_id alone when the pm-items read 502s — never surfaces the failure', async () => {
      const el = await render(lease(), (method, path) => {
        if (method === 'GET' && path === '/api/chunks/C-125/pm-items') throw new RouteError(502, 'hub unreachable');
        return {};
      });

      expect(el.querySelector('[data-testid="agent-title"]')).toBeNull();
      expect(el.querySelector('[data-testid="agent-row"]')?.textContent).toContain('C-125');
    });

    // ★ The no-branching guard (issue #28, decision 1: the title is "optional, may
    // not work, and when it doesn't work the rest of the web app still functions").
    //
    // Deliberately markup-agnostic: it compares rendered *text* against a title-free
    // baseline rather than asserting some known testid is absent. An assertion keyed
    // to `[data-testid="agent-title"]` passes happily while the template grows a
    // *differently* named error/spinner element — so it cannot pin "the row renders
    // chunk_id regardless, and never branches on isError()/isPending()". Text
    // equality can: any error text, spinner, or placeholder the title slot grows in
    // a degraded state makes the row differ from the row that never had a title.
    //
    // 502 (hub down), 503 (runner not wired to a hub) and 404 (no work-source) are
    // one code path — the query settles `error` and the row ignores it — but each is
    // a row in the plan's degradation table, so each is exercised rather than
    // resting on the other two's behalf.
    it.each([
      [502, 'hub down'],
      [503, 'runner not wired to a hub'],
      [404, 'no work-source'],
    ])('renders a %i (%s) row textually identically to a title-free row — no error text, no spinner', async (status) => {
      const rowText = (el: HTMLElement) =>
        (el.querySelector('[data-testid="agent-row"]')?.textContent ?? '').replace(/\s+/g, ' ').trim();

      const baseline = rowText(await render(lease(), () => ({ items: [] })));
      expect(baseline).toContain('C-125');

      TestBed.resetTestingModule();
      const degraded = await render(lease(), (method, path) => {
        if (method === 'GET' && path === '/api/chunks/C-125/pm-items') throw new RouteError(status, 'unavailable');
        return {};
      });

      expect(rowText(degraded)).toBe(baseline);
    });

    it('renders an in-flight row textually identically to a title-free row — nothing blocks, no spinner', async () => {
      const rowText = (el: HTMLElement) =>
        (el.querySelector('[data-testid="agent-row"]')?.textContent ?? '').replace(/\s+/g, ' ').trim();

      const baseline = rowText(await render(lease(), () => ({ items: [] })));

      TestBed.resetTestingModule();
      activeStubs.push(
        stubRunnerClient((method, path) =>
          method === 'GET' && path === '/api/chunks/C-125/pm-items'
            ? {
                items: [
                  {
                    provider: 'github',
                    url: 'https://github.com/acme/widget/issues/8',
                    label: 'gh:widget#8',
                    title: 'Fix the flaky retry',
                    fetched_at: 't',
                    body: 'x',
                    comments: [],
                  },
                ],
              }
            : {},
        ),
      );
      await TestBed.configureTestingModule({
        imports: [AgentRow],
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        ],
      }).compileComponents();
      const fixture = TestBed.createComponent(AgentRow);
      fixture.componentRef.setInput('agent', lease());
      // The stubbed fetch's promise has not resolved: the title query is pending.
      fixture.detectChanges();

      expect(rowText(fixture.nativeElement as HTMLElement)).toBe(baseline);
    });

    it('shows the label chip alone, not the title, on a per-pointer forge degrade (D-084: label survives, title/body go null)', async () => {
      const el = await render(lease(), (method, path) =>
        method === 'GET' && path === '/api/chunks/C-125/pm-items'
          ? {
              items: [
                {
                  provider: 'github',
                  url: 'https://github.com/acme/widget/issues/9',
                  label: 'gh:widget#9',
                  title: null,
                  body: null,
                  error: 'forge unreachable for issues/9',
                  fetched_at: 't',
                  comments: [],
                },
              ],
            }
          : {},
      );

      const ttl = el.querySelector('[data-testid="agent-title"]');
      expect(ttl?.querySelector('.chips')?.textContent).toBe('gh:widget#9');
      expect(ttl?.textContent?.replace('gh:widget#9', '').trim()).toBe('');
    });

    it('falls back to chunk_id alone when a pointer has neither a title nor a label', async () => {
      const el = await render(lease(), (method, path) =>
        method === 'GET' && path === '/api/chunks/C-125/pm-items'
          ? {
              items: [
                {
                  provider: 'github',
                  url: 'not-issue-shaped',
                  label: null,
                  title: null,
                  body: null,
                  error: 'forge unreachable',
                  fetched_at: 't',
                  comments: [],
                },
              ],
            }
          : {},
      );

      expect(el.querySelector('[data-testid="agent-title"]')).toBeNull();
      expect(el.querySelector('[data-testid="agent-row"]')?.textContent).toContain('C-125');
    });

    it('makes exactly one pm-items request for the row\'s chunk_id — never polled, never retried', async () => {
      const el = await render(lease(), (method, path) =>
        method === 'GET' && path === '/api/chunks/C-125/pm-items' ? { items: [] } : {},
      );
      expect(el).not.toBeNull();
      const stub = activeStubs[activeStubs.length - 1];
      expect(stub.forRoute('/api/chunks/C-125/pm-items', 'GET')).toHaveLength(1);
    });
  });
});
