import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { type FindingFactView, KitAsyncState } from 'fleet';
import { deriveFactTimelineRows, type FindingFactRow } from './finding-fact-timeline-rows';

/**
 * A finding's whole fact chain, rendered oldest-first —
 * presentational, just an ordered read-only list. Row derivation lives in `finding-fact-timeline-rows.ts`
 * (`canon:one-owner`) — this component only renders it.
 *
 * Carries no heading of its own — a consumer supplies one around it.
 *
 * The empty state (no facts at all) is a defensive rest state, not a designed-for
 * path — a finding always carries at least an `add` fact in practice — so it stays
 * a bare `fleet-kit-async-state`, not its own designed empty-state copy.
 */
@Component({
  selector: 'app-finding-fact-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  templateUrl: './finding-fact-timeline.html',
  styleUrl: './finding-fact-timeline.css',
})
export class FleetFindingFactTimeline {
  readonly facts = input.required<readonly FindingFactView[]>();

  protected readonly rows = computed<readonly FindingFactRow[]>(() => deriveFactTimelineRows(this.facts()));
}
