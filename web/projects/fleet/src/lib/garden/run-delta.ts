import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ChunkStatus } from '../api/hub';
import { STATUS_TONE } from '../chunk-lanes';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitBadge } from '../kit/kit-badge';

/** One `add` op a delivered set's artifact published (`hub run show`'s own `+` line) —
 * `findingClass` rather than the wire's `class` (`AddedFindingView.class`), so a
 * template never confuses it with the DOM `class` attribute. */
export interface RunDeltaAddedFindingVm {
  readonly findingId: string | null;
  readonly findingClass: string;
  readonly locus: string;
  readonly summary: string;
  readonly introduced: string | null;
}

/** One `gone` op a delivered set's artifact published (`hub run show`'s own `-` line). */
export interface RunDeltaGoneFindingVm {
  readonly findingId: string;
  readonly note: string;
}

/** One delivered set's own published delta — added, observed, and gone kept as three
 * distinct groups (D4), never merged with another set's. */
export interface RunDeltaSetVm {
  readonly findingSetId: string;
  readonly revisionsLabel: string;
  readonly measurement: string | null;
  readonly added: readonly RunDeltaAddedFindingVm[];
  readonly observed: readonly string[];
  readonly gone: readonly RunDeltaGoneFindingVm[];
}

/** The escalated run's open escalation — `null` on any other outcome. */
export interface RunDeltaEscalationVm {
  readonly nodeName: string | null;
  readonly takeoverCommand: string;
  readonly wrappedTakeoverCommand: string;
}

/** One run's own delta view model (`hub run show`'s own read) — plain data, no query
 * or wire type, so this presentational component and its spec never see one. */
export interface RunDeltaVm {
  readonly chunkId: string;
  readonly routineName: string;
  readonly scopeSlug: string;
  readonly mode: string;
  readonly outcome: ChunkStatus;
  readonly escalation: RunDeltaEscalationVm | null;
  readonly sets: readonly RunDeltaSetVm[];
}

/**
 * The gardening runs-and-findings tab's run delta (blizzard#401 Phase 3) —
 * presentational only, no query injection: one run's added/observed/gone, grouped
 * three ways and captioned as a **delta of what changed**, not a current-state
 * snapshot (the plan's own explicit acceptance criterion — stated in the rendered
 * text, not left implicit in the grouping). Several delivered sets (D4) each keep
 * their own added/observed/gone block — two sets' groups are never merged into one.
 */
@Component({
  selector: 'fleet-run-delta',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, RouterLink],
  templateUrl: './run-delta.html',
  styleUrl: './run-delta.css',
})
export class FleetRunDelta {
  readonly vm = input<RunDeltaVm | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  protected readonly STATUS_TONE = STATUS_TONE;
}
