import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import { gardenFindingFact } from '../core/finding.builders';
import { FleetFindingPanel, type FindingPanelVm } from './finding-panel';

/**
 * The finding panel's Summary and Note (the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method) — a real,
 * headless-Chromium proof that each renders as a `fleet-kit-prose-block` in its
 * `markdown` format: the rail and tick sit beside the block, the Markdown paragraph
 * flows as normal prose rather than `pre-wrap` text, the Summary label reads like
 * the Timeline `h4` below it, and nothing overflows horizontally. jsdom computes no
 * inherited style and lays nothing out, so none of these hold there. Gardening sits
 * in the hub's mobile bottom tab bar, so the narrow widths are load-bearing
 * (`bzh:narrow-viewport-tier-rule`).
 *
 * Proven able to fail: nesting the `fleet-kit-markdown` inside the `.tx` element in
 * `kit-prose-block.html` fails the `white-space` check (the paragraph inherits
 * `pre-wrap`); restoring it passes again
 * (`blizzard-context:/verification/blizzard/commands/web/shell-sweep.md`).
 */
const SUMMARY =
  '## Context\n\nThe docstring of `parse_window` still narrates the removed `reset_at` parameter.\n\n- drop the stale sentence\n- name the new clock';
const NOTE = 'Fixed in the same pass as the **gauge** rework.\n\nNo follow-up needed.';

const VM: FindingPanelVm = {
  findingId: 'fin_01M1KANH0RZEABSD44RCEH6G9B',
  findingClass: 'stale-docstring',
  locus: 'src/a.py:1',
  state: 'live',
  exit: null,
  observedCount: 3,
  introducedRev: '4ba7ef06d',
  introducedAt: null,
  firstObservedAt: '2026-01-01T00:00:00Z',
  lastSeenAt: '2026-01-05T00:00:00Z',
  summary: SUMMARY,
  note: NOTE,
  facts: [gardenFindingFact({ recordedAt: '2026-01-01T00:00:00Z' })],
  workItem: null,
  source: 'routine',
  severity: null,
  raisedByChunkId: null,
};

async function render() {
  await TestBed.configureTestingModule({
    imports: [FleetFindingPanel],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetFindingPanel);
  fixture.componentRef.setInput('vm', VM);
  fixture.componentRef.setInput('state', 'ready');
  await fixture.whenStable();
  return fixture;
}

describe('finding panel prose shell sweep (web:shell-sweep)', () => {
  it.each([1280, 390, 320])('renders Summary and Note as Markdown prose blocks at %ipx', async (width) => {
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      for (const testid of ['fp-summary', 'fp-note']) {
        const block = root.querySelector(`[data-testid="${testid}"]`) as HTMLElement;
        expect(block, `${width}px: ${testid} missing`).not.toBeNull();

        // The rail and tick sit beside the body, left of it, spanning its height.
        const rail = (block.querySelector('.rail') as HTMLElement).getBoundingClientRect();
        const tick = (block.querySelector('.rail .tick') as HTMLElement).getBoundingClientRect();
        const body = (block.querySelector('.body') as HTMLElement).getBoundingClientRect();
        expect(tick.width, `${width}px: ${testid} tick not drawn`).toBeGreaterThan(0);
        expect(rail.right, `${width}px: ${testid} rail not beside the body`).toBeLessThanOrEqual(body.left);
        expect(rail.height, `${width}px: ${testid} rail shorter than the body`).toBeGreaterThanOrEqual(body.height - 1);

        const paragraph = block.querySelector('fleet-kit-markdown p') as HTMLElement;
        expect(paragraph, `${width}px: ${testid} has no Markdown paragraph`).not.toBeNull();
        expect(getComputedStyle(paragraph).whiteSpace, `${width}px: ${testid} paragraph inherits pre-wrap`).toBe(
          'normal',
        );
      }

      const label = getComputedStyle(root.querySelector('[data-testid="fp-summary"] .who') as HTMLElement);
      const timeline = getComputedStyle(root.querySelector('.fp-timeline h4') as HTMLElement);
      for (const prop of ['textTransform', 'letterSpacing', 'fontSize'] as const) {
        expect(label[prop], `${width}px: Summary label and Timeline h4 differ in ${prop}`).toBe(timeline[prop]);
      }

      expect(
        root.scrollWidth,
        `${width}px: host overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
      ).toBeLessThanOrEqual(root.clientWidth);
    } finally {
      root.remove();
    }
  });
});
