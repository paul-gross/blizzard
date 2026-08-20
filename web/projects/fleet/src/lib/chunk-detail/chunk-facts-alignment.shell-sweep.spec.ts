import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import type { ChunkDetail } from '../api/hub';
import { ChunkFacts } from './chunk-facts';
import { ChunkTokenBreakdown } from './chunk-token-breakdown';

/**
 * The two-table alignment proof behind `--kv-label-col`/`--chunk-facts-pad`
 * (`chunk-facts.css`, set on {@link ChunkFacts}'s `:host`) — a real-Chromium
 * geometry check jsdom cannot make: it never actually lays out a CSS grid, so
 * `web:unit-test` cannot see the facts table's value column drift from the
 * usage table's. Follows `design/hover-tint.shell-sweep.spec.ts`'s own pattern
 * (real layout claim, `getBoundingClientRect`) rather than a computed-style one.
 *
 * Mounts the two tables exactly as `chunk-detail-panel.html` composes them —
 * {@link ChunkTokenBreakdown} content-projected into {@link ChunkFacts}'s
 * `[token-breakdown]` slot as a sibling `<dl class="kv">`, not nested inside
 * the first — since that sibling relationship, not either component alone, is
 * what the shared custom properties are proving stays aligned. The fixture
 * carries a long runner identity that wraps the facts table's Runner value
 * across more than one line: the failure mode a fixed `74px`/`8px 6px` literal
 * (in place of `var(--kv-label-col)`/`var(--chunk-facts-pad)`) would not show
 * on a short value, since both tables still happen to compute the same
 * default — only differing *content* widths expose independently-sized grid
 * tracks.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
@Component({
  selector: 'fleet-chunk-facts-alignment-host',
  imports: [ChunkFacts, ChunkTokenBreakdown],
  template: `
    <fleet-chunk-detail-facts [detail]="detail">
      <fleet-chunk-detail-token-breakdown token-breakdown [detail]="detail" />
    </fleet-chunk-detail-facts>
  `,
})
class AlignmentHost {
  readonly detail: ChunkDetail = DETAIL;
}

const DETAIL: ChunkDetail = {
  chunk_id: 'ch_01alignment0000000000000000',
  graph_id: 'gr_1',
  graph_name: 'default',
  status: 'done',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 3,
  work_refs: [],
  history: [],
  artifacts: [],
  // A genuinely long identity — long enough to wrap across several lines at
  // phone width, the content-width stress this guard exists to catch.
  route: {
    runner_id: 'runner-a-genuinely-long-identity-string-that-wraps-onto-several-lines-0001',
    workspace_id: 'ws_01',
    environment_ids: ['env_01'],
  },
  cost: {
    input_tokens: 128_000,
    output_tokens: 4_096,
    cache_read_tokens: 900_000,
    cache_create_tokens: 12_000,
    cost_usd: 12.3456,
    cost_partial: false,
  },
};

describe('chunk-facts / chunk-token-breakdown value-column alignment shell sweep (web:shell-sweep)', () => {
  it("keeps the usage table's value column at the facts table's own horizontal position, even under a long runner identity that wraps", async () => {
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [AlignmentHost],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(AlignmentHost);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await page.viewport(360, 500);

    try {
      const factsValue = root.querySelector<HTMLElement>('[data-testid="fact-runner"]')!;
      const usageValue = root.querySelector<HTMLElement>('[data-testid="fact-tokens-input"]')!;
      expect(factsValue.getBoundingClientRect().height, 'fixture defect: the long runner identity did not wrap').toBeGreaterThan(
        20,
      );

      const factsLeft = factsValue.getBoundingClientRect().left;
      const usageLeft = usageValue.getBoundingClientRect().left;
      expect(
        usageLeft,
        `the usage table's value column (${usageLeft}) drifted from the facts table's own (${factsLeft}) — the two tables no longer share --kv-label-col/--chunk-facts-pad`,
      ).toBeCloseTo(factsLeft, 0);
    } finally {
      root.remove();
    }
  });
});
