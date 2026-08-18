import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page, userEvent } from 'vitest/browser';

import type { ChunkDetail } from '../api/hub';
import type { BoardCard } from '../board-card/board-card';
import { BoardCardComponent } from '../board-card/board-card';
import { ChunkArtifacts } from '../chunk-detail/chunk-artifacts';
import { ChunkTimeline } from '../chunk-detail/chunk-timeline';

/**
 * The shared hover-tint proof behind `--tint-hover`/`--tint-selected`
 * (`blizzard-context:/verification/blizzard.md` `bzh:web-shell-sweep`) — a real-Chromium
 * proof of a computed-style claim jsdom cannot make: jsdom parses a `:hover` rule
 * without ever evaluating it, so `web:unit-test` cannot see a hovered row distinguished
 * from a resting or a selected one. Each case below drives a real pointer
 * (`userEvent.hover`, backed by Playwright) rather than dispatching a synthetic
 * `mouseenter`, since only a real pointer state changes which CSS actually matches, and
 * waits a frame after every hover/unhover — a real browser's style recalculation is not
 * guaranteed synchronous with the pointer move settling.
 *
 * Excluded from the default `ng test` run the same way every other `*.shell-sweep.spec.ts`
 * is — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
async function nextFrame(): Promise<void> {
  await new Promise((resolve) => requestAnimationFrame(resolve));
}

/**
 * The design tokens are a global stylesheet (`design/tokens.css`'s own doc comment),
 * loaded via each app's build `styles` — never by a standalone component test, and a
 * plain module import of a `.css` file does not reach the document either under this
 * builder (checked: it lands as an unreferenced lazy chunk). This spec's whole claim is
 * about resolved `var(--tint-hover)`/`var(--tint-selected)` color, so it reads the
 * sheet's real text server-side (`commands.readFile`, the vitest browser command this
 * builder exposes for exactly this) and injects it as a `<style>` element itself.
 */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

async function renderCard(card: BoardCard, selected: boolean): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [BoardCardComponent],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', card);
  fixture.componentRef.setInput('selected', selected);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(390, 400);
  await nextFrame();
  // The real pointer position persists across cases in this file — without this, a row
  // that happens to render under wherever a previous case last left the cursor reads as
  // already hovered before this case's own `userEvent.hover` ever runs.
  await userEvent.unhover(root);
  await nextFrame();
  return root;
}

const CARD: BoardCard = {
  chunkId: 'ch_01hover0000000000000000000',
  shortId: 'C-HOVR',
  status: 'running',
  node: 'build',
  nodeId: 'nd_build',
  pointerLabels: [],
  costUsd: 0,
  costPartial: false,
  completedAt: null,
};

// Populated by the first case, read by the second — see the describe block below.
let boardCardResting = '';
let boardCardHovered = '';

describe('board-card hover/selection tint shell sweep (web:shell-sweep)', () => {
  it('washes a hovered card', async () => {
    await loadDesignTokens();
    const root = await renderCard(CARD, false);
    try {
      const card = root.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
      boardCardResting = getComputedStyle(card).backgroundColor;
      await userEvent.hover(card);
      await nextFrame();
      boardCardHovered = getComputedStyle(card).backgroundColor;
      expect(boardCardHovered, `hover produced no background change from resting (${boardCardResting})`).not.toBe(
        boardCardResting,
      );
    } finally {
      root.remove();
    }
  });

  it('reads a selected-but-unhovered card as distinct from both a resting and a hovered one', async () => {
    await loadDesignTokens();
    const root = await renderCard(CARD, true);
    try {
      const card = root.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
      const selected = getComputedStyle(card).backgroundColor;
      expect(
        selected,
        `a selected card (${selected}) reads identical to a hovered-but-unselected one (${boardCardHovered}) — the two states are not distinguishable`,
      ).not.toBe(boardCardHovered);
      expect(selected, `a selected card (${selected}) reads identical to a resting one (${boardCardResting})`).not.toBe(
        boardCardResting,
      );
    } finally {
      root.remove();
    }
  });
});

