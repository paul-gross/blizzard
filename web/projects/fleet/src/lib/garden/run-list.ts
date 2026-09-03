import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { ChunkStatus } from '../api/hub';
import { STATUS_TONE } from '../chunk-lanes';
import { compactRef } from '../compact-ref';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitBadge } from '../kit/kit-badge';
import { KitSelectRow } from '../kit/kit-select-row';
import { FleetWhen } from '../when-display';

/** A run's finding counts, summed across every set it delivered — `null` when it
 * delivered none, so the row renders no triple rather than a misleading `+0`. */
export interface RunListCountsVm {
  readonly added: number;
  readonly observed: number;
  readonly gone: number;
}

/** One coloured, optionally-suppressed leg of a row's rendered counts triple. */
interface CountLeg {
  readonly text: string;
  readonly cssClass: string;
}

/** One row of the run list — three fixed lines: routine/scope + outcome, mode +
 * chunk ref, minted time + counts. `escalated` folds `RunRowView.escalation`
 * non-`null` once here so the row's own tint never disagrees with what counts as
 * escalated; `outcome` is `RunRowView.outcome` verbatim, and is what makes a
 * failed, running or escalated run legible without relying on that tint. */
export interface RunListRowVm {
  readonly chunkId: string;
  readonly routineName: string;
  readonly scopeSlug: string;
  readonly mode: string;
  readonly mintedAt: string;
  readonly outcome: ChunkStatus;
  readonly escalated: boolean;
  readonly counts: RunListCountsVm | null;
}

/**
 * The gardening runs tab's run list — presentational only, no query injection:
 * renders the rows it is handed. An escalated row (`escalated`) tints its own body
 * red; the run-list shell sweep (`garden-runs.shell-sweep.spec.ts`) proves that
 * tint is a real computed style, not just a class name.
 *
 * The tint is never the only carrier. Every row states its `outcome` as badge text
 * on the headline, so a run that failed, is still running, or needs a human is
 * distinguishable from a clean one by reading it — the tint is emphasis on top of a
 * word, not a substitute for one, and a reader who cannot resolve the colour loses
 * nothing but the emphasis.
 *
 * Built on `fleet-kit-select-row`, so the row's left edge is reserved for
 * *selection* unconditionally — an escalated row's tint rides the projected content
 * (`.rl-body--escalated`), which paints on top of the kit row's own resting/hover/
 * selected background rather than replacing it. That is deliberate: an escalated row
 * that is also selected still needs to read as both, and kit-select-row owns no seam
 * for a second background a consumer could otherwise layer in.
 *
 * Emits `runPick`, `routine-list.ts`'s own `routinePick` naming (`@angular-eslint/no-
 * output-native` forbids an output named `select`); the container turns the picked
 * chunk id into a route navigation.
 */
@Component({
  selector: 'fleet-run-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitSelectRow, FleetWhen],
  templateUrl: './run-list.html',
  styleUrl: './run-list.css',
})
export class FleetRunList {
  protected readonly STATUS_TONE = STATUS_TONE;

  readonly rows = input.required<readonly RunListRowVm[]>();
  readonly selectedChunkId = input<string | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  readonly runPick = output<string>();

  protected readonly compactRef = compactRef;

  protected pick(chunkId: string): void {
    this.runPick.emit(chunkId);
  }

  protected modeLabel(mode: string): string {
    return mode.length === 0 ? mode : mode[0].toUpperCase() + mode.slice(1);
  }

  /**
   * The row's counts triple, one leg per non-suppressed component, joined by
   * `" / "` legs of their own (kept as plain-text legs rather than CSS so a
   * jsdom-level test can assert the exact rendered string). Added always
   * renders — `+0` included, coloured green rather than the usual red since no
   * new findings is good news. Observed and gone each drop out entirely at zero.
   */
  protected countLegs(counts: RunListCountsVm): readonly CountLeg[] {
    const legs: CountLeg[] = [
      {
        text: `+${counts.added}`,
        cssClass: counts.added === 0 ? 'rl-count-added rl-count-added--zero' : 'rl-count-added',
      },
    ];
    if (counts.observed > 0) {
      legs.push({ text: ' / ', cssClass: 'rl-count-sep' });
      legs.push({ text: `${counts.observed}`, cssClass: 'rl-count-observed' });
    }
    if (counts.gone > 0) {
      legs.push({ text: ' / ', cssClass: 'rl-count-sep' });
      legs.push({ text: `-${counts.gone}`, cssClass: 'rl-count-gone' });
    }
    return legs;
  }
}
