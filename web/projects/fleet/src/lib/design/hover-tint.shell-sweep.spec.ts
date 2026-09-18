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

/** Parses a `getComputedStyle` color (always `rgb()`/`rgba()`, regardless of how the
 * source CSS specified it) into its channel values. */
function rgbChannels(color: string): readonly [number, number, number] {
  const match = /rgba?\(([^)]+)\)/.exec(color);
  if (!match) throw new Error(`not an rgb() color: ${color}`);
  const [r, g, b] = match[1].split(',').map((c) => Number(c.trim()));
  return [r, g, b];
}

/** The summed per-channel delta between two computed colors — a cheap stand-in for "does
 * this read as a genuinely different shade," not just "is the string different." A bare
 * `.not.toBe()` on two colors passes on a one-unit rounding difference that a human eye
 * never sees, which is exactly how a board card's resting→hover step measured
 * imperceptible on the live board despite this suite being green throughout. */
function channelDelta(a: string, b: string): number {
  const [ar, ag, ab] = rgbChannels(a);
  const [br, bg, bb] = rgbChannels(b);
  return Math.abs(ar - br) + Math.abs(ag - bg) + Math.abs(ab - bb);
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
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
};

describe('board-card hover/selection tint shell sweep (web:shell-sweep)', () => {
  // One case, not two: the resting/hovered readings a "selected" comparison needs were
  // previously module-level state set by a sibling `it` and read by this one — isolate
  // either case (`-t`, `.only`, reordering) and the second silently compared a real color
  // against `''`, passing vacuously (`review:F9`). Self-contained here instead.
  it('washes a hovered card with a genuinely perceptible step, and reads a selected-but-unhovered card as distinct from both a resting and a hovered one', async () => {
    await loadDesignTokens();

    const restingRoot = await renderCard(CARD, false);
    let boardCardResting: string;
    let boardCardHovered: string;
    try {
      const card = restingRoot.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
      boardCardResting = getComputedStyle(card).backgroundColor;
      const restingBorder = getComputedStyle(card).borderTopColor;
      await userEvent.hover(card);
      await nextFrame();
      boardCardHovered = getComputedStyle(card).backgroundColor;
      const hoveredBorder = getComputedStyle(card).borderTopColor;
      expect(boardCardHovered, `hover produced no background change from resting (${boardCardResting})`).not.toBe(
        boardCardResting,
      );
      // The background step alone measured too subtle to read at a glance on the live
      // board — the top/right/bottom border's own dim-cyan edge is the step that must
      // actually carry "obvious," so it is pinned to a real minimum delta, not just "differs."
      expect(
        channelDelta(hoveredBorder, restingBorder),
        `hover's border (${hoveredBorder}) is too close to resting (${restingBorder}) to read as a distinct edge`,
      ).toBeGreaterThan(80);
    } finally {
      restingRoot.remove();
    }

    const selectedRoot = await renderCard(CARD, true);
    try {
      const card = selectedRoot.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
      const selected = getComputedStyle(card).backgroundColor;
      expect(
        selected,
        `a selected card (${selected}) reads identical to a hovered-but-unselected one (${boardCardHovered}) — the two states are not distinguishable`,
      ).not.toBe(boardCardHovered);
      expect(selected, `a selected card (${selected}) reads identical to a resting one (${boardCardResting})`).not.toBe(
        boardCardResting,
      );
    } finally {
      selectedRoot.remove();
    }
  });

  it('yields the left-edge status color to cyan while selected, and returns it once deselected', async () => {
    await loadDesignTokens();

    const restingRoot = await renderCard(CARD, false);
    let restingLeft: string;
    try {
      const card = restingRoot.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
      restingLeft = getComputedStyle(card).borderLeftColor;
    } finally {
      restingRoot.remove();
    }

    const selectedRoot = await renderCard(CARD, true);
    try {
      const card = selectedRoot.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
      const selectedLeft = getComputedStyle(card).borderLeftColor;
      expect(
        selectedLeft,
        `a selected card's left edge (${selectedLeft}) still reads as the status color (${restingLeft}) instead of yielding to the selection accent`,
      ).not.toBe(restingLeft);
    } finally {
      selectedRoot.remove();
    }

    const restingAgainRoot = await renderCard(CARD, false);
    try {
      const card = restingAgainRoot.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
      expect(
        getComputedStyle(card).borderLeftColor,
        'a deselected card does not return to its own status color',
      ).toBe(restingLeft);
    } finally {
      restingAgainRoot.remove();
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
      providers: [provideZonelessChangeDetection(), provideRouter([])],
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

  it('reads a selected row as distinct from a merely hovered one (blizzard#315)', async () => {
    await loadDesignTokens();
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [ChunkTimeline],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', TIMELINE_DETAIL);
    fixture.componentRef.setInput('activatable', true);
    fixture.componentRef.setInput('selectedKey', 'nd_review:2'); // TIMELINE_DETAIL's own active row.
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await page.viewport(390, 400);
    await nextFrame();
    await userEvent.unhover(root);
    await nextFrame();

    try {
      const historyRow = root.querySelector<HTMLElement>('[data-testid="history-step"]')!;
      const selectedRow = root.querySelector<HTMLElement>('[data-testid="history-active"]')!;
      expect(selectedRow.classList.contains('selected'), 'fixture defect: selectedKey did not select the active row').toBe(
        true,
      );

      await userEvent.hover(historyRow);
      await nextFrame();
      const hovered = getComputedStyle(historyRow).backgroundColor;
      const selected = getComputedStyle(selectedRow).backgroundColor;
      expect(
        selected,
        `a selected row (${selected}) reads identical to a hovered-but-unselected one (${hovered}) — the two states are not distinguishable`,
      ).not.toBe(hovered);
    } finally {
      root.remove();
    }
  });
});

describe('chunk-timeline (Quick Reference) full-bleed row shell sweep (web:shell-sweep)', () => {
  it("reaches its zero-padded ancestor's full width instead of floating inset from it", async () => {
    await loadDesignTokens();
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [ChunkTimeline],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', TIMELINE_DETAIL);
    fixture.componentRef.setInput('activatable', true);
    await fixture.whenStable();
    // A zero-padded wrapper, standing in for `kit-panel.ts`'s own zero-padded
    // `.p-body` — this is the ancestor the General tab's real composition site
    // (`chunk-general-tab.html`) actually wraps this component in.
    const wrapper = document.createElement('div');
    wrapper.style.width = '360px';
    wrapper.style.padding = '0';
    wrapper.appendChild(fixture.nativeElement);
    document.body.appendChild(wrapper);
    await page.viewport(390, 400);
    await nextFrame();

    try {
      const row = wrapper.querySelector<HTMLElement>('[data-testid="history-step"]')!;
      const wrapperRect = wrapper.getBoundingClientRect();
      const rowRect = row.getBoundingClientRect();
      expect(
        rowRect.left,
        `row's left edge (${rowRect.left}) sits inset from its zero-padded ancestor's own left edge (${wrapperRect.left})`,
      ).toBeCloseTo(wrapperRect.left, 0);
      expect(
        rowRect.right,
        `row's right edge (${rowRect.right}) sits inset from its zero-padded ancestor's own right edge (${wrapperRect.right})`,
      ).toBeCloseTo(wrapperRect.right, 0);
    } finally {
      wrapper.remove();
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
  it('washes the bordered row of a hovered artifact link', async () => {
    await loadDesignTokens();
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [ChunkArtifacts],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', ARTIFACTS_DETAIL);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await page.viewport(390, 400);
    await nextFrame();
    await userEvent.unhover(root);
    await nextFrame();

    try {
      const row = root.querySelector<HTMLElement>('[data-testid="artifact"]')!;
      const link = row.querySelector<HTMLElement>('[data-testid="artifact-link"]')!;

      const resting = getComputedStyle(row).backgroundColor;
      await userEvent.hover(link);
      await nextFrame();
      const hovered = getComputedStyle(row).backgroundColor;
      expect(hovered, `hovering the artifact link produced no wash on its row (${resting})`).not.toBe(resting);
    } finally {
      root.remove();
    }
  });
});
