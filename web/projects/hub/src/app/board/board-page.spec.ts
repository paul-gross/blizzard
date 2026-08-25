import { Location } from '@angular/common';
import { provideLocationMocks } from '@angular/common/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Router, provideRouter, withRouterConfig } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { compactRef, hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { BoardPage } from './board-page';

/**
 * The desktop board, driven through a **real** router (`RouterTestingHarness`)
 * rather than a stubbed `ActivatedRoute`: the URL owns the chunk selection
 * (issue #162), so a navigation's full round trip — write the param, re-read it,
 * push a history entry — is part of what is under test. The router is configured
 * exactly as `app.config.ts` configures it (`onSameUrlNavigation: 'reload'`), so
 * the spec exercises the app's own navigation semantics.
 *
 * The hub client's transport is stubbed, so this asserts what the page composes
 * off a known fleet list, not the queries themselves (those have their own specs).
 */
const RUNNING = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const ASKED = 'ch_01KXKVVF1J3D6H6VYZ3XYNBBBB';
const READY = 'ch_01KXKVVF1J3D6H6VYZ3XYNRDY1';
const READY_NEXT = 'ch_01KXKVVF1J3D6H6VYZ3XYNRDY2';
const GONE = 'ch_01KXKVVF1J3D6H6VYZ3XYNGONE';
const BACKLOG = 'ch_01KXKVVF1J3D6H6VYZ3XYNBLG1';
const BACKLOG_NEXT = 'ch_01KXKVVF1J3D6H6VYZ3XYNBLG2';

const CHUNK = (chunkId: string, status: string) => ({
  chunk_id: chunkId,
  graph_id: 'gr_1',
  status,
  current_node_id: 'nd_build',
  current_node_name: 'build',
  model: 'claude-opus-5',
  work_refs: [],
  runner_id: 'runner-local',
  environment_count: 1,
});

const DETAIL = (chunkId: string) => ({
  ...CHUNK(chunkId, 'running'),
  graph_name: 'default',
  latest_epoch: 1,
  history: [
    { node_id: 'nd_build', node_name: 'build', epoch: 1, at: '2026-07-16T11:00:00.000Z', outcome: 'transitioned' },
  ],
  artifacts: [],
});

/**
 * The reads the board's panels and its dock issue, answered off the three
 * chunks above. Everything unnamed falls through to `{}` — the envelope reads
 * (`/api/runners`, `/api/events`) unwrap that to their empty list, so the rail
 * renders as an idle fleet rather than an error.
 *
 * `me` is a parameter (defaulting to the full-permission fixture) rather than
 * baked in, so a spec asserting the withheld-`queue:reorder` case can answer
 * `/api/me` with a narrower identity without duplicating every other route.
 */
function hubRoutes(me: object = OPERATOR_ME_RESPONSE) {
  return (method: string, path: string): unknown => {
    if (method !== 'GET') return {};
    if (path === '/api/me') return me;
    if (path === '/api/queue') {
      return {
        entries: [
          { chunk_id: READY, graph_id: 'gr_1', position: 0, work_refs: [] },
          { chunk_id: READY_NEXT, graph_id: 'gr_1', position: 1, work_refs: [] },
        ],
      };
    }
    if (path === '/api/backlog') {
      return {
        entries: [
          { chunk_id: BACKLOG, graph_id: 'gr_1', position: 0, work_refs: [] },
          { chunk_id: BACKLOG_NEXT, graph_id: 'gr_1', position: 1, work_refs: [] },
        ],
      };
    }
    if (path === '/api/chunks') {
      return [
        CHUNK(RUNNING, 'running'),
        CHUNK(ASKED, 'waiting_on_human'),
        CHUNK(READY, 'ready'),
        CHUNK(READY_NEXT, 'ready'),
        CHUNK(BACKLOG, 'not_ready'),
        CHUNK(BACKLOG_NEXT, 'not_ready'),
      ];
    }
    if (path === '/api/questions') {
      return [
        {
          chunk_id: ASKED,
          runner_id: 'runner-local',
          lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
          question: 'Which branch?',
          asked_at: '2026-07-16T11:30:00.000Z',
        },
      ];
    }
    if (path.endsWith('/work-items')) return { items: [] };
    const detail = /^\/api\/chunks\/([^/]+)$/.exec(path);
    if (detail !== null) return DETAIL(detail[1]);
    return {};
  };
}

