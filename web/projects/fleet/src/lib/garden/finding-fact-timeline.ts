import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { FindingFactView } from '../api/hub';
import { KitAsyncState } from '../kit/kit-async-state';
import { deriveFactTimelineRows, type FindingFactRow } from './finding-fact-timeline-rows';

/**
 * A finding's whole fact chain (blizzard#487, phase 1 of 2), rendered oldest-first —
 * the append-only record `finding-panel.ts`'s own record/summary/note blocks read
 * only the newest of. Presentational and much simpler than `chunk-timeline.ts`, the
 * pair's own model: no join keys, no activation, no per-row usage figures, just an
 * ordered read-only list. Row derivation lives in `finding-fact-timeline-rows.ts`
 * (`canon:one-owner`) — this component only renders it.
 *
 * Carries no heading of its own — `finding-panel.html` supplies one around it,
 * `chunk-timeline.ts`'s own `heading` input simplified away: nothing here needs a
 * consumer that would rather this component's heading stayed off.
 *
 * The empty state (no facts at all) is a defensive rest state, not a designed-for
 * path — a finding always carries at least an `add` fact in practice — so it stays
 * a bare `fleet-kit-async-state`, not its own designed empty-state copy.
 */
@Component({
  selector: 'fleet-finding-fact-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  templateUrl: './finding-fact-timeline.html',
  styleUrl: './finding-fact-timeline.css',
})
export class FleetFindingFactTimeline {
  readonly facts = input.required<readonly FindingFactView[]>();

  protected readonly rows = computed<readonly FindingFactRow[]>(() => deriveFactTimelineRows(this.facts()));
}
