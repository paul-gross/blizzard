import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { type ChunkStatus, finishedWord, LANES, STATUS_TONE, toneColor, UNKNOWN_LANE } from 'fleet';

/** The drawings the icon carries: one per board lane, plus the two statuses that read
 * differently from the lane they share — `paused` beside WAIT/HUMAN's waits, `stopped`
 * beside DONE's finishes. */
export type LaneIconGlyph = 'notready' | 'ready' | 'waiting' | 'paused' | 'needs' | 'running' | 'done' | 'stopped';

/** The drawing each lane key has — a lane added to `LANES` without one here falls back below. */
const LANE_GLYPHS: Readonly<Record<string, LaneIconGlyph>> = {
  notready: 'notready',
  ready: 'ready',
  waiting: 'waiting',
  needs: 'needs',
  running: 'running',
  done: 'done',
};

/** The drawing for a chunk in `lane` with `status`: the status's own where it has one, else
 * the lane's. An unknown lane falls back to BACKLOG's inert stack rather than drawing nothing. */
export function laneIconGlyph(lane: string, status: ChunkStatus | null): LaneIconGlyph {
  if (status === 'paused' || status === 'stopped') return status;
  return LANE_GLYPHS[lane] ?? 'notready';
}

/**
 * A chunk's board lane drawn as a 16px inline-SVG icon, for a row too dense to spell the lane
 * out: an inert stack for BACKLOG, a queued play for READY, an hourglass for a wait, a person
 * flagged for NEEDS HUMAN, a turning arc for RUNNING, a check for DONE. It takes its color from
 * the chunk's status tone (`STATUS_TONE` through `toneColor`, `bzh:frontend-formatters`), so the
 * shape names the lane and the color its status; the running arc honors `prefers-reduced-motion`.
 *
 * Sizes to `--lane-icon-size` (16px by default). Its tooltip and accessible name are the lane's
 * heading, or the status word where the status picks the drawing — the raw status in the unknown
 * lane, which draws BACKLOG's inert stack.
 *
 * Presentational, input-only.
 */
@Component({
  selector: 'app-lane-icon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './lane-icon.html',
  styleUrl: './lane-icon.css',
  host: { '[attr.data-glyph]': 'glyph()', '[attr.title]': 'label()', '[style.color]': 'color()' },
})
export class LaneIcon {
  /** The board lane key (`chunkLane`'s answer). */
  readonly lane = input.required<string>();

  /** The chunk's status — it picks a finer drawing than the lane's where it has one, and the
   * icon's color. */
  readonly status = input.required<ChunkStatus>();

  protected readonly glyph = computed(() => laneIconGlyph(this.lane(), this.status()));

  protected readonly color = computed(() => toneColor(STATUS_TONE(this.status())));

  protected readonly label = computed(() => {
    if (this.lane() === UNKNOWN_LANE) return this.status();
    const glyph = this.glyph();
    if (glyph === 'paused') return 'Paused';
    if (glyph === 'stopped') return finishedWord('stopped');
    return LANES.find((l) => l.key === glyph)?.headerLabel ?? glyph;
  });
}