const TIMELINE_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01hover0000000000000000000',
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 2,
  status: 'running',
  work_refs: [],
  history: [
    {
      choice_name: 'pass',
      epoch: 1,
      from_node_id: 'nd_build',
      from_node_name: 'build',
      to_node_id: 'nd_review',
      to_node_name: 'review',
      recorded_at: '2026-08-09T00:00:00.000Z',
    },
  ],
  artifacts: [],
};

describe('chunk-timeline row hover tint shell sweep (web:shell-sweep)', () => {
  it('washes a hovered history row', async () => {
    await loadDesignTokens();
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [ChunkTimeline],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', TIMELINE_DETAIL);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await page.viewport(390, 400);
    await nextFrame();
    await userEvent.unhover(root);
    await nextFrame();

    try {
      const row = root.querySelector<HTMLElement>('[data-testid="history-step"]')!;
      const resting = getComputedStyle(row).backgroundColor;
      await userEvent.hover(row);
      await nextFrame();
      const hovered = getComputedStyle(row).backgroundColor;
      expect(hovered, `hover produced no background change from resting (${resting})`).not.toBe(resting);
    } finally {
      root.remove();
    }
  });
});

const ARTIFACTS_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01hover0000000000000000000',
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 1,
  status: 'running',
  work_refs: [],
  history: [],
  artifacts: [
    {
      key: 'build.plan.1',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 1,
      name: 'plan',
      kind: 'asset',
      content: 'plan content',
      recorded_at: '2026-08-09T00:00:00.000Z',
    },
    {
      key: 'build.commit.1',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 1,
      name: 'commit',
      kind: 'git_commit',
      content: null,
      recorded_at: '2026-08-09T00:00:01.000Z',
    },
  ],
};

describe('chunk-artifacts row hover tint shell sweep (web:shell-sweep)', () => {
  it('washes the bordered row of a hovered artifact link, but not a contentless .artifact-plain row', async () => {
    await loadDesignTokens();
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [ChunkArtifacts],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', ARTIFACTS_DETAIL);
    // `.artifact-plain` (a row with nothing an expand would reveal) only renders in
    // `expandable` mode — the default `link` mode renders every row, plain or not, as an
    // `<a class="artifact-link">` (`chunk-artifacts.ts`'s own template).
    fixture.componentRef.setInput('expandable', true);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await page.viewport(390, 400);
    await nextFrame();
    await userEvent.unhover(root);
    await nextFrame();

    try {
      const rows = root.querySelectorAll<HTMLElement>('[data-testid="artifact"]');
      const linkRow = rows[0];
      const plainRow = rows[1];
      const link = linkRow.querySelector<HTMLElement>('[data-testid="artifact-link"]')!;
      const plain = plainRow.querySelector<HTMLElement>('[data-testid="artifact-plain"]')!;
      expect(linkRow.dataset['kind'], 'fixture defect: first row is not the asset-with-content artifact').toBe(
        'asset',
      );
      expect(plainRow.dataset['kind'], 'fixture defect: second row is not the git_commit artifact').toBe(
        'git_commit',
      );

      const linkResting = getComputedStyle(linkRow).backgroundColor;
      await userEvent.hover(link);
      await nextFrame();
      const linkHovered = getComputedStyle(linkRow).backgroundColor;
      expect(linkHovered, `hovering the artifact link produced no wash on its row (${linkResting})`).not.toBe(
        linkResting,
      );
      await userEvent.unhover(link);
      await nextFrame();

      const plainResting = getComputedStyle(plainRow).backgroundColor;
      await userEvent.hover(plain);
      await nextFrame();
      const plainHovered = getComputedStyle(plainRow).backgroundColor;
      expect(
        plainHovered,
        `a contentless .artifact-plain row washed on hover (${plainResting} -> ${plainHovered}) — it carries no control and must not`,
      ).toBe(plainResting);
    } finally {
      root.remove();
    }
  });
});
