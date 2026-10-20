import { CdkDropList, type CdkDragDrop } from '@angular/cdk/drag-drop';
import { provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';

import type { BoardCard } from '../board-card/board-card';
import type { BoardLaneMove, BoardReposition } from '../board-shell/board-column';
import { BoardList, type BoardListEntry, type BoardListGroups } from './board-list';

const RUNNER_ID = 'rn_01M49GZR9G2S7PT1TB5ZT4E4AX';

const CARD: BoardCard = {
  chunkId: 'ch_01running000000000000000',
  shortId: 'C-0001',
  status: 'running',
  terminal: false,
  promotable: false,
  node: 'build',
  nodeId: 'nd_build',
  workLines: [{ label: 'hub#1', title: null }],
  itemsRead: true,
  costUsd: 0,
  costPartial: false,
  estimatedCostUsd: null,
  completedAt: null,
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
};

const card = (suffix: string, fields: Partial<BoardCard> = {}): BoardCard => ({
  ...CARD,
  chunkId: `ch_01${suffix}`,
  shortId: `C-${suffix}`,
  ...fields,
});

const entry = (lane: string, each: BoardCard): BoardListEntry => ({ lane, card: each });
const fourItems = ['1', '2', '3', '4'].map((title) => ({ label: null, title }));
const running = (...cards: readonly BoardCard[]): BoardListGroups => groups({ live: cards.map((each) => entry('running', each)) });
const groups = (fields: Partial<BoardListGroups>): BoardListGroups => ({ live: [], ready: [], backlog: [], done: [], ...fields });

describe('BoardList', () => {
  let fixture: ComponentFixture<BoardList>;
  let selected: string[];

  const render = async (rendered: BoardListGroups, loaded = true, permissions: { canReorder?: boolean; canControl?: boolean } = {}) => {
    await TestBed.configureTestingModule({
      imports: [BoardList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    fixture = TestBed.createComponent(BoardList);
    fixture.componentRef.setInput('groups', rendered);
    fixture.componentRef.setInput('canReorder', permissions.canReorder ?? false);
    fixture.componentRef.setInput('canControl', permissions.canControl ?? false);
    fixture.componentRef.setInput('loaded', loaded);
    selected = [];
    fixture.componentInstance.selectChunk.subscribe((id) => selected.push(id));
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  };

  const rows = (el: HTMLElement) => [...el.querySelectorAll('[data-testid="list-row"]')].map((r) => r.getAttribute('data-chunk'));

  const ready = (suffix: string) => entry('ready', card(suffix, { status: 'ready' }));
  const backlog = (suffix: string) => entry('notready', card(suffix, { status: 'not_ready' }));
  const LISTED = groups({
    live: [entry('needs', card('n', { status: 'needs_human' })), entry('running', card('r'))],
    ready: [ready('q1'), ready('q2')],
    backlog: [backlog('b1'), backlog('b2')],
    done: [entry('done', card('d', { status: 'done', terminal: true }))],
  });

  it('renders the live lanes, then READY over a marked line over BACKLOG, then DONE', async () => {
    const el = await render(LISTED);

    expect(rows(el)).toEqual(['ch_01n', 'ch_01r', 'ch_01q1', 'ch_01q2', 'ch_01b1', 'ch_01b2', 'ch_01d']);
    const divider = el.querySelector('[data-testid="list-ready-divider"]')!;
    const readyRows = el.querySelector('[data-testid="list-ready"]')!;
    const backlogRows = el.querySelector('[data-testid="list-backlog"]')!;
    expect(readyRows.compareDocumentPosition(divider) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(divider.compareDocumentPosition(backlogRows) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("leads each row with its lane's icon", async () => {
    const el = await render(groups({ live: [entry('needs', card('a', { status: 'needs_human' }))], done: [entry('done', card('b', { status: 'stopped', terminal: true }))] }));

    const glyphs = [...el.querySelectorAll('[data-testid="list-row-lane"]')].map((icon) => icon.getAttribute('data-glyph'));
    expect(glyphs).toEqual(['needs', 'stopped']);
  });

  describe('drag', () => {
    const dropOn = (testid: string, event: Partial<CdkDragDrop<unknown>>): void => {
      const list = fixture.debugElement.queryAll(By.directive(CdkDropList)).find((each) => each.nativeElement.getAttribute('data-testid') === testid);
      expect(list, `no armed drop list ${testid}`).toBeTruthy();
      const target = list!.injector.get(CdkDropList);
      target.dropped.emit({ container: target, previousContainer: target, ...event } as CdkDragDrop<unknown>);
    };

    it('arms no row without queue:reorder', async () => {
      const el = await render(LISTED);

      expect(el.querySelector('[data-testid="list-reorder-grip"]')).toBeNull();
      expect(fixture.debugElement.queryAll(By.directive(CdkDropList))).toHaveLength(0);
    });

    it('arms READY and BACKLOG rows alone, each with the grip', async () => {
      const el = await render(LISTED, true, { canReorder: true });

      const gripped = [...el.querySelectorAll('[data-testid="list-reorder-grip"]')].map((grip) =>
        grip.parentElement!.querySelector('[data-testid="list-row"]')!.getAttribute('data-chunk'),
      );
      expect(gripped).toEqual(['ch_01q1', 'ch_01q2', 'ch_01b1', 'ch_01b2']);
    });

    it('resolves a drop within READY to the anchor it landed after', async () => {
      await render(LISTED, true, { canReorder: true });
      const moves: BoardReposition[] = [];
      fixture.componentInstance.reposition.subscribe((move) => moves.push(move));

      dropOn('list-ready', { previousIndex: 1, currentIndex: 0 });

      expect(moves).toEqual([{ chunkId: 'ch_01q2', afterChunkId: null, list: 'ready' }]);
    });

    it('turns a drop across the line into a lane move — above it a promote, below it a demote', async () => {
      await render(LISTED, true, { canReorder: true, canControl: true });
      const moves: BoardLaneMove[] = [];
      fixture.componentInstance.laneMove.subscribe((move) => moves.push(move));
      const other = {} as CdkDropList;

      dropOn('list-ready', { previousContainer: other, previousIndex: 0, currentIndex: 1, item: { data: 'ch_01b1' } as never });
      dropOn('list-backlog', { previousContainer: other, previousIndex: 0, currentIndex: 0, item: { data: 'ch_01q1' } as never });

      expect(moves).toEqual([
        { chunkId: 'ch_01b1', afterChunkId: 'ch_01q1', from: 'notready', to: 'ready' },
        { chunkId: 'ch_01q1', afterChunkId: null, from: 'ready', to: 'notready' },
      ]);
    });

    it('connects the two lists only when a row may cross them (chunk:control too)', async () => {
      await render(LISTED, true, { canReorder: true });
      const connected = () =>
        fixture.debugElement.queryAll(By.directive(CdkDropList)).map((list) => list.injector.get(CdkDropList).connectedTo);
      expect(connected()).toEqual([[], []]);

      fixture.componentRef.setInput('canControl', true);
      await fixture.whenStable();
      expect(connected()).toEqual([['board-list-drop-notready'], ['board-list-drop-ready']]);
    });
  });

  it('stands skeleton rows in for the rows while the chunk read loads', async () => {
    const el = await render(groups({}), false);
    fixture.componentRef.setInput('skeletonRows', 3);
    await fixture.whenStable();

    expect(el.querySelectorAll('[data-testid="list-row-skeleton"]')).toHaveLength(3);
  });

  it('says the list is empty only once the chunk read has answered', async () => {
    const unloaded = await render(groups({}), false);
    expect(unloaded.querySelector('[data-testid="list-empty"]')).toBeNull();

    fixture.componentRef.setInput('loaded', true);
    await fixture.whenStable();
    expect(unloaded.querySelector('[data-testid="list-empty"]')?.textContent?.trim()).toBe('Nothing here');
  });

  describe('a row', () => {
    const titles = (el: HTMLElement) =>
      [...el.querySelectorAll('[data-testid="list-row-title"]')].map((t) => t.textContent?.trim());

    it('closes a finished row with when it completed, leaving the cost column to the cost', async () => {
      const el = await render(groups({ done: [entry('done', card('a', { status: 'done', terminal: true, completedAt: '2026-07-13T00:00:01+00:00', costUsd: 1.5 }))] }));

      const finished = el.querySelector('[data-testid="list-row-finished"]')!;
      expect(finished.closest('.work')).not.toBeNull();
      expect(finished.textContent).toContain('Completed');
      expect(finished.querySelector('app-lane-icon')?.getAttribute('data-glyph')).toBe('done');
      expect(el.querySelector('.cost fleet-when')).toBeNull();
    });

    it('heads its work with the chunk id, then each work ref, as plain text, over its title', async () => {
      const el = await render(
        running(card('a', { workLines: [{ label: 'hub#1', title: 'Migrate the event log', url: 'https://example.test/1' }] })),
      );

      const work = el.querySelector('.work')!;
      expect(work.firstElementChild?.textContent?.trim()).toBe('C-a');
      const ref = el.querySelector('[data-testid="list-row-ref"]')!;
      expect(ref.textContent?.trim()).toBe('hub#1');
      expect(ref.tagName).toBe('SPAN');
      expect(titles(el)).toEqual(['Migrate the event log']);
    });

    it('holds a skeleton under its work ref while its titles are unread', async () => {
      const el = await render(running(card('a', { itemsRead: false })));

      expect(titles(el)).toEqual([]);
      expect(el.querySelector('[data-testid="list-row-ref"]')?.textContent?.trim()).toBe('hub#1');
      expect(el.querySelector('[data-testid="list-row-title-skeleton"]')).not.toBeNull();
    });

    it('puts its runner over its step and its PRs in one cell', async () => {
      const el = await render(running(card('a', { runnerId: RUNNER_ID, runnerName: 'r-claude', openPrs: [{ repo: 'blizzard', number: 7, url: 'https://example.test/pr/7' }] })));

      const holder = el.querySelector('.holder')!;
      expect(holder.firstElementChild?.textContent?.trim()).toBe('R-E4AX.r-claude');
      expect(holder.querySelector('.step')).not.toBeNull();
      expect(holder.querySelector('.prs a')?.textContent?.trim()).toBe('#7');
    });

    it('names its runner host/runner where the host is known, and no runner where none holds it', async () => {
      const hosted = await render(running(card('a', { runnerId: RUNNER_ID, runnerName: 'r-claude', hostName: 'atlas' })));
      const runner = hosted.querySelector('[data-testid="list-row-runner"]');
      expect(runner?.textContent?.trim()).toBe('atlas/r-claude');
      expect(runner?.getAttribute('title')).toBe(`atlas/r-claude · ${RUNNER_ID}`);

      fixture.componentRef.setInput('groups', running(card('b', { runnerId: null, runnerName: null, hostName: 'atlas' })));
      await fixture.whenStable();
      expect(hosted.querySelector('[data-testid="list-row-runner"]')).toBeNull();
    });

    it("names an unnamed runner by its compact id", async () => {
      const el = await render(running(card('a', { runnerId: RUNNER_ID, runnerName: null })));
      expect(el.querySelector('[data-testid="list-row-runner"]')?.textContent?.trim()).toBe('R-E4AX');
    });

    it('shows each landed commit by its short hash, linked where it has a url', async () => {
      const hash = '9f2c4e1a7b3d5860fedcba9876543210abcdef12';
      const el = await render(
        running(
          card('a', {
            landedRepos: [
              { repo: 'blizzard', commit_hash: hash, url: 'https://example.test/c/1' },
              { repo: 'infra', commit_hash: hash, url: null },
            ],
          }),
        ),
      );
      expect(el.querySelector('.prs a')?.textContent?.trim()).toBe('9f2c4e1');
      expect(el.querySelector('.prs .commit')?.textContent?.trim()).toBe('9f2c4e1');
    });

    it("tints the row by its project's color, and leaves a project-less row untinted", async () => {
      const tinted = await render(running(card('a', { projectColor: 'teal' })));
      const row = tinted.querySelector<HTMLElement>('[data-testid="list-row"]')!;
      expect(row.classList.contains('tinted')).toBe(true);
      expect(row.style.getPropertyValue('--project-tint')).toBe('var(--project-teal, var(--label-dim))');

      fixture.componentRef.setInput('groups', running(card('b', { projectColor: null })));
      await fixture.whenStable();
      expect(tinted.querySelector('[data-testid="list-row"]')?.classList.contains('tinted')).toBe(false);
    });

    it('folds a large group, and opens it without selecting the row', async () => {
      const el = await render(running(card('a', { workLines: fourItems })));
      expect(titles(el)).toEqual(['1', '2']);

      el.querySelector<HTMLElement>('[data-testid="list-row-fold"]')!.click();
      await fixture.whenStable();

      expect(titles(el)).toEqual(['1', '2', '3', '4']);
      expect(selected).toEqual([]);
    });

    it('opens its chunk from a click on its lane icon, which keeps the pointer for its tooltip', async () => {
      const el = await render(running(card('a')));

      el.querySelector<HTMLElement>('[data-testid="list-row-lane"]')!.click();

      expect(selected).toEqual(['ch_01a']);
    });

    it('opens its chunk through its own open control, named for the chunk', async () => {
      const el = await render(running(card('a')));
      const open = el.querySelector<HTMLButtonElement>('[data-testid="list-row-open"]')!;

      expect(open.tagName).toBe('BUTTON');
      expect(open.getAttribute('aria-label')).toBe('Open chunk C-a');
      open.click();

      expect(selected).toEqual(['ch_01a']);
    });

    it('keeps its PR links and fold outside the open control, each its own target', async () => {
      const el = await render(
        running(card('a', { workLines: fourItems, openPrs: [{ repo: 'blizzard', number: 7, url: 'https://example.test/pr/7' }] })),
      );
      const open = el.querySelector('[data-testid="list-row-open"]')!;
      const link = el.querySelector<HTMLAnchorElement>('[data-testid="list-row"] .prs a')!;
      link.addEventListener('click', (event) => event.preventDefault());

      link.click();

      expect(link.textContent?.trim()).toBe('#7');
      expect(open.contains(link)).toBe(false);
      expect(open.contains(el.querySelector('[data-testid="list-row-fold"]'))).toBe(false);
      expect(selected).toEqual([]);
    });
  });
});