describe('BoardPage', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, hubRoutes());
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'board', component: BoardPage }], withRouterConfig({ onSameUrlNavigation: 'reload' })),
        provideLocationMocks(),
      ],
    });
  });

  afterEach(() => stub.restore());

  /**
   * Open the board at `url` and let every read settle. The location-change
   * listener the browser bootstrap wires up is opted into explicitly here — the
   * harness does not — so a `popstate` (the back/forward spec below) reaches the
   * router the way it does in the app.
   */
  async function open(url = '/board'): Promise<{ el: HTMLElement; harness: RouterTestingHarness }> {
    const harness = await RouterTestingHarness.create();
    TestBed.inject(Router).setUpLocationChangeListener();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { el: harness.fixture.nativeElement as HTMLElement, harness };
  }

  /** The board card for `chunkId`, found by the compact ref the shell renders on it. */
  function card(el: HTMLElement, chunkId: string): HTMLElement {
    const cards = Array.from(el.querySelectorAll<HTMLElement>('[data-testid="chunk-card"]'));
    const match = cards.find(
      (node) => node.querySelector('[data-testid="chunk-id"]')?.textContent?.trim() === compactRef(chunkId),
    );
    if (match === undefined) throw new Error(`no board card for ${chunkId}`);
    return match;
  }

  /** Click a chunk's card the way an operator does — the card's own open button. */
  async function openCard(harness: RouterTestingHarness, chunkId: string): Promise<void> {
    const el = harness.fixture.nativeElement as HTMLElement;
    card(el, chunkId).querySelector<HTMLElement>('.card-open')?.click();
    await settle(harness.fixture);
  }

  it('shows the board loading state before the chunks read resolves, then the populated board', async () => {
    const harness = await RouterTestingHarness.create();
    TestBed.inject(Router).setUpLocationChangeListener();
    await harness.navigateByUrl('/board');
    harness.fixture.detectChanges();
    const el = harness.fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-loading"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();

    await settle(harness.fixture);

    expect(el.querySelector('[data-testid="board-loading"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="chunk-card"]').length).toBeGreaterThan(0);
  });

  it('renders the shared fleet board shell and the operator controls', async () => {
    const { el } = await open();

    expect(el.querySelector('fleet-board-shell')).toBeTruthy();
    expect(el.querySelector('[data-testid="board-shell"]')).toBeTruthy();
    // The one rail composes beside the board: runners, asks, event log. The
    // titlebar itself lives at the app root now.
    expect(el.querySelector('[data-testid="runner-panel"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="questions-panel"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="event-log-panel"]')).toBeTruthy();
  });

  it('lays the board out as two columns, the ready queue among the board lanes', async () => {
    const { el } = await open();

    // The centre and the one rail are the columns of the main grid — the left
    // rail is gone with the queue panel it held (issue #137).
    const columns = el.querySelectorAll('.main > .col');
    expect(columns.length).toBe(2);
    expect(el.querySelector('fleet-board-shell')?.closest('.col')).toBe(columns[0]);
    expect(el.querySelector('[data-testid="runner-panel"]')?.closest('.col')).toBe(columns[1]);
    expect(el.querySelector('fleet-queue-panel')).toBeNull();
    expect(el.querySelector('[data-testid="queue-panel"]')).toBeNull();
  });

  it('stacks the event log under the asks in the right rail', async () => {
    const { el } = await open();

    const rail = el.querySelector('[data-testid="runner-panel"]')?.closest('.col');
    const log = el.querySelector('fleet-event-log-panel')!;
    const questions = el.querySelector('fleet-questions-panel')!;
    expect(log.closest('.col')).toBe(rail);
    // Below the asks, not above them: runners → asks → log.
    expect(questions.compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders a ready chunk exactly once across the whole page (issue #22)', async () => {
    const { el } = await open();

    // The READY lane replaced the rail rather than joining it: one chunk, one
    // place on the board, wherever its status puts it.
    expect(el.querySelectorAll(`[data-chunk="${READY}"]`)).toHaveLength(1);
    expect(card(el, READY).closest('[data-col]')?.getAttribute('data-col')).toBe('ready');
  });

  it('repositions a ready chunk with no anchor when its Top control is used', async () => {
    const { el, harness } = await open();

    // The lane renders in the queue read's order, so the second card is the one
    // with somewhere to go; the first one's Top is disabled.
    const tops = el.querySelectorAll<HTMLButtonElement>('[data-testid="queue-move-top"]');
    expect(tops[0].disabled).toBe(true);
    tops[1].click();
    await settle(harness.fixture);

    const calls = stub.forRoute('/api/queue/position', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ chunk_id: READY_NEXT, after_chunk_id: null });
  });

  /*
   * The BACKLOG lane's reorder affordances — the same drag-and-drop/Top wiring
   * as READY's, but reading `GET /api/backlog` and writing
   * `POST /api/backlog/position` instead of the queue's own routes
   * (`bzh:ranking-is-per-list`), and gated on `queue:reorder` even to read.
   */
  describe('the BACKLOG lane', () => {
    it('renders the backlog in its own hub order, in the notready column', async () => {
      const { el } = await open();

      expect(el.querySelectorAll(`[data-chunk="${BACKLOG}"]`)).toHaveLength(1);
      expect(card(el, BACKLOG).closest('[data-col]')?.getAttribute('data-col')).toBe('notready');
      const ids = [...el.querySelectorAll('[data-col="notready"] [data-testid="chunk-id"]')].map((n) =>
        n.textContent?.trim(),
      );
      expect(ids).toEqual([compactRef(BACKLOG), compactRef(BACKLOG_NEXT)]);
    });

    it("repositions a backlog chunk with no anchor when its Top control is used — the backlog's own route, not the queue's", async () => {
      const { el, harness } = await open();

      const tops = el.querySelectorAll<HTMLButtonElement>('[data-testid="backlog-move-top"]');
      expect(tops[0].disabled).toBe(true);
      tops[1].click();
      await settle(harness.fixture);

      const backlogCalls = stub.forRoute('/api/backlog/position', 'POST');
      expect(backlogCalls).toHaveLength(1);
      expect(backlogCalls[0].body).toEqual({ chunk_id: BACKLOG_NEXT, after_chunk_id: null });
      // Never the ready queue's route.
      expect(stub.forRoute('/api/queue/position', 'POST')).toHaveLength(0);
    });

    it('renders no grouping affordance on the backlog lane', async () => {
      const { el } = await open();

      expect(el.querySelector('[data-col="notready"] [data-testid="queue-select"]')).toBeNull();
      expect(el.querySelector('[data-col="notready"] [data-testid="group-selected"]')).toBeNull();
    });

    it('never reads the backlog, and withholds its drag list and Top button, without queue:reorder', async () => {
      // A narrower identity than OPERATOR_ME_RESPONSE: everything queue:reorder
      // gates is missing, everything else stays so the rest of the board still
      // renders normally.
      const restrictedMe = {
        ...OPERATOR_ME_RESPONSE,
        permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'queue:reorder'),
      };
      stub.restore();
      stub = stubRequestClient(hubClient, hubRoutes(restrictedMe));

      const { el } = await open();

      // The read itself never fires — not a fired-then-discarded 403.
      expect(stub.forRoute('/api/backlog', 'GET')).toHaveLength(0);
      expect(el.querySelector('[data-testid="backlog-move-top"]')).toBeNull();
      // The backlog chunk still renders as a card — a withheld read only
      // withholds the order and the reorder controls, not the chunk itself.
      expect(el.querySelectorAll(`[data-chunk="${BACKLOG}"]`)).toHaveLength(1);
      // No error surfaces anywhere on the board from the withheld read.
      expect(el.querySelector('[data-testid="board-error"]')).toBeNull();
    });
  });

  it('docks chunk detail beside the rails, so selecting never resizes the board (issue #21)', async () => {
    const { el, harness } = await open();

    // Nothing selected: the dock is already mounted, stacked under the board inside
    // the centre column, and holds a rest state prompting the operator to pick a chunk.
    const dockBefore = el.querySelector('fleet-chunk-detail.dock');
    expect(dockBefore).toBeTruthy();
    expect(dockBefore?.closest('.col')).toBe(el.querySelector('fleet-board-shell')?.closest('.col'));
    expect(el.querySelector('fleet-chunk-detail-panel')).toBeNull();
    expect(el.querySelector('[data-testid="chunk-detail-empty"]')?.textContent).toContain('SELECT');

    // Selecting a card fills the SAME dock element — the layout gains no node, so the
    // board columns cannot resize or shift.
    await openCard(harness, RUNNING);

    expect(el.querySelector('fleet-chunk-detail.dock')).toBe(dockBefore);
    expect(el.querySelector('fleet-chunk-detail-panel')).toBeTruthy();
  });

  it('opens a chunk from an ask in the right rail (MVP criterion 7)', async () => {
    const { el, harness } = await open();

    // An ask names a chunk nobody has selected; activating it fills the same dock the
    // board cards fill, which is where the answer is given.
    expect(el.querySelector('fleet-chunk-detail-panel')).toBeNull();
    harness.fixture.debugElement.query(By.css('fleet-questions-panel')).componentInstance.selectChunk.emit(ASKED);
    await settle(harness.fixture);

    expect(el.querySelector('fleet-chunk-detail-panel')).toBeTruthy();
  });

  describe('the URL drives selection (issue #162)', () => {
    it('hydrates the selection from the URL on load, no click — a shareable, refresh-safe link', async () => {
      const { el } = await open(`/board?chunk=${RUNNING}`);

      // The dock is open on the URL's chunk straight away, and its card reads as selected…
      expect(el.querySelector('fleet-chunk-detail-panel')).toBeTruthy();
      expect(card(el, RUNNING).classList.contains('selected')).toBe(true);
      expect(card(el, ASKED).classList.contains('selected')).toBe(false);
      // …and hydration is a pure read: nothing rewrote the URL.
      expect(TestBed.inject(Router).url).toBe(`/board?chunk=${RUNNING}`);
    });

    it('writes the selection into the URL when a card is clicked — a param merge, no reload', async () => {
      const { el, harness } = await open();

      await openCard(harness, RUNNING);

      expect(TestBed.inject(Router).url).toBe(`/board?chunk=${RUNNING}`);
      // A query-param navigation, not a route swap: the same page is still mounted.
      expect(el.querySelector('fleet-board-shell')).toBeTruthy();
      expect(card(el, RUNNING).classList.contains('selected')).toBe(true);
    });

    it('writes the same param when an ask in the right rail opens its chunk', async () => {
      const { el, harness } = await open();

      harness.fixture.debugElement.query(By.css('fleet-questions-panel')).componentInstance.selectChunk.emit(ASKED);
      await settle(harness.fixture);

      expect(TestBed.inject(Router).url).toBe(`/board?chunk=${ASKED}`);
      expect(card(el, ASKED).classList.contains('selected')).toBe(true);
    });

    it('clears the param when the dock is dismissed', async () => {
      const { el, harness } = await open(`/board?chunk=${RUNNING}`);

      harness.fixture.debugElement.query(By.css('fleet-chunk-detail')).componentInstance.dismiss.emit();
      await settle(harness.fixture);

      expect(TestBed.inject(Router).url).toBe('/board');
      expect(el.querySelector('[data-testid="chunk-detail-empty"]')?.textContent).toContain('SELECT');
    });

    it('merges into the URL, leaving query params it does not own alone', async () => {
      const { harness } = await open('/board?lane=running');

      await openCard(harness, RUNNING);

      expect(TestBed.inject(Router).url).toBe(`/board?lane=running&chunk=${RUNNING}`);
    });

    it('degrades a chunk id that no longer exists to no-selection, leaving the URL untouched', async () => {
      const { el } = await open(`/board?chunk=${GONE}`);

      // The board renders its normal no-selection state rather than erroring…
      expect(el.querySelector('[data-testid="chunk-detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
      expect(el.querySelectorAll('[data-testid="chunk-card"].selected').length).toBe(0);
      // …no detail read fired for the chunk that is not there…
      expect(stub.forRoute(`/api/chunks/${GONE}`, 'GET')).toEqual([]);
      // …and the board never rewrote the URL to "correct" it.
      expect(TestBed.inject(Router).url).toBe(`/board?chunk=${GONE}`);
    });

    it('back and forward walk the selection history', async () => {
      const { el, harness } = await open();

      await openCard(harness, RUNNING);
      await openCard(harness, ASKED);
      expect(TestBed.inject(Router).url).toBe(`/board?chunk=${ASKED}`);

      // Back returns to the previously selected chunk, and the dock follows the URL.
      TestBed.inject(Location).back();
      await settle(harness.fixture);
      expect(TestBed.inject(Router).url).toBe(`/board?chunk=${RUNNING}`);
      expect(card(el, RUNNING).classList.contains('selected')).toBe(true);
      expect(card(el, ASKED).classList.contains('selected')).toBe(false);

      // Forward walks it again.
      TestBed.inject(Location).forward();
      await settle(harness.fixture);
      expect(TestBed.inject(Router).url).toBe(`/board?chunk=${ASKED}`);
      expect(card(el, ASKED).classList.contains('selected')).toBe(true);
    });
  });
});
