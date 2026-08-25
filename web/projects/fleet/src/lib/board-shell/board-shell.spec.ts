import { CdkDropList, type CdkDragDrop } from '@angular/cdk/drag-drop';
import { provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';

import type { ChunkSummary } from '../api/hub';
import { compactRef } from '../compact-ref';
import type { KitAsyncStateValue } from '../kit/kit-async-state';
import { KitPanel } from '../kit/kit-panel';
import { BoardShell } from './board-shell';

const READY = (suffix: string): ChunkSummary => ({
  chunk_id: `ch_01ready${suffix}`,
  graph_id: 'gr_1',
  status: 'ready',
  current_node_id: 'nd_build',
  work_refs: [],
});

const BACKLOG = (suffix: string): ChunkSummary => ({
  chunk_id: `ch_01backlog${suffix}`,
  graph_id: 'gr_1',
  status: 'not_ready',
  current_node_id: 'nd_build',
  work_refs: [],
});

describe('BoardShell', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BoardShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  /** Render the board off a chunk list (and, when the READY lane is under test,
   * the hub's dispatch order for it), settled. Defaults to `'ready'` — most of
   * this spec is exercising populated-board behavior, not the triad itself.
   * `canControl`/`canReorder` default `true` — this spec's baseline actor is
   * fully permissioned; a test asserting the withheld case sets its own `false`. */
  const render = async (
    chunks: ChunkSummary[],
    readyOrder: string[] = [],
    state: KitAsyncStateValue = 'ready',
    permissions: { canControl?: boolean; canReorder?: boolean } = {},
    backlogOrder: string[] = [],
  ) => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('readyOrder', readyOrder);
    fixture.componentRef.setInput('backlogOrder', backlogOrder);
    fixture.componentRef.setInput('state', state);
    fixture.componentRef.setInput('canControl', permissions.canControl ?? true);
    fixture.componentRef.setInput('canReorder', permissions.canReorder ?? true);
    await fixture.whenStable();
    return fixture;
  };

  /** The chunk ids rendered in a lane, top to bottom. */
  const laneIds = (el: HTMLElement, column: string): string[] =>
    [...el.querySelectorAll(`[data-col="${column}"] [data-testid="chunk-card"]`)].map(
      (card) => card.getAttribute('data-chunk') ?? '',
    );

  it('renders the board shell with all six columns and an empty state', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('state', 'empty');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-shell"]')).toBeTruthy();
    // Six board columns: the backlog, the ready queue, and the four post-dispatch
    // lanes. READY is a lane like any other (issue #137) — the ready queue is on the
    // board, not in a rail beside it.
    expect(el.querySelectorAll('[data-col]')).toHaveLength(6);
    expect(el.querySelector('[data-col="notready"]')).toBeTruthy();
    expect(el.querySelector('[data-col="ready"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('NO CHUNKS');
  });

  it("suppresses the board panel's own scrolling — each lane scrolls itself instead (issue #309)", async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();

    const panel = fixture.debugElement.query(By.directive(KitPanel));
    expect(panel.componentInstance.bodyScroll()).toBe(false);
  });

  it('shows a loading indicator instead of the empty state while the chunks read is pending (AC 1)', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('state', 'loading');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-loading"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
  });

  it('shows an error indicator when the chunks read fails', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('state', 'error');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
  });

  it('still renders the lane grid while loading — the board keeps its shape', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('state', 'loading');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-col]')).toHaveLength(6);
  });

  it('engraves the backlog column BACKLOG and the queue column READY, in dispatch order', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const heads = [...el.querySelectorAll('[data-col] .col-lbl')].map((n) => n.textContent?.trim());
    expect(heads).toEqual(['BACKLOG', 'READY', 'RUNNING', 'WAIT/HUMAN', 'NEEDS HUMAN', 'DONE']);
  });

  it('renders a not-ready chunk in the backlog column with a Promote action that emits its id', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01notready00000000000000000', graph_id: 'gr_1', status: 'not_ready', current_node_id: 'nd_build', work_refs: [] },
      { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', status: 'running', current_node_id: 'nd_build', work_refs: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('canControl', true);
    let promoted: string | undefined;
    fixture.componentInstance.promote.subscribe((id) => (promoted = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The not-ready chunk is a card in its own column, distinct from the ready rail and the
    // running lane; only it carries a Promote button.
    const card = el.querySelector('[data-col="notready"] [data-testid="chunk-card"]');
    expect(card?.getAttribute('data-status')).toBe('not_ready');
    expect(el.querySelectorAll('[data-testid="promote-chunk"]')).toHaveLength(1);
    expect(el.querySelector('[data-col="running"] [data-testid="promote-chunk"]')).toBeNull();

    card?.querySelector<HTMLButtonElement>('[data-testid="promote-chunk"]')?.click();
    expect(promoted).toBe('ch_01notready00000000000000000');
  });

  it('withholds Promote without chunk:control', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01notready00000000000000000', graph_id: 'gr_1', status: 'not_ready', current_node_id: 'nd_build', work_refs: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-col="notready"] [data-testid="chunk-card"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="promote-chunk"]')).toBeNull();
  });

  it('renders one card per chunk, in its derived-status column, showing status + current node', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01ready0000000000000000000', graph_id: 'gr_1', status: 'ready', current_node_id: 'nd_build', work_refs: [] },
      { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', status: 'running', current_node_id: 'nd_build', work_refs: [] },
      { chunk_id: 'ch_01done00000000000000000000', graph_id: 'gr_1', status: 'done', current_node_id: 'done', work_refs: [] },
    ];
    const el = (await render(chunks)).nativeElement as HTMLElement;

    // No empty state once the fleet has chunks, and every chunk is a card —
    // the ready one included, in the READY lane (issue #137).
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="chunk-card"]')).toHaveLength(3);

    // A card carries the derived status and the current node id.
    const running = el.querySelector('[data-col="running"] [data-testid="chunk-card"]');
    expect(running?.querySelector('[data-testid="chunk-status"]')?.textContent).toContain('running');
    expect(running?.querySelector('[data-testid="chunk-node"]')?.textContent).toContain('nd_build');

    // Each status lands in its own column, and a chunk is a card in exactly one of them.
    expect(el.querySelectorAll('[data-col="ready"] [data-testid="chunk-card"]')).toHaveLength(1);
    expect(el.querySelectorAll('[data-col="running"] [data-testid="chunk-card"]')).toHaveLength(1);
    expect(el.querySelectorAll('[data-col="done"] [data-testid="chunk-card"]')).toHaveLength(1);
    expect(el.querySelectorAll('[data-chunk="ch_01ready0000000000000000000"]')).toHaveLength(1);
  });

  it('renders a paused chunk in the WAIT/HUMAN column (issue #46)', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01paused000000000000000000', graph_id: 'gr_1', status: 'paused', current_node_id: 'nd_build', work_refs: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Paused shares the WAIT/HUMAN column — work stopped pending a human either way.
    const card = el.querySelector('[data-col="waiting"] [data-testid="chunk-card"]');
    expect(card?.getAttribute('data-status')).toBe('paused');
  });

  it('emits the chunk id when a card is activated (fills the detail dock)', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', status: 'running', current_node_id: 'nd_build', work_refs: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="chunk-card"] button')?.click();
    expect(selected).toBe('ch_01running000000000000000000');
  });

  it('renders the node name as the visible label with the raw ULID demoted to a tooltip', async () => {
    const chunks: ChunkSummary[] = [
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1',
        status: 'running',
        current_node_id: 'nd_01KXHKVCWZ1000000000000000',
        current_node_name: 'review',
        work_refs: [],
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const node = el.querySelector('[data-testid="chunk-node"]');
    expect(node?.textContent?.trim()).toBe('review');
    expect(node?.getAttribute('title')).toBe('nd_01KXHKVCWZ1000000000000000');
  });

  it('names each work item as plain text, one chip per line — a card carries no competing link', async () => {
    const chunks: ChunkSummary[] = [
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1',
        status: 'running',
        current_node_id: 'nd_build',
        current_node_name: 'build',
        work_refs: [
          { source: 'blizzard', ref: '8', label: 'blizzard#8', web_url: 'https://github.com/paul-gross/blizzard/issues/8' },
          { source: 'widget', ref: '9', label: 'widget#9', web_url: 'https://github.com/paul-gross/widget/issues/9' },
        ],
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Each pointer gets its own work-item line; the whole card is a single click
    // target for opening the chunk, so nothing inside it is an anchor competing for
    // that click — the link out to the forge lives in the detail panel.
    const chips = el.querySelectorAll('[data-testid="work-ref-chip"]');
    expect(Array.from(chips).map((c) => c.textContent?.trim())).toEqual(['blizzard#8', 'widget#9']);
    expect(el.querySelectorAll('[data-testid="chunk-card"] a')).toHaveLength(0);
    // The short chunk id stays visible as the stable handle.
    expect(el.querySelector('[data-testid="chunk-id"]')?.textContent).toContain('C-0000');
  });

  it('degrades to the short chunk id when a chunk has no labeled pointer', async () => {
    const chunks: ChunkSummary[] = [
      // Zero pointers, and a pointer whose URL did not parse (null label) — no chips,
      // the short id carries the identity, and nothing errors.
      { chunk_id: 'ch_01done00000000000000000000', graph_id: 'gr_1', status: 'done', current_node_id: 'done', work_refs: [] },
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1',
        status: 'running',
        current_node_id: 'nd_build',
        work_refs: [{ source: 'blizzard', ref: 'wiki', label: null, web_url: null }],
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="work-ref-chip"]')).toHaveLength(0);
    expect(el.querySelectorAll('[data-testid="chunk-card"]')).toHaveLength(2);
    const running = el.querySelector('[data-col="running"] [data-testid="chunk-card"]');
    expect(running?.querySelector('[data-testid="chunk-id"]')?.textContent).toContain('C-');
  });

  it('names a chunk by its ULID tail, where the entropy that tells chunks apart lives', () => {
    // A leading slice would print the same timestamp prefix on every card minted in
    // the same millisecond-ish window; the tail is what actually discriminates them.
    expect(compactRef('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9')).toBe('C-3YJ9');
    expect(compactRef('ch_01KXKVVF1J3D6H6VYZ3XYN3YAB')).toBe('C-3YAB');
  });

  it("shows the chunk's derived cost total as a badge on its card (issue #60)", async () => {
    const chunks: ChunkSummary[] = [
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1',
        status: 'running',
        current_node_id: 'nd_build',
        work_refs: [],
        cost: {
          input_tokens: 100,
          output_tokens: 50,
          cache_read_tokens: 0,
          cache_create_tokens: 0,
          cost_usd: 1.23,
          cost_partial: false,
        },
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="card-cost"]')?.textContent).toContain('$1.23');
  });

  it("marks a card's cost badge with the lower-bound prefix when the total is PARTIAL (issue #60)", async () => {
    const chunks: ChunkSummary[] = [
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1',
        status: 'running',
        current_node_id: 'nd_build',
        work_refs: [],
        cost: {
          input_tokens: 100,
          output_tokens: 50,
          cache_read_tokens: 0,
          cache_create_tokens: 0,
          cost_usd: 0.1,
          cost_partial: true,
        },
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="card-cost"]')?.textContent).toContain('~$0.10');
  });

  it('shows no cost badge for a chunk with zero, non-partial spend', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', status: 'running', current_node_id: 'nd_build', work_refs: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="card-cost"]')).toBeNull();
  });

  /*
   * The READY lane (issue #137) — the ready queue as a board column: ordered by
   * the hub's dispatch order, and the only lane carrying the queue-shaping
   * affordances the retired left rail used to own (same testids, moved not
   * renamed).
   */
  describe('the READY lane', () => {
    const A = READY('aaaaaaaaaaaaaaaaaaaa');
    const B = READY('bbbbbbbbbbbbbbbbbbbb');
    const C = READY('cccccccccccccccccccc');

    /** A `CdkDragDrop`-shaped event for the drop handler. A real pointer drag is
     * an e2e concern; what this component owns is the index → anchor arithmetic,
     * and that is exactly what the event carries. */
    const drop = (previousIndex: number, currentIndex: number) =>
      ({ previousIndex, currentIndex }) as CdkDragDrop<unknown>;

    /** Fire a drop on the READY lane's drop list through its real
     * `(cdkDropListDropped)` binding — BACKLOG is reorderable too, so this picks
     * READY's out by its `[data-col]`, not by assuming it is the only one. */
    const dropOnReady = (fixture: ComponentFixture<BoardShell>, event: CdkDragDrop<unknown>): void => {
      const lists = fixture.debugElement.queryAll(By.directive(CdkDropList));
      const ready = lists.find((list) => list.nativeElement.closest('[data-col]').getAttribute('data-col') === 'ready');
      expect(ready).toBeTruthy();
      ready!.injector.get(CdkDropList).dropped.emit(event);
    };

    it('orders its cards by the hub dispatch order, not the fleet list order', async () => {
      const fixture = await render([A, B, C], [C.chunk_id, A.chunk_id, B.chunk_id]);

      expect(laneIds(fixture.nativeElement as HTMLElement, 'ready')).toEqual([C.chunk_id, A.chunk_id, B.chunk_id]);
    });

    it('sorts a ready chunk the queue read does not yet name after the ones it does, order kept', async () => {
      // A promote the queue read has not caught up with is still a ready chunk; it
      // waits at the back rather than jumping the queue or vanishing off the board.
      const fixture = await render([A, B, C], [C.chunk_id]);

      expect(laneIds(fixture.nativeElement as HTMLElement, 'ready')).toEqual([C.chunk_id, A.chunk_id, B.chunk_id]);
    });

    it('opens a ready chunk in the detail dock on click, like any other card', async () => {
      const fixture = await render([A], [A.chunk_id]);
      let selected: string | undefined;
      fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));

      (fixture.nativeElement as HTMLElement)
        .querySelector<HTMLButtonElement>('[data-col="ready"] [data-testid="chunk-card"] .card-open')
        ?.click();

      expect(selected).toBe(A.chunk_id);
    });

    it('emits moveToTop tagged \'ready\' from a card\'s Top button, disabled for the one already there', async () => {
      const fixture = await render([A, B], [A.chunk_id, B.chunk_id]);
      let emitted: { chunkId: string; list: string } | undefined;
      fixture.componentInstance.moveToTop.subscribe((move) => (emitted = move));
      const el = fixture.nativeElement as HTMLElement;

      const tops = el.querySelectorAll<HTMLButtonElement>('[data-testid="queue-move-top"]');
      expect(tops).toHaveLength(2);
      expect(tops[0].disabled).toBe(true);
      tops[1].click();
      expect(emitted).toEqual({ chunkId: B.chunk_id, list: 'ready' });
    });

    it('emits group with the checked ids in lane order, and only from two up', async () => {
      const fixture = await render([A, B, C], [A.chunk_id, B.chunk_id, C.chunk_id]);
      let emitted: readonly string[] | undefined;
      fixture.componentInstance.group.subscribe((ids) => (emitted = ids));
      const el = fixture.nativeElement as HTMLElement;

      const groupButton = el.querySelector<HTMLButtonElement>('[data-testid="group-selected"]');
      expect(groupButton?.disabled).toBe(true);

      const checks = el.querySelectorAll<HTMLInputElement>('[data-testid="queue-select"]');
      checks[2].click();
      checks[1].click();
      fixture.detectChanges();

      // Lane order, not click order — the top-most selected is the survivor.
      el.querySelector<HTMLButtonElement>('[data-testid="group-selected"]')?.click();
      expect(emitted).toEqual([B.chunk_id, C.chunk_id]);
    });

    it('carries the queue-shaping controls in READY alone', async () => {
      const el = (
        await render([
          A,
          { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', status: 'running', current_node_id: 'nd_build', work_refs: [] },
        ])
      ).nativeElement as HTMLElement;

      expect(el.querySelectorAll('[data-testid="queue-select"]')).toHaveLength(1);
      expect(el.querySelectorAll('[data-testid="group-selected"]')).toHaveLength(1);
      expect(el.querySelector('[data-col="ready"] [data-testid="queue-select"]')).toBeTruthy();
      expect(el.querySelector('[data-col="running"] [data-testid="queue-move-top"]')).toBeNull();
    });

    it('does not arm the drag list, and withholds Group/Top/checkbox, without queue:reorder', async () => {
      const fixture = await render([A, B], [A.chunk_id, B.chunk_id], 'ready', { canReorder: false });
      const el = fixture.nativeElement as HTMLElement;

      expect(fixture.debugElement.queryAll(By.directive(CdkDropList))).toHaveLength(0);
      expect(el.querySelector('[data-testid="group-selected"]')).toBeNull();
      expect(el.querySelector('[data-testid="queue-select"]')).toBeNull();
      expect(el.querySelector('[data-testid="queue-move-top"]')).toBeNull();
      // The cards themselves still render — a read-only board still shows the queue.
      expect(laneIds(el, 'ready')).toEqual([A.chunk_id, B.chunk_id]);
    });

    it('resolves a drop to the anchor it landed after — null at the top — tagged \'ready\'', async () => {
      const fixture = await render([A, B, C], [A.chunk_id, B.chunk_id, C.chunk_id]);
      const moves: { chunkId: string; afterChunkId: string | null; list: string }[] = [];
      fixture.componentInstance.reposition.subscribe((move) => moves.push(move));

      // C dragged to the very top: no chunk above it.
      dropOnReady(fixture, drop(2, 0));
      // A dragged into the middle: it lands under whatever closed up behind it.
      dropOnReady(fixture, drop(0, 1));
      // A dragged to the bottom: the anchor is the last of the others.
      dropOnReady(fixture, drop(0, 2));

      expect(moves).toEqual([
        { chunkId: C.chunk_id, afterChunkId: null, list: 'ready' },
        { chunkId: A.chunk_id, afterChunkId: B.chunk_id, list: 'ready' },
        { chunkId: A.chunk_id, afterChunkId: C.chunk_id, list: 'ready' },
      ]);
    });

    it('writes nothing for a drop that changed no order', async () => {
      const fixture = await render([A, B], [A.chunk_id, B.chunk_id]);
      const moves: unknown[] = [];
      fixture.componentInstance.reposition.subscribe((move) => moves.push(move));

      dropOnReady(fixture, drop(1, 1));

      expect(moves).toEqual([]);
    });
  });

  /*
   * The BACKLOG lane's own reorder affordances (the backlog ranking work that
   * followed issue #137) — drag-and-drop and a Top button, exactly like READY's,
   * but tagged 'notready' so a container routes the write to the backlog's own
   * mutation rather than the ready queue's. Grouping stays READY-only
   * (out of scope to extend): BACKLOG never renders the checkbox or Group
   * button, permission or not.
   */
  describe('the BACKLOG lane', () => {
    const P = BACKLOG('pppppppppppppppppppp');
    const Q = BACKLOG('qqqqqqqqqqqqqqqqqqqq');

    const drop = (previousIndex: number, currentIndex: number) =>
      ({ previousIndex, currentIndex }) as CdkDragDrop<unknown>;

    /** Fire a drop on the BACKLOG lane's drop list through its real
     * `(cdkDropListDropped)` binding — picked out by `[data-col="notready"]`. */
    const dropOnBacklog = (fixture: ComponentFixture<BoardShell>, event: CdkDragDrop<unknown>): void => {
      const lists = fixture.debugElement.queryAll(By.directive(CdkDropList));
      const backlog = lists.find(
        (list) => list.nativeElement.closest('[data-col]').getAttribute('data-col') === 'notready',
      );
      expect(backlog).toBeTruthy();
      backlog!.injector.get(CdkDropList).dropped.emit(event);
    };

    it('orders its cards by the hub backlog order, not the fleet list order', async () => {
      const fixture = await render([P, Q], [], 'ready', {}, [Q.chunk_id, P.chunk_id]);

      expect(laneIds(fixture.nativeElement as HTMLElement, 'notready')).toEqual([Q.chunk_id, P.chunk_id]);
    });

    it('arms the drag list and renders a Top button with its own testid, with queue:reorder', async () => {
      const fixture = await render([P, Q], [], 'ready', { canReorder: true }, [P.chunk_id, Q.chunk_id]);
      const el = fixture.nativeElement as HTMLElement;

      const lists = fixture.debugElement.queryAll(By.directive(CdkDropList));
      expect(lists.some((l) => l.nativeElement.closest('[data-col]').getAttribute('data-col') === 'notready')).toBe(
        true,
      );
      const tops = el.querySelectorAll<HTMLButtonElement>('[data-col="notready"] [data-testid="backlog-move-top"]');
      expect(tops).toHaveLength(2);
      expect(tops[0].disabled).toBe(true);
      // READY's own testid never leaks onto the BACKLOG lane.
      expect(el.querySelector('[data-col="notready"] [data-testid="queue-move-top"]')).toBeNull();
    });

    it('withholds the drag list and the Top button without queue:reorder — cards still render', async () => {
      const fixture = await render([P, Q], [], 'ready', { canReorder: false }, [P.chunk_id, Q.chunk_id]);
      const el = fixture.nativeElement as HTMLElement;

      const lists = fixture.debugElement.queryAll(By.directive(CdkDropList));
      expect(lists.some((l) => l.nativeElement.closest('[data-col]').getAttribute('data-col') === 'notready')).toBe(
        false,
      );
      expect(el.querySelector('[data-col="notready"] [data-testid="backlog-move-top"]')).toBeNull();
      expect(laneIds(el, 'notready')).toEqual([P.chunk_id, Q.chunk_id]);
    });

    it('renders no grouping affordance — no checkbox, no Group button — with or without queue:reorder', async () => {
      for (const canReorder of [true, false]) {
        const fixture = await render([P, Q], [], 'ready', { canReorder }, [P.chunk_id, Q.chunk_id]);
        const el = fixture.nativeElement as HTMLElement;

        expect(el.querySelector('[data-col="notready"] [data-testid="queue-select"]')).toBeNull();
        expect(el.querySelector('[data-col="notready"] [data-testid="group-selected"]')).toBeNull();
      }
    });

    it("emits moveToTop tagged 'notready' from a card's Top button", async () => {
      const fixture = await render([P, Q], [], 'ready', {}, [P.chunk_id, Q.chunk_id]);
      let emitted: { chunkId: string; list: string } | undefined;
      fixture.componentInstance.moveToTop.subscribe((move) => (emitted = move));
      const el = fixture.nativeElement as HTMLElement;

      const tops = el.querySelectorAll<HTMLButtonElement>('[data-col="notready"] [data-testid="backlog-move-top"]');
      tops[1].click();

      expect(emitted).toEqual({ chunkId: Q.chunk_id, list: 'notready' });
    });

    it("resolves a drop to the anchor it landed after, tagged 'notready'", async () => {
      const fixture = await render([P, Q], [], 'ready', {}, [P.chunk_id, Q.chunk_id]);
      const moves: { chunkId: string; afterChunkId: string | null; list: string }[] = [];
      fixture.componentInstance.reposition.subscribe((move) => moves.push(move));

      // Q dragged to the very top: no chunk above it.
      dropOnBacklog(fixture, drop(1, 0));

      expect(moves).toEqual([{ chunkId: Q.chunk_id, afterChunkId: null, list: 'notready' }]);
    });

    it('does not disturb READY drops or vice versa — the two lists are independent drop targets', async () => {
      const A = READY('aaaaaaaaaaaaaaaaaaaa');
      const B = READY('bbbbbbbbbbbbbbbbbbbb');
      const fixture = await render([A, B, P, Q], [A.chunk_id, B.chunk_id], 'ready', {}, [P.chunk_id, Q.chunk_id]);
      const moves: { chunkId: string; afterChunkId: string | null; list: string }[] = [];
      fixture.componentInstance.reposition.subscribe((move) => moves.push(move));

      dropOnBacklog(fixture, drop(1, 0));

      expect(moves).toEqual([{ chunkId: Q.chunk_id, afterChunkId: null, list: 'notready' }]);
      // READY's own order is untouched by a BACKLOG-only drop.
      expect(laneIds(fixture.nativeElement as HTMLElement, 'ready')).toEqual([A.chunk_id, B.chunk_id]);
    });
  });

  /*
   * The DONE lane's newest-first order (issue #173) — a second lane-scoped ordering,
   * the same shape as READY's own above but keyed on the completion instant instead
   * of the hub's dispatch order.
   */
  describe('the DONE lane', () => {
    const done = (idSuffix: string, completedAt: string | null): ChunkSummary => ({
      chunk_id: `ch_01done${idSuffix}`,
      graph_id: 'gr_1',
      status: 'done',
      current_node_id: 'done',
      work_refs: [],
      completed_at: completedAt,
    });

    it('orders newest completed_at first', async () => {
      const oldest = done('aaaaaaaaaaaaaaaaaaaa', '2026-07-01T00:00:00+00:00');
      const newest = done('bbbbbbbbbbbbbbbbbbbb', '2026-07-03T00:00:00+00:00');
      const middle = done('cccccccccccccccccccc', '2026-07-02T00:00:00+00:00');
      const fixture = await render([oldest, newest, middle]);

      expect(laneIds(fixture.nativeElement as HTMLElement, 'done')).toEqual([
        newest.chunk_id,
        middle.chunk_id,
        oldest.chunk_id,
      ]);
    });

    it('sorts a null completed_at last, keeping its relative order rather than jumping to the top', async () => {
      const noInstant = done('aaaaaaaaaaaaaaaaaaaa', null);
      const dated = done('bbbbbbbbbbbbbbbbbbbb', '2026-07-01T00:00:00+00:00');
      const alsoNoInstant = done('cccccccccccccccccccc', null);
      const fixture = await render([noInstant, dated, alsoNoInstant]);

      expect(laneIds(fixture.nativeElement as HTMLElement, 'done')).toEqual([
        dated.chunk_id,
        noInstant.chunk_id,
        alsoNoInstant.chunk_id,
      ]);
    });

    it("does not disturb READY's dispatch order or any other lane's order", async () => {
      const A = READY('aaaaaaaaaaaaaaaaaaaa');
      const B = READY('bbbbbbbbbbbbbbbbbbbb');
      const oldest = done('cccccccccccccccccccc', '2026-07-01T00:00:00+00:00');
      const newest = done('dddddddddddddddddddd', '2026-07-02T00:00:00+00:00');
      const fixture = await render([oldest, newest, A, B], [B.chunk_id, A.chunk_id]);

      expect(laneIds(fixture.nativeElement as HTMLElement, 'ready')).toEqual([B.chunk_id, A.chunk_id]);
      expect(laneIds(fixture.nativeElement as HTMLElement, 'done')).toEqual([newest.chunk_id, oldest.chunk_id]);
    });
  });
});
